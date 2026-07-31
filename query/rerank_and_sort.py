# Step 7 of the query pipeline (docs/query_architecture.md): Voyage rerank-2.5-lite
# re-scores step 6's retrieved chunks for semantic relevance, then candidates (policies -
# concern_match_count and premium are both per-policy fields, unlike step 6's per-chunk
# hits) are sorted: concern_match_count (step 3) descending is the primary key; within a
# concern_match_count block, candidates are grouped into relevance tiers by rerank score
# (tolerance band, placeholder 0.05 - UNCALIBRATED, see docs' open questions); within a
# tier, sorted by interpolated premium (step 5) ascending. Not one blended weighted score
# - see the doc's rationale for why (keeps ranking explainable for a future LLM-judge /
# golden-set eval).

import time
from voyageai.error import RateLimitError

RERANK_MODEL = "rerank-2.5-lite"
TIER_TOLERANCE = 0.05  # placeholder, uncalibrated - docs/query_architecture.md open questions

# Client construction (env vars, timeout) lives in clients.py, shared with
# narrative_retrieval.py - this module only does reranking + sorting.

# Same free-tier 3 RPM / 10K TPM cap embed_and_load_layer3.py hits. Unlike the query embed
# in narrative_retrieval.py (one short string, no batching needed), a single rerank call
# sends the query PLUS every retrieved chunk's full text - confirmed 2026-07-31 that 20
# full-section chunks (~26K words) is ~33K estimated tokens, over 3x the 10K TPM cap in one
# request. Retrying with backoff can't fix an over-limit single request, only a
# transiently-full window - so this batches the same way embed_and_load_layer3.py does,
# and paces batches a full 65s apart (TPM is a rolling 60s window, not per-request).
# MAX_RETRIES bumped from 3 to 6 (2026-07-31, confirmed via real timestamps, not
# turn-counted estimates): a live run crashed after exhausting 3 retries in ~6.5 real
# minutes while the account was still under rate-limit contention from earlier testing in
# the same session - 3 attempts x 65s backoff (~2 min of retry headroom) wasn't enough to
# outlast that contention. More headroom costs only wall-clock time, not money.
MAX_BATCH_WORDS = 5000
SECONDS_BETWEEN_BATCHES = 65
MAX_RETRIES = 6
RETRY_BACKOFF_SECONDS = 65


def _batch_chunks(chunks, max_words):
    """Same greedy word-budget batching as embed_and_load_layer3.py's batch_chunks."""
    batches, current, current_words = [], [], 0
    for c in chunks:
        w = c.payload["word_count"]
        if current and current_words + w > max_words:
            batches.append(current)
            current, current_words = [], 0
        current.append(c)
        current_words += w
    if current:
        batches.append(current)
    return batches


def rerank_chunks(query_text, chunks, voyage_client, model=RERANK_MODEL):
    """chunks: Qdrant ScoredPoints from narrative_retrieval.retrieve_narrative_chunks.
    Returns a list of {"chunk_index": i, "relevance_score": float} - i indexes back into
    `chunks` directly (already remapped across batches), not Voyage's own per-batch
    RerankingObject.results[].index, which only makes sense within one batch's document
    list."""
    batches = _batch_chunks(chunks, MAX_BATCH_WORDS)
    scored = []
    offset = 0
    for i, batch in enumerate(batches):
        documents = [c.payload["chunk_text"] for c in batch]
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result = voyage_client.rerank(query=query_text, documents=documents, model=model)
                break
            except RateLimitError:
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(RETRY_BACKOFF_SECONDS)
        for r in result.results:
            scored.append({"chunk_index": offset + r.index, "relevance_score": r.relevance_score})
        offset += len(batch)
        if i < len(batches) - 1:
            time.sleep(SECONDS_BETWEEN_BATCHES)
    return scored


def max_rerank_score_by_policy(chunks, scored):
    """Collapses per-chunk rerank scores (rerank_chunks' output) to one per policy_id - a
    policy's relevance is judged by its single most relevant retrieved chunk, not an
    average, so a policy isn't penalized for having some chunks that are
    boilerplate/irrelevant to this query."""
    scores = {}
    for r in scored:
        policy_id = chunks[r["chunk_index"]].payload["policy_id"]
        scores[policy_id] = max(scores.get(policy_id, -1.0), r["relevance_score"])
    return scores


def _assign_tiers(candidates, tolerance):
    """candidates already sorted by rerank_score descending. Cuts a new tier whenever the
    gap between consecutive scores exceeds tolerance - avoids the transitive-closeness
    ambiguity of "within tolerance of each other" (A~B, B~C doesn't imply A~C), the
    simplest defensible reading given the docs don't specify a chaining rule."""
    tier = 0
    for i, c in enumerate(candidates):
        if i > 0 and candidates[i - 1]["rerank_score"] - c["rerank_score"] > tolerance:
            tier += 1
        c["relevance_tier"] = tier
    return candidates


def rank_candidates(budget_survivors, rerank_scores_by_policy, tolerance=TIER_TOLERANCE):
    """budget_survivors: premium_interpolation.filter_by_budget's survivors (each has
    policy_id, concern_match_count, premium_amount already). rerank_scores_by_policy:
    max_rerank_score_by_policy's output. A survivor with no retrieved chunk (step 6's
    limit may not have surfaced every surviving policy_id) gets rerank_score=None, logged
    and sorted last within its concern_match_count block - flagged rather than silently
    scored as if it had a real (e.g. zero) relevance signal."""
    candidates = []
    unscored = []
    for s in budget_survivors:
        pid = s["policy_id"]
        if pid not in rerank_scores_by_policy:
            unscored.append({**s, "rerank_score": None, "relevance_tier": None})
            continue
        candidates.append({**s, "rerank_score": rerank_scores_by_policy[pid]})

    # Group by concern_match_count (desc), tier + premium-sort within each group.
    by_count = {}
    for c in candidates:
        by_count.setdefault(c["concern_match_count"], []).append(c)

    ranked = []
    for count in sorted(by_count, reverse=True):
        group = sorted(by_count[count], key=lambda c: c["rerank_score"], reverse=True)
        group = _assign_tiers(group, tolerance)
        group.sort(key=lambda c: (c["relevance_tier"], c["premium_amount"]))
        ranked.extend(group)

    if unscored:
        unscored.sort(key=lambda c: (-c["concern_match_count"], c["premium_amount"]))
    return ranked + unscored


if __name__ == "__main__":
    from clients import make_voyage_client, make_qdrant_client
    from load_extracted import load_layer1, load_layer2
    from eligibility_filter import apply_fallback
    from premium_interpolation import filter_by_budget
    from narrative_retrieval import retrieve_narrative_chunks, synthesize_query_text

    voyage = make_voyage_client()
    qdrant = make_qdrant_client()

    layer1 = load_layer1()
    layer2 = load_layer2()

    print("--- End-to-end steps 3-7: income_replacement + critical illness, age 30, SA 50L, term 20, regular ---")
    profile = {
        "age": 30, "sum_assured": 5000000, "term": 20,
        "concern_tags": ["income_replacement", "medical_critical_illness_addon"],
        "premium_payment_option": "regular", "budget": 10000,
    }
    eligible = apply_fallback(profile, layer2)
    print(f"step 3/4: tier={eligible['fallback_tier']}, {len(eligible['results'])} eligible")

    survivors, excluded_log = filter_by_budget(eligible["results"], profile, layer1)
    print(f"step 5: {len(survivors)} within budget, {len(excluded_log)} excluded")
    for e in excluded_log:
        print(f"  excluded {e['policy_id']}: {e['reason']}")

    policy_ids = [s["policy_id"] for s in survivors]
    chunks = retrieve_narrative_chunks(profile["concern_tags"], policy_ids, voyage, qdrant, limit=20)
    print(f"step 6: retrieved {len(chunks)} chunks across {len(set(c.payload['policy_id'] for c in chunks))} policies")

    query_text = synthesize_query_text(profile["concern_tags"])
    reranked = rerank_chunks(query_text, chunks, voyage)
    scores_by_policy = max_rerank_score_by_policy(chunks, reranked)
    print(f"step 7 rerank scores by policy: {scores_by_policy}")

    ranked = rank_candidates(survivors, scores_by_policy)
    print("\nfinal order:")
    for c in ranked:
        print(f"  {c['policy_id']}: concern_match_count={c['concern_match_count']} "
              f"tier={c['relevance_tier']} rerank_score={c['rerank_score']} "
              f"premium={c['premium_amount']}")
