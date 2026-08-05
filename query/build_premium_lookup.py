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

sa_scaling multipliers are derived directly from the scraped CSV's cross-SA rows
(the only source with more than one sum_assured), averaged per age band - see
docs/schema.md's rebate_structures age bands (up_to_30 / 31_to_45 for level SA +
regular/limited premium) for why those exact band boundaries were chosen, not
guessed.
"""

import csv
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

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


def build_sa_scaling(ground_truth_rows):
    """Empirical multiplier per (age_band, sum_assured), averaged across every
    term that has a same-age-and-SA sample - confirmed 2026-08 that the ratio is
    constant across term within an age band, so averaging just smooths sampling
    noise, it isn't blending genuinely different values."""
    # (age_band, sum_assured) -> [ratio, ratio, ...]
    ratios = defaultdict(list)
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

    age_bands_out = []
    for b in AGE_BANDS:
        points = []
        for (band, sa), rs in sorted(ratios.items()):
            if band != b["band"]:
                continue
            points.append({
                "sum_assured": sa,
                "multiplier": round(sum(rs) / len(rs), 4),
                "sample_count": len(rs),
            })
        age_bands_out.append({
            "band": b["band"], "age_min": b["age_min"], "age_max": b["age_max"], "points": points,
        })
    return {"reference_sum_assured": REFERENCE_SUM_ASSURED, "age_bands": age_bands_out}


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
          f"{sum(len(b['points']) for b in sa_scaling['age_bands'])} sa_scaling points "
          f"across {len(sa_scaling['age_bands'])} age bands")
    for b in sa_scaling["age_bands"]:
        for p in b["points"]:
            print(f"  {b['band']}: SA {p['sum_assured']:>10} -> {p['multiplier']}x "
                  f"(n={p['sample_count']})")


if __name__ == "__main__":
    main()
