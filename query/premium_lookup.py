# Precomputed premium lookup - v1 pilot, policy 876 only, regular/level only
# (docs/query_architecture.md open questions), policy 859's SA axis added 2026-08-05.
# Replaces the single two-point linear interpolation in premium_interpolation.py with
# two independently-modeled axes, confirmed separable against real scraped ground truth
# (docs/progress/ground-truth/876_scraped_premiums.csv):
#
#   1. age x term (NOT linear - mortality cost accelerates with age): bilinear
#      interpolation over the real (age, term) grid points we actually have, at one
#      reference sum_assured per policy. A query outside the known grid's convex hull
#      is excluded, never extrapolated - same "exclude and log" behavior
#      premium_interpolation.py already uses for out-of-range candidates.
#   2. sum_assured (separable from term, confirmed constant ratio at every term within
#      an age band on 876 - but NOT simple proportional scaling, e.g. Rs 1Cr costs
#      ~1.64-1.68x of Rs 50L, not 2x): a closed-form formula parsed directly from
#      Layer 1's rebate_structures.high_sum_assured_rebate_table (docs/schema.md),
#      needing zero scraping - scraped ground truth is used only as a QA cross-check
#      on the formula (see query/build_premium_lookup.py), never as its source.
#      **The formula's actual shape is policy-specific, not one universal equation** -
#      confirmed 2026-08-05 that 859's real rebate table is genuinely a different kind
#      of rebate, not just different numbers: a flat Rupee deduction proportional to
#      Basic Sum Assured, not a percent-of-premium discount. compute_sa_adjustment()
#      below dispatches on the table's own "type" tag (set by the extraction prompt
#      itself, docs/prompts/prompt_a_pdf.txt trap 17 - not inferred or guessed here):
#        - "percent_of_tabular_premium" (876/877-style, age-banded):
#          premium(SA) = base_premium x (SA / reference_SA) x (1 - rebate_pct/100)
#        - "per_mille_of_sum_assured_rupees" (859-style, no age split):
#          premium(SA) = base_premium x (SA / reference_SA) - (rate_per_mille/1000) x SA
#      Sum-assured bands in both shapes are extracted as explicit numeric
#      {min, max (None = open-ended), ...} objects with an INCLUSIVE max (per the same
#      trap 17 contract) - no string-band-name parsing or inclusive/exclusive
#      convention-guessing needed at this layer.
#
# The age x term grid is scattered, not a clean rectangle (some ages only have 1-2
# terms scraped) - see docs/progress/ground-truth/ and the 2026-08 gap analysis. The
# age bracket search below deliberately skips past a thin single-term anchor to the
# next age that actually has a real bracket for the requested term, rather than
# blocking on an exact age match with insufficient data at that exact age.


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


def _find_sa_band(sa_bands, sum_assured):
    """sa_bands: [{"min": int, "max": int | None, ...}, ...] - max is INCLUSIVE
    (None = open-ended/"and above"), per the extraction prompt's explicit numeric-band
    contract (docs/prompts/prompt_a_pdf.txt trap 17). Returns the matching band, or
    None if sum_assured falls in a genuine gap between printed bands (e.g. 859's real
    table has no band at all for BSA 9.5L-10L - a real gap in the source document, not
    a bug) - caller should treat None as excluded, not interpolate across the gap."""
    return next(
        (b for b in sa_bands if b["min"] <= sum_assured and (b["max"] is None or sum_assured <= b["max"])),
        None,
    )


def compute_sa_adjustment(sa_scaling, base_premium, age, sum_assured):
    """sa_scaling: one policy's built SA-scaling object (query/build_premium_lookup.py),
    tagged with the same "type" the extraction prompt itself assigned to
    rebate_structures.high_sum_assured_rebate_table - dispatches on that tag rather than
    assuming one universal rebate formula, since 859's real rebate table turned out to
    be a genuinely different kind of rebate from 876's, not just different numbers (see
    this module's docstring). Returns {"premium": ..., "method": ...} or None if
    sum_assured/age falls outside what this policy's rebate table actually covers -
    caller should treat None as excluded, never extrapolated."""
    ref_sa = sa_scaling["reference_sum_assured"]
    if sum_assured < ref_sa:
        return None

    rtype = sa_scaling["type"]

    if rtype == "percent_of_tabular_premium":
        band = next(
            (b for b in sa_scaling["age_bands"] if b["age_min"] <= age <= b["age_max"]),
            None,
        )
        if band is None:
            return None
        sa_band = _find_sa_band(band["sa_bands"], sum_assured)
        if sa_band is None:
            return None
        multiplier = (sum_assured / ref_sa) * (1 - sa_band["rebate_pct"] / 100)
        return {
            "premium": base_premium * multiplier,
            "method": f"percent_of_tabular_premium_x{round(multiplier, 4)}",
        }

    if rtype == "per_mille_of_sum_assured_rupees":
        sa_band = _find_sa_band(sa_scaling["sa_bands"], sum_assured)
        if sa_band is None:
            return None
        rebate_rs = (sa_band["rate_per_mille"] / 1000) * sum_assured
        premium = base_premium * (sum_assured / ref_sa) - rebate_rs
        return {
            "premium": premium,
            "method": f"per_mille_of_sum_assured_rupees_rebate_rs{round(rebate_rs, 2)}",
        }

    raise ValueError(f"compute_sa_adjustment: unsupported sa_scaling type {rtype!r} - "
                      f"add a formula branch above before building a lookup that uses it")


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

    adjustment = compute_sa_adjustment(policy_lookup["sa_scaling"], base["premium"], age, sum_assured)
    if adjustment is None:
        return {
            "excluded": True,
            "reason": f"sum_assured={sum_assured} outside the known SA-scaling range for age={age}",
        }

    return {
        "excluded": False,
        "premium_amount": round(adjustment["premium"], 2),
        "method": f"{base['method']}_x_{adjustment['method']}",
        "table_baseline_sum_assured": policy_lookup["sa_scaling"]["reference_sum_assured"],
        "sum_assured_type": policy_lookup["sum_assured_type"],
    }
