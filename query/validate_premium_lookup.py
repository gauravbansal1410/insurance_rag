#!/usr/bin/env python3
"""Leave-one-out validation for query/premium_lookup.py's bilinear age x term
interpolation, against a policy's own real (age, term) grid - before this design
gets wired into the live query pipeline. For each known point, hides it, re-runs
bilinear_lookup() against every other point, and compares the prediction to the
real value. A point with no usable bracket once removed (e.g. was itself the only
data at its age) is reported separately, not counted as a numeric error.

Usage: python3 query/validate_premium_lookup.py [policy_id]  (defaults to 876)
"""

import json
import sys
from pathlib import Path

from premium_lookup import bilinear_lookup

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOKUP_PATH = REPO_ROOT / "query" / "premium_lookup.json"


def main():
    policy_id = sys.argv[1] if len(sys.argv) > 1 else "876"
    lookup = json.loads(LOOKUP_PATH.read_text())[policy_id]
    grid = lookup["age_term_grid"]

    errors = []
    untestable = []
    for i, held_out in enumerate(grid):
        remaining = grid[:i] + grid[i + 1:]
        result = bilinear_lookup(remaining, held_out["age"], held_out["term"])
        if result is None:
            untestable.append(held_out)
            continue
        actual = held_out["premium"]
        predicted = result["premium"]
        pct_error = abs(predicted - actual) / actual * 100
        errors.append({
            "age": held_out["age"], "term": held_out["term"], "source": held_out["source"],
            "actual": actual, "predicted": round(predicted, 1), "pct_error": round(pct_error, 2),
        })

    errors.sort(key=lambda e: -e["pct_error"])

    print(f"policy {policy_id}: {len(errors)} points testable (had a usable bracket with the rest of the grid), "
          f"{len(untestable)} untestable (isolated, no other point could bracket them)\n")

    print(f"{'age':>4} {'term':>5} {'source':>9} {'actual':>10} {'predicted':>10} {'% error':>8}")
    for e in errors:
        print(f"{e['age']:>4} {e['term']:>5} {e['source']:>9} {e['actual']:>10} "
              f"{e['predicted']:>10} {e['pct_error']:>7.2f}%")

    pct_errors = [e["pct_error"] for e in errors]
    print(f"\nmean % error: {sum(pct_errors) / len(pct_errors):.2f}%")
    print(f"median % error: {sorted(pct_errors)[len(pct_errors) // 2]:.2f}%")
    print(f"max % error: {max(pct_errors):.2f}%  "
          f"(age={errors[0]['age']}, term={errors[0]['term']})")

    if untestable:
        print(f"\nuntestable points (no leave-one-out check possible):")
        for p in untestable:
            print(f"  age={p['age']}, term={p['term']}, source={p['source']}")


if __name__ == "__main__":
    main()
