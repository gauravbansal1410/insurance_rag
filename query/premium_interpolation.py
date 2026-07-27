# Step 5 of the query pipeline (docs/query_architecture.md): deterministic premium
# estimation + budget filter. No LLM - a manual two-point linear interpolation over a
# policy's Layer 1 sample_illustrative_premiums (not Layer 2), matched within a single
# premium_payment_option + term column, then scaled for the profile's actual sum_assured.
#
# profile shape (extends the eligibility_filter profile):
#   {"age": int, "sum_assured": int, "term": int, "premium_payment_option": str, "budget": number}


def _rows_for_column(sample_table, term, premium_payment_option):
    return sorted(
        (r for r in sample_table if r["term"] == term and r["premium_payment_option"] == premium_payment_option),
        key=lambda r: r["age"],
    )


def _ambiguous_ages(rows):
    """Detects rows that share every currently-extracted key (age/term/option/payment_mode/
    distribution_channel) but disagree on premium_amount - found on 4/7 term-assurance
    policies (875, 876, 954, 955) during 2026-07-27 testing: every row is exactly
    duplicated except for premium_amount, meaning some real dimension (most likely
    gender - a common LIC premium-table split) isn't captured anywhere in Layer 1's
    sample_illustrative_premiums schema (docs/schema.md). Silently picking one would
    produce a wrong premium for real users, so this is treated as a hard exclusion, not
    guessed around - a Layer 1 schema/extraction fix is the real fix, out of scope here."""
    by_age = {}
    for r in rows:
        by_age.setdefault(r["age"], set()).add(r["premium_amount"])
    return {age for age, amounts in by_age.items() if len(amounts) > 1}


def interpolate_premium(profile, layer1_record):
    """Returns {"excluded": False, "premium_amount": ..., ...} or
    {"excluded": True, "reason": ...} - never raises, so a bad candidate never fails the
    whole query (docs/query_architecture.md: "the query still completes")."""
    sample_table = layer1_record["layer1"]["sample_illustrative_premiums"]
    rows = _rows_for_column(sample_table, profile["term"], profile["premium_payment_option"])

    if not rows:
        return {
            "excluded": True,
            "reason": f"no sample-premium table for term={profile['term']}, "
                      f"premium_payment_option={profile['premium_payment_option']}",
        }

    ambiguous = _ambiguous_ages(rows)
    if ambiguous:
        return {
            "excluded": True,
            "reason": f"ambiguous sample-premium rows at age(s) {sorted(ambiguous)} - identical on every "
                      f"extracted field but different premium_amount, likely an uncaptured schema dimension "
                      f"(e.g. gender) - see query/premium_interpolation.py's _ambiguous_ages docstring",
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

    print("\n--- Hand-check: policy 875, age 30, term 20, regular - real data has duplicate/ambiguous rows, should exclude ---")
    est = interpolate_premium(
        {"age": 30, "sum_assured": 5000000, "term": 20, "premium_payment_option": "regular"},
        layer1["875"],
    )
    print(est)  # expect excluded=True, ambiguity reason - see _ambiguous_ages docstring

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
