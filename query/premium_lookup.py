# Precomputed premium lookup - v1 pilot, policy 876 only, regular/level only
# (docs/query_architecture.md open questions). Replaces the single two-point linear
# interpolation in premium_interpolation.py with two independently-modeled axes,
# confirmed separable against real scraped ground truth
# (docs/progress/ground-truth/876_scraped_premiums.csv):
#
#   1. age x term (NOT linear - mortality cost accelerates with age): bilinear
#      interpolation over the real (age, term) grid points we actually have, at one
#      reference sum_assured per policy. A query outside the known grid's convex hull
#      is excluded, never extrapolated - same "exclude and log" behavior
#      premium_interpolation.py already uses for out-of-range candidates.
#   2. sum_assured (separable from term, confirmed constant ratio at every term within
#      an age band - but NOT simple proportional scaling: real ratios are ~1.64-1.68x
#      at 1Cr and ~2.80-2.88x at 2Cr, not 2x/4x, likely a fixed-cost component the
#      rebate_structures percentage table alone doesn't capture) - stored as an
#      empirical multiplier table keyed by age band, derived directly from scraped
#      ratios rather than reverse-engineered from rebate percentages.
#
# The grid is scattered, not a clean rectangle (some ages only have 1-2 terms
# scraped) - see docs/progress/ground-truth/ and the 2026-08 gap analysis. The age
# bracket search below deliberately skips past a thin single-term anchor to the next
# age that actually has a real bracket for the requested term, rather than blocking
# on an exact age match with insufficient data at that exact age.


def _term_interp(term_premium_pairs, term):
    """term_premium_pairs: [(term, premium), ...] for one age (unsorted OK).
    Returns interpolated premium at `term`, or None if `term` isn't bracketed by
    this age's own data (i.e. this age can't supply the requested term at all)."""
    lo = max((p for p in term_premium_pairs if p[0] <= term), key=lambda p: p[0], default=None)
    hi = min((p for p in term_premium_pairs if p[0] >= term), key=lambda p: p[0], default=None)
    if lo is None or hi is None:
        return None
    if lo[0] == hi[0]:
        return lo[1]
    weight = (term - lo[0]) / (hi[0] - lo[0])
    return lo[1] + weight * (hi[1] - lo[1])


def _age_side_with_term_bracket(ages_by_distance, by_age, term):
    """ages_by_distance: ages ordered nearest-to-query-first, all on one side
    (all <= query age, or all >= query age). Returns the first age in that order
    whose own data actually brackets `term` - skipping past a thin anchor (e.g. an
    age with only one scraped term) that can't supply the requested term itself."""
    for age in ages_by_distance:
        pairs = by_age[age]
        terms = [t for t, _ in pairs]
        if min(terms, default=term + 1) <= term <= max(terms, default=term - 1):
            return age
    return None


def bilinear_lookup(age_term_grid, age, term):
    """age_term_grid: [{"age": int, "term": int, "premium": number}, ...] at one
    reference sum_assured. Returns {"premium": ..., "method": ...} or None if
    (age, term) falls outside the known grid (no usable bracket on one or both
    axes) - the caller should treat None as excluded, not extrapolate."""
    by_age = {}
    for row in age_term_grid:
        by_age.setdefault(row["age"], []).append((row["term"], row["premium"]))

    known_ages = sorted(by_age)
    below = sorted((a for a in known_ages if a <= age), reverse=True)
    above = sorted(a for a in known_ages if a >= age)

    age_lo = _age_side_with_term_bracket(below, by_age, term)
    age_hi = _age_side_with_term_bracket(above, by_age, term)
    if age_lo is None or age_hi is None:
        return None

    premium_lo = _term_interp(by_age[age_lo], term)
    if age_lo == age_hi:
        return {"premium": premium_lo, "method": f"bilinear_age_{age_lo}_exact_or_term_interp"}

    premium_hi = _term_interp(by_age[age_hi], term)
    weight = (age - age_lo) / (age_hi - age_lo)
    premium = premium_lo + weight * (premium_hi - premium_lo)
    return {"premium": premium, "method": f"bilinear_age_{age_lo}_{age_hi}_term_{term}"}


def sa_multiplier(sa_scaling, age, sum_assured):
    """sa_scaling: {"reference_sum_assured": int, "age_bands": [{"age_min", "age_max",
    "points": [{"sum_assured", "multiplier"}, ...]}, ...]}. Returns a multiplier
    (piecewise-linear over the band's known SA points, reference SA always implicitly
    1.0x) or None if `sum_assured` falls outside the known points for this age's band
    (e.g. above the highest sum_assured we have real rebate data for) - excluded, not
    extrapolated, same policy as the age x term axis above."""
    band = next(
        (b for b in sa_scaling["age_bands"] if b["age_min"] <= age <= b["age_max"]),
        None,
    )
    if band is None:
        return None

    ref_sa = sa_scaling["reference_sum_assured"]
    points = [(ref_sa, 1.0)] + [(p["sum_assured"], p["multiplier"]) for p in band["points"]]
    points.sort()

    if sum_assured < points[0][0] or sum_assured > points[-1][0]:
        return None

    lo = max((p for p in points if p[0] <= sum_assured), key=lambda p: p[0])
    hi = min((p for p in points if p[0] >= sum_assured), key=lambda p: p[0])
    if lo[0] == hi[0]:
        return lo[1]
    weight = (sum_assured - lo[0]) / (hi[0] - lo[0])
    return lo[1] + weight * (hi[1] - lo[1])


def lookup_premium(policy_lookup, age, term, sum_assured):
    """policy_lookup: one policy's entry from premium_lookup.json. Returns
    {"excluded": False, "premium_amount": ..., "method": ...} or
    {"excluded": True, "reason": ...} - matches premium_interpolation.interpolate_premium's
    return shape so the two are drop-in interchangeable at the call site."""
    base = bilinear_lookup(policy_lookup["age_term_grid"], age, term)
    if base is None:
        return {
            "excluded": True,
            "reason": f"age={age}/term={term} outside the known premium-lookup grid "
                      f"(no bracket on one or both axes)",
        }

    multiplier = sa_multiplier(policy_lookup["sa_scaling"], age, sum_assured)
    if multiplier is None:
        return {
            "excluded": True,
            "reason": f"sum_assured={sum_assured} outside the known SA-scaling range for age={age}",
        }

    return {
        "excluded": False,
        "premium_amount": round(base["premium"] * multiplier, 2),
        "method": f"{base['method']}_x_sa_multiplier_{round(multiplier, 4)}",
        "table_baseline_sum_assured": policy_lookup["sa_scaling"]["reference_sum_assured"],
        "sum_assured_type": policy_lookup["sum_assured_type"],
    }
