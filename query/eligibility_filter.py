# Step 3/4 of the query pipeline (docs/query_architecture.md): deterministic Group C
# bound checks + a Group A concern-tag gate, over Layer 2 JSON already loaded in memory
# (query.load_extracted.load_layer2) - no Qdrant, no LLM. Applied identically to base
# plans and riders: a rider's eligibility is never inherited from its base plan passing,
# each layer2 record is checked independently.
#
# profile shape: {"age": int, "sum_assured": int, "term": int, "concern_tags": [str]}

from load_extracted import load_layer2  # sibling import - run as `python3 query/eligibility_filter.py`


# Public (not _bounds_ok) as of 2026-08-01 - reused by premium_interpolation.py's
# available_terms()/available_payment_options_for_term() to filter to just the policies
# actually eligible for a given age/sum_assured before checking sample-table availability,
# rather than duplicating this same bounds logic there.
def bounds_ok(profile, group_c):
    age_ok = group_c["min_age"] <= profile["age"] <= group_c["max_age"]
    sa_ok = group_c["min_sum_assured"] <= profile["sum_assured"] and (
        group_c["max_sum_assured"] is None or profile["sum_assured"] <= group_c["max_sum_assured"]
    )  # max_sum_assured nullable - "No Limit, subject to underwriting" (docs/schema.md) means unbounded, not failing
    term_ok = group_c["min_term"] <= profile["term"] <= group_c["max_term"]
    return age_ok and sa_ok and term_ok


def _concern_overlap(profile, concern_tags):
    return set(profile["concern_tags"]) & set(concern_tags)


def filter_eligible(profile, layer2_records, require_bounds=True, require_concern=True):
    """One filter pass over layer2_records. require_bounds/require_concern toggle the
    two gates independently, so apply_fallback can relax them one at a time."""
    results = []
    for policy_id, record in layer2_records.items():
        group_a = record["layer2"]["group_a_concern_tags"]
        group_c = record["layer2"]["group_c"]

        if require_bounds and not bounds_ok(profile, group_c):
            continue

        overlap = _concern_overlap(profile, group_a)
        if require_concern and not overlap:
            continue

        results.append({
            "policy_id": policy_id,
            "concern_match_count": len(overlap),
            "group_b": record["layer2"]["group_b"],
            "group_c": group_c,
        })

    results.sort(key=lambda r: r["concern_match_count"], reverse=True)
    return results


def apply_fallback(profile, layer2_records, min_results=3):
    """Two-tier fallback per docs/query_architecture.md step 4:
    Tier 0: bounds + concern gate both enforced.
    Tier 1 (<min_results at tier 0): bounds relaxed, concern gate stays firm.
    Tier 2 (<min_results at tier 1): concern gate also dropped.

    "Relax" here means the bound check is dropped entirely for the tier, not
    loosened by some margin - the docs don't specify a relaxation magnitude
    (unlike, say, step 7's rerank tolerance, which is at least a flagged
    placeholder), so this is the simplest defensible interpretation for a
    first implementation. Revisit with a margin-based relaxation (e.g. age
    +/-5, sum_assured +/-20%) if tier 1 proves too blunt in practice.
    """
    tier0 = filter_eligible(profile, layer2_records, require_bounds=True, require_concern=True)
    if len(tier0) >= min_results:
        return {"results": tier0, "fallback_tier": 0}

    tier1 = filter_eligible(profile, layer2_records, require_bounds=False, require_concern=True)
    if len(tier1) >= min_results:
        return {"results": tier1, "fallback_tier": 1}

    tier2 = filter_eligible(profile, layer2_records, require_bounds=False, require_concern=False)
    return {"results": tier2, "fallback_tier": 2}


if __name__ == "__main__":
    layer2 = load_layer2()

    print("--- Profile A: age 30, SA 50L, term 20, income_replacement (should clear tier 0, no fallback needed) ---")
    profile_a = {"age": 30, "sum_assured": 5000000, "term": 20, "concern_tags": ["income_replacement"]}
    out = apply_fallback(profile_a, layer2)
    print(f"tier={out['fallback_tier']}, {len(out['results'])} results")
    for r in out["results"]:
        print(f"  {r['policy_id']}: concern_match_count={r['concern_match_count']}")

    print("\n--- Profile A2: age 30, SA 10L, term 20, income_replacement (below most plans' 50L minimum - only 859 clears tier 0, forces tier 1) ---")
    profile_a2 = {"age": 30, "sum_assured": 1000000, "term": 20, "concern_tags": ["income_replacement"]}
    out = apply_fallback(profile_a2, layer2)
    print(f"tier={out['fallback_tier']}, {len(out['results'])} results")
    for r in out["results"]:
        print(f"  {r['policy_id']}: concern_match_count={r['concern_match_count']}")

    print("\n--- Profile B: age 5 (below every plan's min_age - should force fallback) ---")
    profile_b = {"age": 5, "sum_assured": 5000000, "term": 20, "concern_tags": ["income_replacement"]}
    out = apply_fallback(profile_b, layer2)
    print(f"tier={out['fallback_tier']}, {len(out['results'])} results")
    for r in out["results"]:
        print(f"  {r['policy_id']}: concern_match_count={r['concern_match_count']}, group_c={r['group_c']}")

    print("\n--- Profile C: age 30, SA 50L, term 20, retirement_income (no term plan has this concern) ---")
    profile_c = {"age": 30, "sum_assured": 5000000, "term": 20, "concern_tags": ["retirement_income"]}
    out = apply_fallback(profile_c, layer2)
    print(f"tier={out['fallback_tier']}, {len(out['results'])} results")
    for r in out["results"]:
        print(f"  {r['policy_id']}: concern_match_count={r['concern_match_count']}")
