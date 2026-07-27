# Step 5 of the query pipeline (docs/query_architecture.md): deterministic premium
# estimation + budget filter. No LLM - a manual two-point linear interpolation over a
# policy's Layer 1 sample_illustrative_premiums (not Layer 2), matched within a single
# premium_payment_option + term column, then scaled for the profile's actual sum_assured.
#
# profile shape (extends the eligibility_filter profile):
#   {"age": int, "sum_assured": int, "term": int, "premium_payment_option": str,
#    "sum_assured_type": "level" | "increasing" (optional, defaults to "level"), "budget": number}


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
