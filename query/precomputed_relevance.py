# Live-path lookup into chunking/precomputed_rerank_scores.json (see
# chunking/precompute_rerank_scores.py's docstring for what's precomputed and why, and the
# max-over-selected-tags approximation this relies on). Replaces a live step 7 Voyage
# rerank call with a pure in-memory lookup for any policy_id already covered by the
# precompute table - zero Voyage calls, no query-time latency.
#
# A policy_id is "covered" only if it appears anywhere in the table (i.e. its chunks have
# been precomputed against at least one tag) - not merely if a specific tag scored it, since
# every precomputed policy is scored against all 8 tags together, so partial-tag coverage
# shouldn't happen in practice but isn't assumed here.

import json
import os

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "chunking", "precomputed_rerank_scores.json")


def load_precomputed(path=DEFAULT_PATH):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _known_policy_ids(precomputed):
    known = set()
    for chunk_scores in precomputed.values():
        for entry in chunk_scores.values():
            known.add(entry["policy_id"])
    return known


def lookup_relevance_by_policy(concern_tags, policy_ids, precomputed):
    """Returns (scores, missing_policy_ids).
    scores: policy_id -> max precomputed score across the user's selected concern_tags and
    that policy's chunks (mirrors rerank_and_sort.max_rerank_score_by_policy's per-policy
    max-over-chunks aggregation, just sourced from the offline table instead of a live
    rerank call).
    missing_policy_ids: policy_ids not covered by the precompute table at all - the signal
    for the caller to fall back to a live rerank (or vector-score ordering) for just this
    subset, per the fallback design in docs/progress/20260731-progress.md."""
    known = _known_policy_ids(precomputed)
    missing = [pid for pid in policy_ids if pid not in known]

    scores = {}
    wanted = set(policy_ids)
    for tag in concern_tags:
        for chunk_id, entry in precomputed.get(tag, {}).items():
            pid = entry["policy_id"]
            if pid in wanted:
                scores[pid] = max(scores.get(pid, -1.0), entry["score"])

    return scores, missing


if __name__ == "__main__":
    precomputed = load_precomputed()
    print(f"Loaded precompute table: {len(precomputed)} tags, "
          f"{sum(len(v) for v in precomputed.values())} total (tag, chunk) rows, "
          f"{len(_known_policy_ids(precomputed))} distinct policy_ids covered.")

    scores, missing = lookup_relevance_by_policy(
        concern_tags=["income_replacement", "medical_critical_illness_addon"],
        policy_ids=["875", "876", "512N351V02", "512N350V02", "999999"],
        precomputed=precomputed,
    )
    print("scores:", scores)
    print("missing (not in precompute table):", missing)
