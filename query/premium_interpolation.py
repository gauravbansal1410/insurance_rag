# Step 5 of the query pipeline (docs/query_architecture.md): deterministic premium
# estimation + budget filter. No LLM - a manual two-point linear interpolation over a
# policy's Layer 1 sample_illustrative_premiums (not Layer 2), matched within a single
# premium_payment_option + term column, then scaled for the profile's actual sum_assured.
#
# profile shape (extends the eligibility_filter profile):
#   {"age": int, "sum_assured": int, "term": int, "premium_payment_option": str,
#    "sum_assured_type": "level" | "increasing" (optional, defaults to "level"), "budget": number}

import json
from pathlib import Path

from eligibility_filter import bounds_ok  # sibling import - run as `python3 query/premium_interpolation.py`
from premium_lookup import lookup_premium

# Precomputed premium lookup (docs/query_architecture.md open questions) - built by
# query/build_premium_lookup.py from real scraped ground truth
# (docs/progress/ground-truth/<policy_id>_scraped_premiums.csv) for whichever policies
# have both a real (non-placeholder) rebate table and enough ground truth to validate a
# bilinear age x term surface against (see query/validate_premium_lookup.py) - 876 and
# 859 as of 2026-08-08. Dispatch below is scope-guarded per policy by each entry's own
# stored premium_payment_option/sum_assured_type (no separate hardcoded scope constant
# needed - generalized 2026-08-08 once 859 became a second entry), so a policy/option
# combination missing from this table transparently falls through to the original live
# linear interpolation further down, untouched.
_PREMIUM_LOOKUP_PATH = Path(__file__).resolve().parent / "premium_lookup.json"


def _load_premium_lookup_table():
    if not _PREMIUM_LOOKUP_PATH.exists():
        return {}
    return json.loads(_PREMIUM_LOOKUP_PATH.read_text())


_premium_lookup_table = _load_premium_lookup_table()


def _policy_matches(l2, age, sum_assured, term, concern_tags):
    """Group C bounds AND (if concern_tags given) at least one Group A concern_tags
    overlap - the same hard OR-gate eligibility_filter.py's step 3 applies, needed here too
    since a term/option can look available by age/cover alone but only on a policy that
    doesn't serve the user's stated concern at all (confirmed 2026-08-01: 877/878's 25-year
    data only serves debt_linked_cover, not income_replacement)."""
    if not bounds_ok({"age": age, "sum_assured": sum_assured, "term": term}, l2["layer2"]["group_c"]):
        return False
    if concern_tags is not None:
        if not (set(concern_tags) & set(l2["layer2"]["group_a_concern_tags"])):
            return False
    return True


# Fixed enum of every premium_payment_option service/chat_session.py's QUESTIONS can ever
# produce (its "single"/"regular"/"5"/"10"/"15" mapping) - the candidate set
# available_terms()/available_payment_options_for_term() probe below, since neither
# function knows the user's eventual answer in advance.
_ALL_PAYMENT_OPTIONS = ["single", "regular", "limited_ppt_5", "limited_ppt_10", "limited_ppt_15"]


def _candidate_terms(layer1_records):
    """Union of every term that could conceivably have real data anywhere - both Layer 1's
    sparse brochure sample tables (usually just one term per policy) and every precomputed
    premium_lookup.json grid's own term axis (typically a much wider real range - see
    docs/schema.md's premium-lookup artifact section). Cheap, fixed candidate set; the real
    yes/no per (policy, term, option) still goes through interpolate_premium() itself
    below, this only bounds what gets probed."""
    terms = set()
    for record in layer1_records.values():
        for row in record["layer1"]["sample_illustrative_premiums"]:
            terms.add(row["term"])
    for entry in _premium_lookup_table.values():
        for p in entry["age_term_grid"]:
            terms.add(p["term"])
    return terms


def available_terms(layer1_records, layer2_records=None, age=None, sum_assured=None, concern_tags=None):
    """Every term interpolate_premium() can actually serve (for at least one payment
    option, on at least one eligible policy) - used by the chat slot-filling step
    (service/chat_session.py) to reject an unsupported term immediately, rather than
    silently accepting it and discovering the problem only after every other question has
    been answered (confirmed 2026-08-01 that silently proceeding led to an empty candidate
    set and a hallucinated result).

    **Routes through interpolate_premium() itself - the real step-5 dispatch, including the
    precomputed lookup - rather than re-scanning Layer 1's sample_illustrative_premiums
    directly, as an earlier version of this function did.** Confirmed 2026-08-13 that the
    sample-table-only version had drifted out of sync with what interpolate_premium() could
    actually serve: real regular-premium data out to term=40 on 876/875/954 (via
    premium_lookup.json) was invisible to it, since Layer 1's own brochure table only ever
    samples one or two terms. One source of truth for "is this available", not two that can
    silently disagree.

    If `layer2_records`/`age`/`sum_assured` are given, restricts to policies actually
    eligible for this age/sum_assured/concern (via `_policy_matches()`) before checking
    table availability - confirmed 2026-08-01 that a term can have real data corpus-wide
    but only on a policy the user doesn't qualify for by cover amount or concern, which
    isn't a real answer for them and would otherwise still lead to a dead end later.
    age/sum_assured are required for the real interpolate_premium() check below - if either
    is missing, falls back to the raw candidate universe (shouldn't happen via chat_session,
    whose FIELD_ORDER always supplies both before term is asked)."""
    if age is None or sum_assured is None:
        return _candidate_terms(layer1_records)

    terms = set()
    for policy_id, record in layer1_records.items():
        if layer2_records is not None:
            l2 = layer2_records.get(policy_id)
            if l2 is None:
                continue
        for term in _candidate_terms(layer1_records):
            if layer2_records is not None and not _policy_matches(l2, age, sum_assured, term, concern_tags):
                continue
            for option in _ALL_PAYMENT_OPTIONS:
                profile = {"age": age, "sum_assured": sum_assured, "term": term, "premium_payment_option": option}
                if not interpolate_premium(profile, record)["excluded"]:
                    terms.add(term)
                    break
    return terms


def available_payment_options_for_term(layer1_records, term, layer2_records=None, age=None,
                                        sum_assured=None, concern_tags=None):
    """Every payment option interpolate_premium() can actually serve at this specific term -
    a term can be servable under some options but not others. Same real-dispatch routing
    and eligibility-narrowing as available_terms() above, same 2026-08-13 fix (this used to
    scan Layer 1's sample table directly too, missing premium_lookup.json's real coverage
    the same way)."""
    if age is None or sum_assured is None:
        options = set()
        for record in layer1_records.values():
            for row in record["layer1"]["sample_illustrative_premiums"]:
                if row["term"] == term:
                    options.add(row["premium_payment_option"])
        return options

    options = set()
    for policy_id, record in layer1_records.items():
        if layer2_records is not None:
            l2 = layer2_records.get(policy_id)
            if l2 is None or not _policy_matches(l2, age, sum_assured, term, concern_tags):
                continue
        for option in _ALL_PAYMENT_OPTIONS:
            profile = {"age": age, "sum_assured": sum_assured, "term": term, "premium_payment_option": option}
            if not interpolate_premium(profile, record)["excluded"]:
                options.add(option)
    return options


def _rows_for_column(sample_table, term, premium_payment_option):
    return sorted(
        (r for r in sample_table if r["term"] == term and r["premium_payment_option"] == premium_payment_option),
        key=lambda r: r["age"],
    )


def _split_sum_assured_variants(sample_table):
    """Groups a sample_illustrative_premiums table by its real sum_assured_type field
    ("level" | "increasing") - added to Layer 1's extraction schema 2026-07-27
    (docs/prompts/prompt_a_pdf.txt trap 16, docs/schema.md) after finding that 4/7
    term-assurance policies (875, 876, 954, 955) print a full second sample-premium
    table for an increasing-sum-assured death-benefit option, identical to the level-SA
    table on every other field. An earlier version of this function inferred the split
    positionally (first half of the array = level, second half = increasing), since the
    field didn't exist yet - replaced now that all 4 affected policies have been
    re-extracted with the real tag, read from each table's own heading rather than
    guessed from array order."""
    variants = {}
    for row in sample_table:
        variants.setdefault(row.get("sum_assured_type", "level"), []).append(row)
    return variants


def interpolate_premium(profile, layer1_record):
    """Returns {"excluded": False, "premium_amount": ..., ...} or
    {"excluded": True, "reason": ...} - never raises, so a bad candidate never fails the
    whole query (docs/query_architecture.md: "the query still completes")."""
    sum_assured_type = profile.get("sum_assured_type", "level")
    policy_id = layer1_record["policy_id"]

    lookup_entry = _premium_lookup_table.get(policy_id)
    if (
        lookup_entry is not None
        and profile["premium_payment_option"] == lookup_entry["premium_payment_option"]
        and sum_assured_type == lookup_entry["sum_assured_type"]
    ):
        return lookup_premium(
            lookup_entry, profile["age"], profile["term"], profile["sum_assured"],
        )

    sample_table = layer1_record["layer1"]["sample_illustrative_premiums"]
    variants = _split_sum_assured_variants(sample_table)

    if sum_assured_type not in variants:
        return {
            "excluded": True,
            "reason": f"no sample-premium table for sum_assured_type={sum_assured_type} "
                      f"(this policy only has: {sorted(variants)})",
        }

    rows = _rows_for_column(variants[sum_assured_type], profile["term"], profile["premium_payment_option"])

    if not rows:
        return {
            "excluded": True,
            "reason": f"no sample-premium table for term={profile['term']}, "
                      f"premium_payment_option={profile['premium_payment_option']}, "
                      f"sum_assured_type={sum_assured_type}",
        }

    ages = [r["age"] for r in rows]
    if profile["age"] < ages[0] or profile["age"] > ages[-1]:
        return {
            "excluded": True,
            "reason": f"age {profile['age']} outside sample table range [{ages[0]}, {ages[-1]}] "
                      f"for this term/option",
        }

    # Exact age match - no interpolation needed, just scale for sum_assured.
    exact = next((r for r in rows if r["age"] == profile["age"]), None)
    if exact is not None:
        baseline_premium, baseline_sa = exact["premium_amount"], exact["sum_assured"]
        method = "exact_age_match"
    else:
        lower = max((r for r in rows if r["age"] < profile["age"]), key=lambda r: r["age"])
        upper = min((r for r in rows if r["age"] > profile["age"]), key=lambda r: r["age"])
        # Two-point linear interpolation in age, at the table's own baseline sum_assured
        # (lower/upper should share the same baseline SA within one term/option column -
        # this is the "within-column" assumption the docs call out).
        span = upper["age"] - lower["age"]
        weight = (profile["age"] - lower["age"]) / span
        baseline_premium = lower["premium_amount"] + weight * (upper["premium_amount"] - lower["premium_amount"])
        baseline_sa = lower["sum_assured"]
        method = f"interpolated_between_ages_{lower['age']}_{upper['age']}"

    # Scale linearly for the profile's actual sum_assured vs. the table's baseline SA.
    # UNCALIBRATED assumption (see docs/query_architecture.md open questions): term-life
    # premiums are assumed close enough to linear in sum_assured for this estimate: not
    # checked against real premium curves yet.
    premium_amount = baseline_premium * (profile["sum_assured"] / baseline_sa)

    return {
        "excluded": False,
        "premium_amount": round(premium_amount, 2),
        "method": method,
        "table_baseline_sum_assured": baseline_sa,
        "sum_assured_type": sum_assured_type,
    }


def filter_by_budget(candidates, profile, layer1_records):
    """candidates: eligibility_filter results (each has policy_id). Returns
    (survivors, excluded_log) - survivors have premium_amount attached, excluded_log
    entries carry the reason (out-of-range table, or over budget). Never drops the whole
    query - exclude-and-log only, per docs/query_architecture.md step 5."""
    survivors, excluded_log = [], []
    for candidate in candidates:
        layer1_record = layer1_records[candidate["policy_id"]]
        estimate = interpolate_premium(profile, layer1_record)

        if estimate["excluded"]:
            excluded_log.append({"policy_id": candidate["policy_id"], "reason": estimate["reason"]})
            continue

        if estimate["premium_amount"] > profile["budget"]:
            excluded_log.append({
                "policy_id": candidate["policy_id"],
                "reason": f"estimated premium {estimate['premium_amount']} exceeds budget {profile['budget']}",
            })
            continue

        survivors.append({**candidate, "premium_amount": estimate["premium_amount"], "premium_method": estimate["method"]})

    return survivors, excluded_log


if __name__ == "__main__":
    from load_extracted import load_layer1, load_layer2
    from eligibility_filter import apply_fallback

    layer1 = load_layer1()
    layer2 = load_layer2()

    print("--- Hand-check: policy 877, age 30, SA 50L (table's own baseline SA), term 25, limited_ppt_15 - exact age match, no scaling ---")
    est = interpolate_premium(
        {"age": 30, "sum_assured": 5000000, "term": 25, "premium_payment_option": "limited_ppt_15"},
        layer1["877"],
    )
    print(est)  # expect premium_amount == 6200 (exact match from extracted/layer1_877_..._run1.json, baseline SA)

    print("\n--- Hand-check: policy 877, age 35 (between table's 30 and 40), same SA/term/option - true interpolation ---")
    est = interpolate_premium(
        {"age": 35, "sum_assured": 5000000, "term": 25, "premium_payment_option": "limited_ppt_15"},
        layer1["877"],
    )
    print(est)  # expect midpoint of age 30 (6200) and age 40 (11650) = 8925

    print("\n--- Hand-check: policy 877, SA 25L (half the table's 50L baseline) - should exactly halve the premium ---")
    est = interpolate_premium(
        {"age": 30, "sum_assured": 2500000, "term": 25, "premium_payment_option": "limited_ppt_15"},
        layer1["877"],
    )
    print(est)  # expect 3100 (half of 6200)

    print("\n--- Hand-check: policy 877, term=99 (no such column) - should exclude ---")
    est = interpolate_premium(
        {"age": 30, "sum_assured": 5000000, "term": 99, "premium_payment_option": "limited_ppt_15"},
        layer1["877"],
    )
    print(est)

    print("\n--- Hand-check: policy 875, age 30, term 20, regular, sum_assured_type omitted -> defaults to 'level' ---")
    est = interpolate_premium(
        {"age": 30, "sum_assured": 5000000, "term": 20, "premium_payment_option": "regular"},
        layer1["875"],
    )
    print(est)  # expect premium_amount == 5950 (first/level table's value - see raw layer1_875.json row 4)

    print("\n--- Hand-check: policy 875, same profile, sum_assured_type='increasing' explicitly ---")
    est = interpolate_premium(
        {"age": 30, "sum_assured": 5000000, "term": 20, "premium_payment_option": "regular", "sum_assured_type": "increasing"},
        layer1["875"],
    )
    print(est)  # expect premium_amount == 8250 (second table's value - see raw layer1_875.json row 16)

    print("\n--- Hand-check: policy 877 (no level/increasing split - single table), asking for 'increasing' should exclude ---")
    est = interpolate_premium(
        {"age": 30, "sum_assured": 5000000, "term": 25, "premium_payment_option": "limited_ppt_15", "sum_assured_type": "increasing"},
        layer1["877"],
    )
    print(est)  # expect excluded=True - 877 only has a "level" table (single, undifferentiated)

    print("\n--- End-to-end: eligibility -> budget filter for a realistic profile ---")
    print("    (debt_linked_cover forces tier-2 fallback so all 7 policies are candidates;")
    print("     term=25/limited_ppt_15 has clean data on 877/878 but no such column at all on")
    print("     875/876/512.../859, whose sample tables only cover term=20 - see excluded log)")
    profile = {
        "age": 30, "sum_assured": 5000000, "term": 25,
        "concern_tags": ["debt_linked_cover"],
        "premium_payment_option": "limited_ppt_15", "budget": 6500,
    }
    eligible = apply_fallback(profile, layer2)
    print(f"eligible: tier={eligible['fallback_tier']}, {len(eligible['results'])} candidates")
    survivors, excluded_log = filter_by_budget(eligible["results"], profile, layer1)
    print(f"survivors (within budget {profile['budget']}):")
    for s in survivors:
        print(f"  {s['policy_id']}: premium={s['premium_amount']} ({s['premium_method']})")
    print("excluded:")
    for e in excluded_log:
        print(f"  {e['policy_id']}: {e['reason']}")

    print("\n--- End-to-end, level vs increasing: same profile against 875/876/512..., budget tight enough to separate them ---")
    profile_level = {
        "age": 30, "sum_assured": 5000000, "term": 20,
        "concern_tags": ["income_replacement"],
        "premium_payment_option": "regular", "sum_assured_type": "level", "budget": 6500,
    }
    eligible = apply_fallback(profile_level, layer2)
    survivors, excluded_log = filter_by_budget(eligible["results"], profile_level, layer1)
    print(f"level (budget {profile_level['budget']}) survivors:")
    for s in survivors:
        print(f"  {s['policy_id']}: premium={s['premium_amount']}")

    profile_increasing = {**profile_level, "sum_assured_type": "increasing"}
    survivors, excluded_log = filter_by_budget(eligible["results"], profile_increasing, layer1)
    print(f"increasing (same budget {profile_increasing['budget']}) survivors - expect fewer, since increasing SA costs more:")
    for s in survivors:
        print(f"  {s['policy_id']}: premium={s['premium_amount']}")
