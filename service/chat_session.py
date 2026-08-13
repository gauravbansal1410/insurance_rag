# Turn-based slot-filling for the /chat endpoint (docs/query_architecture.md steps 1-2).
# Deterministic Q&A for v1 (fixed questions, type/enum parsing, no LLM) - kept behind one
# narrow function, fill_next_field(), specifically so a future conversational frontend
# (Ollama/Gemini, per the user's stated intent to extend rather than rewrite) can replace
# just this module's implementation later, without touching main.py's endpoint wiring, the
# session-state shape, or run_query_pipeline.py's actual query execution.
#
# Deliberately NOT built as a formal strategy interface/registry now - that would mean
# guessing at a future implementation's actual shape (e.g. whether it needs full
# conversation history, not just the current field-state dict) before it exists. The seam
# that matters is real (this one function), the framework around it isn't needed yet.

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "query"))
from premium_interpolation import available_terms, available_payment_options_for_term

# concern_tags asked before term/premium_payment_option (not after, as originally ordered) -
# confirmed 2026-08-01 that validating term/payment_option availability against real data
# needs the concern tags known first: a term can look available (right age/sum_assured) but
# only on a policy that doesn't match the user's stated concern (e.g. 877/878's 25-year data
# only serves debt_linked_cover, not income_replacement) - undetectable without concern_tags
# already in hand.
FIELD_ORDER = ["age", "sum_assured", "concern_tags", "term", "premium_payment_option", "budget"]

QUESTIONS = {
    "age": "What's your age?",
    "sum_assured": "How much cover (sum assured) are you looking for, in rupees?",
    "term": "For how many years (policy term)?",
    "premium_payment_option": (
        "How do you want to pay premiums - single (one-time), or regular/limited over "
        "5, 10, or 15 years?"
    ),
    "budget": "What's your yearly budget for premiums, in rupees?",
    "concern_tags": (
        "What are you most concerned about? Reply with one or more numbers:\n"
        "1) Replacing income for my family\n"
        "2) Paying off a loan/debt\n"
        "3) Funding my child's education\n"
        "4) Building retirement income\n"
        "5) Estate/legacy planning\n"
        "6) A disciplined way to save\n"
        "7) Critical illness/medical cover\n"
        "8) Borrowing against the policy"
    ),
}

# Matches query/concern_tags.py's CONCERN_TAG_PHRASES order exactly - the numbered list
# above must stay in sync with this if either ever changes (same class of drift
# check_concern_tags_sync.py guards against for the extraction/precompute side).
CONCERN_TAG_BY_NUMBER = {
    1: "income_replacement", 2: "debt_linked_cover", 3: "child_education_fund",
    4: "retirement_income", 5: "estate_legacy_planning", 6: "forced_savings_discipline",
    7: "medical_critical_illness_addon", 8: "liquidity_via_policy_loan",
}

# Plain-language label per concern tag number, matching QUESTIONS["concern_tags"]'s own
# numbered list text - used by _field_meta() below to give a frontend the same options as
# structured data (for checkboxes) instead of text a user has to read and re-type numbers
# from.
_CONCERN_TAG_LABELS = {
    1: "Replacing income for my family", 2: "Paying off a loan/debt",
    3: "Funding my child's education", 4: "Building retirement income",
    5: "Estate/legacy planning", 6: "A disciplined way to save",
    7: "Critical illness/medical cover", 8: "Borrowing against the policy",
}

# Fixed order for rendering payment-option choices - stable regardless of dict/set
# iteration order. "reply" is the exact text _parse_field()'s premium_payment_option
# mapping (below) expects back - a frontend just echoes this value, it never needs to know
# the internal limited_ppt_N enum naming itself.
_PAYMENT_OPTION_ORDER = ["single", "regular", "limited_ppt_5", "limited_ppt_10", "limited_ppt_15"]
_PAYMENT_OPTION_META = {
    "single": {"label": "Single — pay once, up front", "reply": "single"},
    "regular": {"label": "Regular — pay every year for the full term", "reply": "regular"},
    "limited_ppt_5": {"label": "Limited — pay for 5 years only", "reply": "5"},
    "limited_ppt_10": {"label": "Limited — pay for 10 years only", "reply": "10"},
    "limited_ppt_15": {"label": "Limited — pay for 15 years only", "reply": "15"},
}


def new_session():
    return {"profile": {}, "sum_assured_type": "level"}  # level: not asked in v1, see docs/schema.md


NUMBER_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def _extract_number(text):
    """Pulls the first number out of natural phrasing ("20 years", "10000 per year",
    Indian comma-grouping like "50,00,000") rather than requiring the whole reply to be a
    bare number - confirmed necessary: an earlier version required int()/float() on the
    full stripped string and failed on anything but a bare digit string."""
    match = NUMBER_RE.search(text)
    return match.group(0).replace(",", "") if match else None


# Indian shorthand units for currency amounts (sum_assured, budget) - "cr"/"crore" (1e7),
# "lakh"/"lac"/"l" (1e5), "k"/"thousand" (1e3). The trailing \b matters: without it, "l"
# would match as a false-positive substring prefix of "lakh"/"lac" before the engine gets a
# chance to try the longer alternative - Python's alternation tries left-to-right and takes
# the first successful match at each position, so \b forces backtracking past a
# too-short match ("l" immediately followed by "akh", no boundary) until a longer
# alternative ("lakh") actually satisfies it.
_AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(crore|cr|lakhs?|lacs?|l|k|thousand)?\b", re.I)
_AMOUNT_UNIT_MULTIPLIER = {
    "crore": 1_00_00_000, "cr": 1_00_00_000,
    "lakh": 1_00_000, "lakhs": 1_00_000, "lac": 1_00_000, "lacs": 1_00_000, "l": 1_00_000,
    "k": 1_000, "thousand": 1_000,
}


def _extract_amount(text):
    """Like _extract_number(), but also understands Indian shorthand currency units
    ("1cr", "50L", "50 lakh", "20k") - confirmed necessary 2026-08-13: a user typing "1cr"
    for a Rs. 1,00,00,000 sum assured had it parsed as literally Rs. 1 by the old
    digits-only _extract_number(), which passed silently (no parse error - "1" is a
    perfectly valid number) and only surfaced downstream as a confusing "Available terms:
    (empty)" message once every policy failed eligibility's min_sum_assured bound. Used for
    sum_assured/budget (both real Rupee amounts a user would naturally shorthand); age/term
    stay on the plain _extract_number() - nobody says "31 lakh years old"."""
    match = _AMOUNT_RE.search(text)
    if match is None:
        return None
    value = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "").lower()
    return value * _AMOUNT_UNIT_MULTIPLIER.get(unit, 1)


def _parse_field(field, message):
    """Returns (parsed_value, error_message_or_none) - never raises, so a bad reply
    re-asks the same question rather than crashing the session."""
    text = message.strip().lower()

    if field in ("age", "term"):
        num = _extract_number(text)
        if num is None:
            return None, f"I didn't understand that as a number. {QUESTIONS[field]}"
        return int(float(num)), None

    if field == "sum_assured":
        amount = _extract_amount(text)
        if amount is None:
            return None, f"I didn't understand that as a number. {QUESTIONS[field]}"
        return int(amount), None

    if field == "budget":
        amount = _extract_amount(text)
        if amount is None:
            return None, f"I didn't understand that as a number. {QUESTIONS[field]}"
        return amount, None

    if field == "premium_payment_option":
        mapping = {
            "single": "single", "regular": "regular",
            "5": "limited_ppt_5", "10": "limited_ppt_10", "15": "limited_ppt_15",
        }
        for key, value in mapping.items():
            if key in text:
                return value, None
        return None, f"I didn't recognize that option. {QUESTIONS[field]}"

    if field == "concern_tags":
        numbers = [int(n) for n in re.findall(r"\d+", text)]
        tags = [CONCERN_TAG_BY_NUMBER[n] for n in numbers if n in CONCERN_TAG_BY_NUMBER]
        if not tags:
            return None, f"I didn't recognize any of those numbers. {QUESTIONS[field]}"
        return tags, None

    raise ValueError(f"unknown field: {field}")


def _corpus_bounds(layer2_records):
    """Rough age/sum_assured bounds across every policy's own Group C, for the number-input
    widgets' min/max hints (_field_meta() below) - not an eligibility check itself (that's
    still eligibility_filter.py's job per-candidate), just a sane range so a frontend's
    number input doesn't let someone type an age of 200. Falls back to fixed defaults if no
    layer2_records given (matches this project's actual corpus at the time this was
    written, term_assurance only - docs/schema.md)."""
    if not layer2_records:
        return {"age_min": 18, "age_max": 70, "sum_assured_min": 500_000, "sum_assured_max": None}
    group_cs = [l2["layer2"]["group_c"] for l2 in layer2_records.values()]
    max_sas = [gc["max_sum_assured"] for gc in group_cs if gc["max_sum_assured"] is not None]
    return {
        "age_min": min(gc["min_age"] for gc in group_cs),
        "age_max": max(gc["max_age"] for gc in group_cs),
        "sum_assured_min": min(gc["min_sum_assured"] for gc in group_cs),
        "sum_assured_max": max(max_sas) if max_sas else None,
    }


def _field_meta(field, profile, layer1_records, layer2_records):
    """Widget descriptor for whichever field is about to be asked - lets a frontend render a
    constrained input (a bounded number field, a dropdown of real currently-valid values, or
    checkboxes) instead of free text for every question. Added 2026-08-13, directly in
    response to a user pushing back on continuing to patch free-text parsing edge cases one
    at a time (the "1cr" shorthand fix just above being the most recent example) - "if we
    give user free text, they will continue to reply random things and we can't keep
    building more for every case." Constraining the input is the real fix for any field with
    a small, known valid set; free-text parsing stays only where there's no such set to
    constrain to (this function doesn't remove _parse_field()'s parsing - the n8n chat
    widget's plain-text UI still depends on it, per that session's explicit scope decision:
    only the standalone frontend gets structured widgets, n8n's chat trigger stays free
    text).

    term/premium_payment_option route through the same available_terms()/
    available_payment_options_for_term() real-availability check fill_next_field() already
    uses to validate an answer after the fact - now used here too, to constrain the choices
    offered in the first place rather than only rejecting a bad one after the user typed it.
    """
    bounds = _corpus_bounds(layer2_records)

    if field == "age":
        return {"field": field, "type": "number", "min": bounds["age_min"], "max": bounds["age_max"]}

    if field == "sum_assured":
        meta = {"field": field, "type": "number", "min": bounds["sum_assured_min"], "step": 100_000}
        if bounds["sum_assured_max"] is not None:
            meta["max"] = bounds["sum_assured_max"]
        return meta

    if field == "budget":
        return {"field": field, "type": "number", "min": 0, "step": 1_000}

    if field == "concern_tags":
        return {
            "field": field, "type": "multiselect",
            "options": [{"value": n, "label": label} for n, label in _CONCERN_TAG_LABELS.items()],
        }

    if field == "term":
        if layer1_records is None:
            return {"field": field, "type": "number", "min": 5, "max": 40}
        valid_terms = available_terms(
            layer1_records, layer2_records, profile.get("age"), profile.get("sum_assured"),
            profile.get("concern_tags"),
        )
        return {"field": field, "type": "select", "options": sorted(valid_terms)}

    if field == "premium_payment_option":
        if layer1_records is None:
            options = [{"value": m["reply"], "label": m["label"]} for m in _PAYMENT_OPTION_META.values()]
        else:
            valid = available_payment_options_for_term(
                layer1_records, profile["term"], layer2_records, profile.get("age"),
                profile.get("sum_assured"), profile.get("concern_tags"),
            )
            options = [
                {"value": _PAYMENT_OPTION_META[opt]["reply"], "label": _PAYMENT_OPTION_META[opt]["label"]}
                for opt in _PAYMENT_OPTION_ORDER if opt in valid
            ]
        return {"field": field, "type": "select", "options": options}

    raise ValueError(f"unknown field: {field}")


def fill_next_field(state, message, layer1_records=None, layer2_records=None):
    """The swap point described above - everything before/after this call (session state
    shape, run_query_pipeline.py's execution) stays the same regardless of how this
    function is implemented. `message` is None on the very first turn (no user reply yet).

    `layer1_records` (optional): when provided, validates `term` and
    `premium_payment_option` against real sample-premium-table availability the moment
    they're answered, rejecting an impossible combination immediately instead of silently
    accepting it and discovering the problem only after every question has been answered.
    Confirmed 2026-08-01 that letting this go undetected led to an empty candidate set at
    step 5 and a hallucinated result at step 8 - catching it here is the real fix, a
    downstream "no results" message is a fallback, not a substitute for this.

    `layer2_records` (optional, only used alongside `layer1_records`): narrows the
    availability check to policies actually eligible for the user's already-answered
    age/sum_assured (via eligibility_filter.bounds_ok) - confirmed 2026-08-01 that a term
    can have real data corpus-wide but only on a policy the user doesn't qualify for by
    cover amount, which isn't a real answer for them (the ₹1 crore / term-25 / regular
    case: term 25 exists corpus-wide, but the only policy with that exact column has a
    ₹25L sum assured cap, so it was never a real option for that profile).

    Returns (state, reply_text_or_none, profile_complete, field_meta) - field_meta (see
    _field_meta() above) describes whichever field the reply is asking about (None once
    profile_complete is True), so a frontend can render a constrained widget instead of a
    free-text box; added 2026-08-13 alongside _field_meta() itself. A caller that only wants
    the text (e.g. a plain-text UI) can just ignore the 4th element."""
    profile = state["profile"]

    if message is not None:
        pending = next((f for f in FIELD_ORDER if f not in profile), None)
        if pending is not None:
            value, error = _parse_field(pending, message)
            if error:
                return state, error, False, _field_meta(pending, profile, layer1_records, layer2_records)

            if pending == "term" and layer1_records is not None:
                valid_terms = available_terms(
                    layer1_records, layer2_records, profile.get("age"), profile.get("sum_assured"),
                    profile.get("concern_tags"),
                )
                if value not in valid_terms:
                    options = ", ".join(str(t) for t in sorted(valid_terms))
                    return state, (
                        f"We don't have premium data for a {value}-year term matching your "
                        f"age/cover/concerns. Available terms: {options}. {QUESTIONS['term']}"
                    ), False, _field_meta(pending, profile, layer1_records, layer2_records)

            if pending == "premium_payment_option" and layer1_records is not None:
                valid_options = available_payment_options_for_term(
                    layer1_records, profile["term"], layer2_records, profile.get("age"),
                    profile.get("sum_assured"), profile.get("concern_tags"),
                )
                if value not in valid_options:
                    readable = ", ".join(sorted(valid_options)) or "none"
                    return state, (
                        f"We don't have premium data for a {profile['term']}-year term with "
                        f"'{value}' payments. Available options for this term: {readable}. "
                        f"{QUESTIONS['premium_payment_option']}"
                    ), False, _field_meta(pending, profile, layer1_records, layer2_records)

            profile[pending] = value

    pending = next((f for f in FIELD_ORDER if f not in profile), None)
    if pending is None:
        return state, None, True, None

    return state, QUESTIONS[pending], False, _field_meta(pending, profile, layer1_records, layer2_records)
