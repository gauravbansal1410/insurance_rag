#!/usr/bin/env python3
"""Builds query/premium_lookup.json - one entry per policy in POLICIES below, each
regular/level only for now (see query/premium_lookup.py for the interpolation design
this feeds). Generalized 2026-08-08 from an earlier 876-only version (module-level
constants) once policy 859 needed the same build with different scope values and a
different rebate-table shape - every policy-specific value now lives in one POLICIES
entry instead of module globals, so adding a policy is a new list entry, not a rewrite.

Usage: python3 query/build_premium_lookup.py

For each policy, merges two sources at its own reference sum_assured (that policy's
own sample-table baseline, and also its sum_assured_min - the 0%-rebate band):
  - extracted/layer1_<id>.json's own sample_illustrative_premiums (regular/level rows)
  - docs/progress/ground-truth/<id>_scraped_premiums.csv (regular/level, SA=reference only)
On an exact (age, term) collision, the scraped row wins (already cross-validated
against the brochure's own baseline for 876 - see docs/progress/20260801-progress.md -
so this is a tie-break rule, not a real conflict).

sa_scaling is a direct formula parsed from Layer 1's own
rebate_structures.high_sum_assured_rebate_table, not an empirical table - see
query/premium_lookup.py's module docstring for the two formula shapes this
dispatches between (corrected 2026-08-05, generalized 2026-08-05 once policy 859
confirmed the rebate table's real shape - not just its numbers - varies by policy).
The rebate table's own "type" tag and explicit numeric {min, max, ...} sa_bands
(docs/prompts/prompt_a_pdf.txt trap 17) mean this loader does no string-band
parsing or bound-inclusivity guessing - it reads the extraction's own numbers
directly. This means the SA axis needs zero scraping once a policy's rebate table
is real structured data (not the placeholder prose string still present on most
term-assurance policies as of this date - see docs/schema.md's extraction-rule
caveats). The scraped CSV's cross-SA rows are still used here, but only as a QA
cross-check logged into the built artifact, not as the source of the multiplier.
"""

import csv
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH_DIR = REPO_ROOT / "docs" / "progress" / "ground-truth"
OUT_PATH = REPO_ROOT / "query" / "premium_lookup.json"

# One entry per policy this build script knows how to produce a lookup for.
# age_bands/rebate_sa_type_key/rebate_premium_type_key are only meaningful for a
# "percent_of_tabular_premium" rebate table (876's shape) - a "per_mille_of_sum_
# assured_rupees" policy (859's shape) has no age split at all, so those keys are
# simply absent from its entry rather than set to some placeholder value.
POLICIES = [
    {
        "policy_id": "876",
        "layer1_path": REPO_ROOT / "extracted" / "layer1_876.json",
        "ground_truth_csv": GROUND_TRUTH_DIR / "876_scraped_premiums.csv",
        "reference_sum_assured": 5_000_000,  # 876's own sum_assured_min - 0% rebate band
        "premium_payment_option": "regular",
        "sum_assured_type": "level",
        # CSV was scraped 2026-08-01 - fixed here rather than read from the system
        # clock, since age depends on this date.
        "scrape_date": date(2026, 8, 1),
        # Matches rebate_structures.high_sum_assured_rebate_table's own age-band key
        # names for level_sum_assured / regular_limited_premium (docs/schema.md,
        # extracted/layer1_876.json) - the extraction prompt requires explicit numeric
        # sa_bands leaves (trap 17) but leaves the age-band level's own key naming to
        # mirror the document, so this mapping (which age range a given key name means)
        # is still policy-specific config, not something the build script can infer
        # from the key string alone.
        "age_bands": [
            {"band": "up_to_30_years", "age_min": 0, "age_max": 30},
            {"band": "31_to_45_years", "age_min": 31, "age_max": 999},
        ],
        "rebate_sa_type_key": "level_sum_assured",
        "rebate_premium_type_key": "regular_limited_premium",
    },
    {
        "policy_id": "859",
        "layer1_path": REPO_ROOT / "extracted" / "layer1_859.json",
        "ground_truth_csv": GROUND_TRUTH_DIR / "859_scraped_premiums.csv",
        "reference_sum_assured": 500_000,  # 859's own sum_assured_min - Nil rebate band
        "premium_payment_option": "regular",
        "sum_assured_type": "level",
        "scrape_date": date(2026, 8, 8),
        # No age_bands/rebate_sa_type_key/rebate_premium_type_key - 859's rebate table
        # is per_mille_of_sum_assured_rupees, dimensions: [], so build_sa_scaling()
        # reads table["sa_bands"] directly with no age navigation at all.
    },
    {
        "policy_id": "875",
        "layer1_path": REPO_ROOT / "extracted" / "layer1_875.json",
        "ground_truth_csv": GROUND_TRUTH_DIR / "875_scraped_premiums.csv",
        "reference_sum_assured": 5_000_000,  # 875's own sum_assured_min - 0% rebate band, same figure as 876's
        "premium_payment_option": "regular",
        "sum_assured_type": "level",
        "scrape_date": date(2026, 8, 8),
        # Same percent_of_tabular_premium shape and age-band key names as 876's entry
        # (docs/schema.md) - confirmed by inspecting extracted/layer1_875.json's own
        # rebate_structures.high_sum_assured_rebate_table directly, not assumed from
        # 876's shape.
        "age_bands": [
            {"band": "up_to_30_years", "age_min": 0, "age_max": 30},
            {"band": "31_to_45_years", "age_min": 31, "age_max": 999},
        ],
        "rebate_sa_type_key": "level_sum_assured",
        "rebate_premium_type_key": "regular_limited_premium",
    },
]


def _age_from_dob(dob_str, scrape_date):
    d, m, y = (int(p) for p in dob_str.split("/"))
    age = scrape_date.year - y
    if (scrape_date.month, scrape_date.day) < (m, d):
        age -= 1
    return age


def _age_from_row(row, scrape_date):
    if row["dob"]:
        return _age_from_dob(row["dob"], scrape_date)
    m = re.search(r"intended_age (\d+)", row["notes"])
    if m is None:
        raise ValueError(f"row has no dob and no intended_age note: {row}")
    return int(m.group(1))


def _age_band(age, age_bands):
    for b in age_bands:
        if b["age_min"] <= age <= b["age_max"]:
            return b["band"]
    raise ValueError(f"age {age} doesn't fall in any known age band")


def load_rebate_table(cfg):
    """Reads rebate_structures.high_sum_assured_rebate_table as-is - the extraction
    prompt (docs/prompts/prompt_a_pdf.txt trap 17) requires this to already be typed
    ("type": "percent_of_tabular_premium" | "per_mille_of_sum_assured_rupees" | "other")
    with explicit numeric {min, max, ...} sa_bands leaves, so no string-band parsing
    happens here or in query/premium_lookup.py's compute_sa_adjustment() - this loader
    just fails loudly if the field is still the old unstructured placeholder/prose
    shape (a real, still-possible state - see docs/schema.md's extraction-rule
    caveats for which policies haven't been re-extracted with the current prompt yet)."""
    layer1 = json.loads(cfg["layer1_path"].read_text())["layer1"]
    table = layer1["rebate_structures"]["high_sum_assured_rebate_table"]
    if not isinstance(table, dict) or "type" not in table:
        raise TypeError(
            f"{cfg['policy_id']}: rebate_structures.high_sum_assured_rebate_table is not "
            "structured, typed data (still the old placeholder/prose string, or extracted "
            "before trap 17's \"type\" tag was added?) - can't build a formula from it"
        )
    return table


def build_sa_bands_for_type_a(table, cfg):
    """percent_of_tabular_premium: walks table["table"][rebate_sa_type_key][rebate_premium_type_key]
    (this policy's fixed sum_assured_type/premium_payment_option scope) down to each age
    band's own "sa_bands" array - already numeric, extracted verbatim from Layer 1."""
    by_age_band_key = table["table"][cfg["rebate_sa_type_key"]][cfg["rebate_premium_type_key"]]
    age_bands_out = []
    for b in cfg["age_bands"]:
        sa_bands = [dict(x) for x in by_age_band_key[b["band"]]]
        age_bands_out.append({"band": b["band"], "age_min": b["age_min"], "age_max": b["age_max"], "sa_bands": sa_bands})
    return age_bands_out


def load_brochure_points(cfg):
    layer1 = json.loads(cfg["layer1_path"].read_text())["layer1"]
    points = {}
    for row in layer1["sample_illustrative_premiums"]:
        if (
            row["premium_payment_option"] == cfg["premium_payment_option"]
            and row.get("sum_assured_type", "level") == cfg["sum_assured_type"]
            and row["sum_assured"] == cfg["reference_sum_assured"]
        ):
            points[(row["age"], row["term"])] = {
                "age": row["age"], "term": row["term"],
                "premium": row["premium_amount"], "source": "brochure",
            }
    return points


def load_ground_truth_rows(cfg):
    rows = list(csv.DictReader(cfg["ground_truth_csv"].open()))
    parsed = []
    for r in rows:
        if (
            r["policy_id"] != cfg["policy_id"]
            or r["premium_payment_option"] != cfg["premium_payment_option"]
            or r["sum_assured_type"] != cfg["sum_assured_type"]
            or not r["yearly_premium"]
        ):
            continue
        parsed.append({
            "age": _age_from_row(r, cfg["scrape_date"]),
            "term": int(r["policy_term"]),
            "sum_assured": int(r["sum_assured"]),
            "premium": int(r["yearly_premium"]),
        })
    return parsed


def build_age_term_grid(brochure_points, ground_truth_rows, cfg):
    grid = dict(brochure_points)  # (age, term) -> point; scraped overwrites brochure below
    for r in ground_truth_rows:
        if r["sum_assured"] != cfg["reference_sum_assured"]:
            continue
        grid[(r["age"], r["term"])] = {
            "age": r["age"], "term": r["term"], "premium": r["premium"], "source": "scraped",
        }
    return sorted(grid.values(), key=lambda p: (p["age"], p["term"]))


def _empirical_ratios_by_age_band_and_sa(ground_truth_rows, cfg):
    """QA-only: real scraped ratio per (age_band, sum_assured), averaged across every
    term that has a same-age-and-SA sample (confirmed 2026-08 the ratio is constant
    across term within an age band, so averaging smooths sampling noise, doesn't blend
    genuinely different values). Used only to cross-check the rebate-table formula
    below, never as the multiplier's own source."""
    ratios = defaultdict(list)  # (age_band, sum_assured) -> [ratio, ...]
    by_age_term = defaultdict(dict)  # (age, term) -> {sum_assured: premium}
    for r in ground_truth_rows:
        by_age_term[(r["age"], r["term"])][r["sum_assured"]] = r["premium"]

    ref_sa = cfg["reference_sum_assured"]
    for (age, term), sa_premiums in by_age_term.items():
        if ref_sa not in sa_premiums:
            continue
        baseline = sa_premiums[ref_sa]
        for sa, premium in sa_premiums.items():
            if sa == ref_sa:
                continue
            ratios[(_age_band(age, cfg["age_bands"]), sa)].append(premium / baseline)
    return ratios


def _per_mille_checks_by_sa(ground_truth_rows, ref_sa):
    """QA-only, per_mille_of_sum_assured_rupees's equivalent of
    _empirical_ratios_by_age_band_and_sa() above: for every ground-truth row not at
    the reference SA, if a reference-SA row exists at the SAME (age, term), that pair
    is a genuine end-to-end check of compute_sa_adjustment()'s formula (not just the
    SA axis in isolation) - keyed by sum_assured so it can be attached per sa_band.
    Grouped by exact (age, term) match rather than averaged like the percent-type
    ratio check, since this formula subtracts an absolute Rupee amount rather than
    applying a pure ratio, so it isn't age/term-invariant the same way a ratio is -
    each sample carries its own age/term so a real mismatch can be traced to exactly
    which query it came from. Used only to cross-check the formula, never as its
    source."""
    by_age_term = defaultdict(dict)  # (age, term) -> {sum_assured: premium}
    for r in ground_truth_rows:
        by_age_term[(r["age"], r["term"])][r["sum_assured"]] = r["premium"]

    checks_by_sa = defaultdict(list)
    for (age, term), sa_premiums in by_age_term.items():
        if ref_sa not in sa_premiums:
            continue
        baseline = sa_premiums[ref_sa]
        for sa, actual in sa_premiums.items():
            if sa == ref_sa:
                continue
            checks_by_sa[sa].append({"age": age, "term": term, "baseline": baseline, "actual": actual})
    return checks_by_sa


def build_sa_scaling(ground_truth_rows, cfg):
    """sa_scaling comes directly from rebate_structures.high_sum_assured_rebate_table
    (load_rebate_table()) - a closed-form formula, not fit to scraped data, so it needs
    zero scraping to extend to any sum_assured the table's own bands cover. Dispatches
    on the table's own "type" - both formula shapes query/premium_lookup.py's
    compute_sa_adjustment() knows about get a qa_check cross-check against scraped
    cross-SA rows, when such rows exist (a sa_band with no matching scraped SA just
    gets an empty qa_check, not a failure - true for both types until a policy has
    ground truth at more than one sum_assured)."""
    table = load_rebate_table(cfg)
    rtype = table["type"]
    ref_sa = cfg["reference_sum_assured"]

    if rtype == "percent_of_tabular_premium":
        age_bands = build_sa_bands_for_type_a(table, cfg)
        empirical_ratios = _empirical_ratios_by_age_band_and_sa(ground_truth_rows, cfg)

        for band in age_bands:
            for sa_band in band["sa_bands"]:
                # QA only makes sense for a scraped SA we actually have empirical
                # ratios for - a sa_band with no matching scraped SA just gets no
                # qa_check, not a failure.
                checks = []
                for (b, sa), rs in sorted(empirical_ratios.items()):
                    if b != band["band"] or not (sa_band["min"] <= sa and (sa_band["max"] is None or sa <= sa_band["max"])):
                        continue
                    formula_multiplier = (sa / ref_sa) * (1 - sa_band["rebate_pct"] / 100)
                    empirical_multiplier = round(sum(rs) / len(rs), 4)
                    checks.append({
                        "sum_assured": sa,
                        "formula_multiplier": round(formula_multiplier, 4),
                        "empirical_multiplier": empirical_multiplier,
                        "sample_count": len(rs),
                        "match": abs(formula_multiplier - empirical_multiplier) < 0.005,
                    })
                sa_band["qa_check"] = checks

        return {"type": rtype, "reference_sum_assured": ref_sa, "age_bands": age_bands}

    if rtype == "per_mille_of_sum_assured_rupees":
        checks_by_sa = _per_mille_checks_by_sa(ground_truth_rows, ref_sa)
        sa_bands = []
        for raw_band in table["sa_bands"]:
            band = dict(raw_band)
            checks = []
            for sa, samples in sorted(checks_by_sa.items()):
                if not (band["min"] <= sa and (band["max"] is None or sa <= band["max"])):
                    continue
                for s in samples:
                    rebate_rs = (band["rate_per_mille"] / 1000) * sa
                    formula_premium = s["baseline"] * (sa / ref_sa) - rebate_rs
                    checks.append({
                        "sum_assured": sa,
                        "age": s["age"],
                        "term": s["term"],
                        "formula_premium": round(formula_premium, 2),
                        "actual_premium": s["actual"],
                        "match": abs(formula_premium - s["actual"]) < max(1.0, 0.005 * s["actual"]),
                    })
            band["qa_check"] = checks
            sa_bands.append(band)
        return {"type": rtype, "reference_sum_assured": ref_sa, "sa_bands": sa_bands}

    raise ValueError(
        f"{cfg['policy_id']}: rebate table type {rtype!r} has no build_sa_scaling branch yet - "
        f"add one above (and a matching formula in query/premium_lookup.py's "
        f"compute_sa_adjustment()) before building a lookup that needs it"
    )


def build_policy_entry(cfg):
    brochure_points = load_brochure_points(cfg)
    ground_truth_rows = load_ground_truth_rows(cfg)

    age_term_grid = build_age_term_grid(brochure_points, ground_truth_rows, cfg)
    sa_scaling = build_sa_scaling(ground_truth_rows, cfg)

    return {
        "premium_payment_option": cfg["premium_payment_option"],
        "sum_assured_type": cfg["sum_assured_type"],
        "age_term_grid": age_term_grid,
        "sa_scaling": sa_scaling,
        "built_from": {
            "layer1_brochure_points": len(brochure_points),
            "ground_truth_csv": str(cfg["ground_truth_csv"].relative_to(REPO_ROOT)),
            "ground_truth_rows_at_reference_sa": sum(
                1 for r in ground_truth_rows if r["sum_assured"] == cfg["reference_sum_assured"]
            ),
            "merged_age_term_points": len(age_term_grid),
        },
    }


def main():
    out = {}
    total_mismatches = 0

    for cfg in POLICIES:
        entry = build_policy_entry(cfg)
        out[cfg["policy_id"]] = entry
        sa_scaling = entry["sa_scaling"]

        print(f"{cfg['policy_id']}: {len(entry['age_term_grid'])} age/term points, "
              f"sa_scaling type={sa_scaling['type']} (zero scraping needed for this axis)")

        sa_band_groups = sa_scaling["age_bands"] if "age_bands" in sa_scaling else [{"band": None, "sa_bands": sa_scaling["sa_bands"]}]
        for band in sa_band_groups:
            for sa_band in band["sa_bands"]:
                max_str = f"<={sa_band['max']}" if sa_band["max"] is not None else "and above"
                label = f"{band['band']}: " if band["band"] else ""
                if sa_scaling["type"] == "percent_of_tabular_premium":
                    print(f"  {label}SA {sa_band['min']:>10} {max_str:>12} -> {sa_band['rebate_pct']}% rebate")
                else:
                    print(f"  {label}SA {sa_band['min']:>10} {max_str:>12} -> {sa_band['rate_per_mille']}%o of SA")
                for check in sa_band["qa_check"]:
                    status = "OK" if check["match"] else "MISMATCH"
                    if not check["match"]:
                        total_mismatches += 1
                    if "formula_multiplier" in check:
                        print(f"    QA @ SA {check['sum_assured']:>10}: formula={check['formula_multiplier']}x "
                              f"vs scraped={check['empirical_multiplier']}x (n={check['sample_count']}) [{status}]")
                    else:
                        print(f"    QA @ SA {check['sum_assured']:>10} (age={check['age']}, term={check['term']}): "
                              f"formula={check['formula_premium']} vs scraped={check['actual_premium']} [{status}]")

    OUT_PATH.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nWrote {OUT_PATH.relative_to(REPO_ROOT)}: {len(out)} polic{'y' if len(out) == 1 else 'ies'}")
    if total_mismatches:
        print(f"\n{total_mismatches} QA MISMATCH(ES) - a rebate-table formula disagrees with real "
              f"scraped data somewhere above. Investigate before trusting this build.")


if __name__ == "__main__":
    main()
