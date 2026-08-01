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


def _parse_field(field, message):
    """Returns (parsed_value, error_message_or_none) - never raises, so a bad reply
    re-asks the same question rather than crashing the session."""
    text = message.strip().lower()

    if field in ("age", "sum_assured", "term"):
        num = _extract_number(text)
        if num is None:
            return None, f"I didn't understand that as a number. {QUESTIONS[field]}"
        return int(float(num)), None

    if field == "budget":
        num = _extract_number(text)
        if num is None:
            return None, f"I didn't understand that as a number. {QUESTIONS[field]}"
        return float(num), None

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

    Returns (state, reply_text_or_none, profile_complete)."""
    profile = state["profile"]

    if message is not None:
        pending = next((f for f in FIELD_ORDER if f not in profile), None)
        if pending is not None:
            value, error = _parse_field(pending, message)
            if error:
                return state, error, False

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
                    ), False

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
                    ), False

            profile[pending] = value

    pending = next((f for f in FIELD_ORDER if f not in profile), None)
    if pending is None:
        return state, None, True

    return state, QUESTIONS[pending], False
