#!/usr/bin/env python3
"""Builds query/premium_lookup.json - v1 pilot, policy 876 only, regular/level only
(see query/premium_lookup.py for the interpolation design this feeds).

Usage: python3 query/build_premium_lookup.py

Merges two sources at the reference sum_assured (876's own sample-table baseline,
Rs 50L - also 876's sum_assured_min, so no rebate applies there):
  - extracted/layer1_876.json's own sample_illustrative_premiums (regular/level rows)
  - docs/progress/ground-truth/876_scraped_premiums.csv (regular/level, SA=reference only)
On an exact (age, term) collision, the scraped row wins (already cross-validated
against the brochure's own baseline - see docs/progress/20260801-progress.md - so
this is a tie-break rule, not a real conflict).

sa_scaling is now a direct formula parsed from Layer 1's own
rebate_structures.high_sum_assured_rebate_table (level_sum_assured /
regular_limited_premium), not an empirical table - corrected 2026-08-05 after
finding the earlier "doesn't quite match" conclusion was a band-boundary bug in
that check, not a real gap: sum_assured bands are lower-bound-inclusive (e.g.
"1Cr_to_2Cr" applies AT Rs 1Cr itself), and once read that way the formula
reproduces the scraped ratios exactly. This means the SA axis needs zero
scraping once a policy's rebate table is real structured data (not the placeholder
prose string still present on 6/7 term-assurance policies as of this date - see
docs/schema.md's extraction-rule caveats). The scraped CSV's cross-SA rows are
still used here, but only as a QA cross-check logged into the built artifact, not
as the source of the multiplier.
"""

import csv
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

_INDIAN_UNIT_MULTIPLIERS = {"L": 100_000, "Cr": 10_000_000}


def _parse_indian_amount(s):
    """'50L' -> 5000000, '1Cr' -> 10000000 - the unit shorthand used in
    rebate_structures.high_sum_assured_rebate_table's own band-name keys."""
    m = re.match(r"^(\d+(?:\.\d+)?)(L|Cr)$", s)
    if not m:
        raise ValueError(f"unrecognized Indian amount format: {s!r}")
    return int(float(m.group(1)) * _INDIAN_UNIT_MULTIPLIERS[m.group(2)])


def _parse_sa_band_key(key):
    """'50L_to_1Cr' -> (5000000, 10000000); '5Cr_and_above' -> (50000000, None).
    Bands are lower-bound-inclusive - confirmed 2026-08-05 by checking which
    band's rebate_pct actually reproduces the scraped ratios at exactly Rs 1Cr
    and Rs 2Cr (the band named for that amount, not the one below it)."""
    if key.endswith("_and_above"):
        return _parse_indian_amount(key[: -len("_and_above")]), None
    lo, hi = key.split("_to_")
    return _parse_indian_amount(lo), _parse_indian_amount(hi)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAYER1_PATH = REPO_ROOT / "extracted" / "layer1_876.json"
GROUND_TRUTH_CSV = REPO_ROOT / "docs" / "progress" / "ground-truth" / "876_scraped_premiums.csv"
OUT_PATH = REPO_ROOT / "query" / "premium_lookup.json"

POLICY_ID = "876"
REFERENCE_SUM_ASSURED = 5_000_000  # 876's own sum_assured_min - 0% rebate band
PREMIUM_PAYMENT_OPTION = "regular"
SUM_ASSURED_TYPE = "level"

# Matches rebate_structures.high_sum_assured_rebate_table's own age bands for
# level_sum_assured / regular_limited_premium (docs/schema.md, extracted/layer1_876.json).
AGE_BANDS = [
    {"band": "up_to_30", "age_min": 0, "age_max": 30},
    {"band": "31_to_45", "age_min": 31, "age_max": 999},
]

# Maps this build's fixed scope (module-level constants above) to
# rebate_structures.high_sum_assured_rebate_table's own key names.
REBATE_SA_TYPE_KEY = {"level": "level_sum_assured", "increasing": "increasing_sum_assured"}[SUM_ASSURED_TYPE]
REBATE_PREMIUM_TYPE_KEY = {"regular": "regular_limited_premium", "single": "single_premium"}[PREMIUM_PAYMENT_OPTION]

# CSV was scraped 2026-08-01 (docs/progress/ground-truth/876_scraped_premiums.csv) -
# fixed here rather than read from the system clock, since age depends on this date.
SCRAPE_DATE = date(2026, 8, 1)


def _age_from_dob(dob_str):
    d, m, y = (int(p) for p in dob_str.split("/"))
    age = SCRAPE_DATE.year - y
    if (SCRAPE_DATE.month, SCRAPE_DATE.day) < (m, d):
        age -= 1
    return age


def _age_from_row(row):
    if row["dob"]:
        return _age_from_dob(row["dob"])
    m = re.search(r"intended_age (\d+)", row["notes"])
    if m is None:
        raise ValueError(f"row has no dob and no intended_age note: {row}")
    return int(m.group(1))


def _age_band(age):
    for b in AGE_BANDS:
        if b["age_min"] <= age <= b["age_max"]:
            return b["band"]
    raise ValueError(f"age {age} doesn't fall in any known age band")


def load_rebate_bands():
    """Parses rebate_structures.high_sum_assured_rebate_table into, per age band,
    a list of {"min", "max" (None = unbounded), "rebate_pct"} sa_bands - the direct
    formula source for sa_multiplier(), not an empirical fit. Raises if the field
    is still the old placeholder/prose-string shape (fine to fail loudly here -
    this build script is 876-only; docs/schema.md's extraction-rule caveats note
    the other 6 term-assurance policies aren't in this shape yet)."""
    layer1 = json.loads(LAYER1_PATH.read_text())["layer1"]
    table = layer1["rebate_structures"]["high_sum_assured_rebate_table"]
    if not isinstance(table, dict):
        raise TypeError(
            "rebate_structures.high_sum_assured_rebate_table is not structured data "
            "(still the old placeholder/prose string?) - can't build a formula from it"
        )
    by_age_band_key = table[REBATE_SA_TYPE_KEY][REBATE_PREMIUM_TYPE_KEY]

    age_bands_out = []
    for b in AGE_BANDS:
        sa_band_pcts = by_age_band_key[b["band"]]
        sa_bands = []
        for sa_key, pct in sa_band_pcts.items():
            lo, hi = _parse_sa_band_key(sa_key)
            sa_bands.append({"min": lo, "max": hi, "rebate_pct": pct})
        sa_bands.sort(key=lambda x: x["min"])
        age_bands_out.append({"band": b["band"], "age_min": b["age_min"], "age_max": b["age_max"], "sa_bands": sa_bands})
    return age_bands_out


def load_brochure_points():
    layer1 = json.loads(LAYER1_PATH.read_text())["layer1"]
    points = {}
    for row in layer1["sample_illustrative_premiums"]:
        if (
            row["premium_payment_option"] == PREMIUM_PAYMENT_OPTION
            and row.get("sum_assured_type", "level") == SUM_ASSURED_TYPE
            and row["sum_assured"] == REFERENCE_SUM_ASSURED
        ):
            points[(row["age"], row["term"])] = {
                "age": row["age"], "term": row["term"],
                "premium": row["premium_amount"], "source": "brochure",
            }
    return points


def load_ground_truth_rows():
    rows = list(csv.DictReader(GROUND_TRUTH_CSV.open()))
    parsed = []
    for r in rows:
        if (
            r["policy_id"] != POLICY_ID
            or r["premium_payment_option"] != PREMIUM_PAYMENT_OPTION
            or r["sum_assured_type"] != SUM_ASSURED_TYPE
            or not r["yearly_premium"]
        ):
            continue
        parsed.append({
            "age": _age_from_row(r),
            "term": int(r["policy_term"]),
            "sum_assured": int(r["sum_assured"]),
            "premium": int(r["yearly_premium"]),
        })
    return parsed


def build_age_term_grid(brochure_points, ground_truth_rows):
    grid = dict(brochure_points)  # (age, term) -> point; scraped overwrites brochure below
    for r in ground_truth_rows:
        if r["sum_assured"] != REFERENCE_SUM_ASSURED:
            continue
        grid[(r["age"], r["term"])] = {
            "age": r["age"], "term": r["term"], "premium": r["premium"], "source": "scraped",
        }
    return sorted(grid.values(), key=lambda p: (p["age"], p["term"]))


def _empirical_ratios_by_age_band_and_sa(ground_truth_rows):
    """QA-only: real scraped ratio per (age_band, sum_assured), averaged across every
    term that has a same-age-and-SA sample (confirmed 2026-08 the ratio is constant
    across term within an age band, so averaging smooths sampling noise, doesn't blend
    genuinely different values). Used only to cross-check the rebate-table formula
    below, never as the multiplier's own source."""
    ratios = defaultdict(list)  # (age_band, sum_assured) -> [ratio, ...]
    by_age_term = defaultdict(dict)  # (age, term) -> {sum_assured: premium}
    for r in ground_truth_rows:
        by_age_term[(r["age"], r["term"])][r["sum_assured"]] = r["premium"]

    for (age, term), sa_premiums in by_age_term.items():
        if REFERENCE_SUM_ASSURED not in sa_premiums:
            continue
        baseline = sa_premiums[REFERENCE_SUM_ASSURED]
        for sa, premium in sa_premiums.items():
            if sa == REFERENCE_SUM_ASSURED:
                continue
            ratios[(_age_band(age), sa)].append(premium / baseline)
    return ratios


def build_sa_scaling(ground_truth_rows):
    """sa_scaling multiplier now comes directly from rebate_structures.high_sum_assured_rebate_table
    (load_rebate_bands()) - a closed-form formula, not fit to scraped data, so it needs
    zero scraping to extend to any sum_assured the table's own bands cover (corrected
    2026-08-05 - see query/premium_lookup.py's module docstring for why the earlier
    empirical-only approach was a mistaken workaround, not a real requirement).
    Scraped ground truth is still used here, but only to attach a `qa_check` cross-check
    onto each sa_band, flagging (not silently swallowing) any real disagreement between
    the formula and what LIC's live calculator actually returned."""
    age_bands = load_rebate_bands()
    empirical_ratios = _empirical_ratios_by_age_band_and_sa(ground_truth_rows)

    for band in age_bands:
        for sa_band in band["sa_bands"]:
            # QA only makes sense for a scraped SA we actually have empirical ratios
            # for (876's ground truth only covers exactly Rs 1Cr / Rs 2Cr) - a sa_band
            # with no matching scraped SA just gets no qa_check, not a failure.
            checks = []
            for (b, sa), rs in sorted(empirical_ratios.items()):
                if b != band["band"] or not (sa_band["min"] <= sa and (sa_band["max"] is None or sa < sa_band["max"])):
                    continue
                formula_multiplier = (sa / REFERENCE_SUM_ASSURED) * (1 - sa_band["rebate_pct"] / 100)
                empirical_multiplier = round(sum(rs) / len(rs), 4)
                checks.append({
                    "sum_assured": sa,
                    "formula_multiplier": round(formula_multiplier, 4),
                    "empirical_multiplier": empirical_multiplier,
                    "sample_count": len(rs),
                    "match": abs(formula_multiplier - empirical_multiplier) < 0.005,
                })
            sa_band["qa_check"] = checks

    return {"reference_sum_assured": REFERENCE_SUM_ASSURED, "age_bands": age_bands}


def main():
    brochure_points = load_brochure_points()
    ground_truth_rows = load_ground_truth_rows()

    age_term_grid = build_age_term_grid(brochure_points, ground_truth_rows)
    sa_scaling = build_sa_scaling(ground_truth_rows)

    out = {
        POLICY_ID: {
            "premium_payment_option": PREMIUM_PAYMENT_OPTION,
            "sum_assured_type": SUM_ASSURED_TYPE,
            "age_term_grid": age_term_grid,
            "sa_scaling": sa_scaling,
            "built_from": {
                "layer1_brochure_points": len(brochure_points),
                "ground_truth_csv": str(GROUND_TRUTH_CSV.relative_to(REPO_ROOT)),
                "ground_truth_rows_at_reference_sa": sum(
                    1 for r in ground_truth_rows if r["sum_assured"] == REFERENCE_SUM_ASSURED
                ),
                "merged_age_term_points": len(age_term_grid),
            },
        }
    }

    OUT_PATH.write_text(json.dumps(out, indent=2) + "\n")
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)}: "
          f"{len(age_term_grid)} age/term points, "
          f"sa_scaling now a formula from rebate_structures across "
          f"{len(sa_scaling['age_bands'])} age bands (zero scraping needed for this axis)")
    mismatches = 0
    for b in sa_scaling["age_bands"]:
        for sa_band in b["sa_bands"]:
            rebate_pct = sa_band["rebate_pct"]
            max_str = f"<{sa_band['max']}" if sa_band["max"] is not None else "and above"
            print(f"  {b['band']}: SA {sa_band['min']:>10} {max_str:>12} -> {rebate_pct}% rebate")
            for check in sa_band["qa_check"]:
                status = "OK" if check["match"] else "MISMATCH"
                if not check["match"]:
                    mismatches += 1
                print(f"    QA @ SA {check['sum_assured']:>10}: formula={check['formula_multiplier']}x "
                      f"vs scraped={check['empirical_multiplier']}x (n={check['sample_count']}) [{status}]")
    if mismatches:
        print(f"\n{mismatches} QA MISMATCH(ES) - the rebate-table formula disagrees with real "
              f"scraped data somewhere above. Investigate before trusting this build.")


if __name__ == "__main__":
    main()
