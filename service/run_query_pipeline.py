# Sequences query pipeline steps 3-8 (docs/query_architecture.md) for one profile - the
# actual orchestration logic behind service/main.py's single HTTP endpoint. Kept separate
# from the FastAPI wiring so it's callable/testable directly (dependency-injected clients,
# same pattern every query/ module already uses), not only through a running server.
#
# Single "run query" function/endpoint, not one per step - steps 3-8 are already a fixed,
# tightly sequential chain in the Python code (each step's output directly feeds the next),
# so splitting that across separate n8n-visible calls would just re-expose an ordering n8n
# can't meaningfully reorder anyway (docs/query_architecture.md's "Runtime orchestration"
# section).

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "query"))

from eligibility_filter import apply_fallback
from premium_interpolation import filter_by_budget
from narrative_retrieval import retrieve_narrative_chunks
from rerank_and_sort import rank_candidates, rerank_chunks, max_rerank_score_by_policy
from precomputed_relevance import lookup_relevance_by_policy
from narrative_generation import generate_narrative, plan_name_for
from concern_tags import synthesize_query_text

TOP_N = 3
GROUNDING_CHUNKS_PER_POLICY = 9  # a term-assurance policy's own full chunk count - see docs/schema.md


def run_query(profile, layer1_records, layer2_records, precomputed, voyage_client, qdrant_client):
    """profile: dict with age, sum_assured, term, premium_payment_option, budget,
    concern_tags, optionally sum_assured_type. Returns a dict with fallback_tier, excluded
    (step 5's exclude-and-log entries), top3 (ranked candidates), and narrative (step 8's
    generated text) - never raises on a bad candidate, matching every step's own
    exclude-and-log design (the query still completes)."""

    # Steps 3-4: eligibility filter + two-tier fallback.
    eligible = apply_fallback(profile, layer2_records)

    # Step 5: premium interpolation + budget filter.
    survivors, excluded_log = filter_by_budget(eligible["results"], profile, layer1_records)

    policy_ids = [s["policy_id"] for s in survivors]

    # Step 7, precomputed lookup first (the routine, zero-Voyage-call path).
    scores, missing = lookup_relevance_by_policy(profile["concern_tags"], policy_ids, precomputed)

    # Step 6 + live step 7, only for the policies the precompute table doesn't cover yet -
    # the rare safety-net path per docs/query_architecture.md, not the common case. A
    # missing policy that still has no retrievable chunks stays unscored, flagged by
    # rank_candidates rather than silently guessed at.
    if missing:
        fallback_chunks = retrieve_narrative_chunks(
            profile["concern_tags"], missing, voyage_client, qdrant_client, limit=20
        )
        if fallback_chunks:
            query_text = synthesize_query_text(profile["concern_tags"])
            reranked = rerank_chunks(query_text, fallback_chunks, voyage_client)
            scores.update(max_rerank_score_by_policy(fallback_chunks, reranked))

    # Sort: concern_match_count desc -> relevance tier -> premium asc (rerank_and_sort.py).
    ranked = rank_candidates(survivors, scores)
    top3 = ranked[:TOP_N]

    # Hard guard, not a prompt-level ask: confirmed 2026-08-01 that calling
    # generate_narrative() with zero candidates (every eligible policy excluded at step 5 -
    # e.g. a term with no matching sample-premium table row, as happened with a 40-year
    # term none of this corpus's tables cover) produces an EMPTY candidates section in the
    # prompt, and Gemini filled the gap by hallucinating three entirely fake, non-LIC
    # insurance products instead of refusing. A prompt instruction alone can't be trusted to
    # refuse gracefully when handed nothing to ground on - detect this case in code and
    # never call Gemini at all when there's nothing real to describe.
    if not top3:
        return {
            "fallback_tier": eligible["fallback_tier"],
            "excluded": excluded_log,
            "top3": [],
            "narrative": (
                "No eligible plans were found for this profile within your stated budget. "
                "This can happen if the requested term, sum assured, or budget falls outside "
                "what's available in the current policy set - try adjusting one of those "
                "(e.g. a shorter term or a higher budget) and asking again."
            ),
        }

    # Step 6 again, this time for step 8's grounding text - a separate purpose (narrative
    # grounding, not relevance scoring) from the lookup/fallback above, so re-fetched
    # per-policy rather than reusing whatever fallback_chunks happened to retrieve.
    chunks_by_policy = {
        c["policy_id"]: retrieve_narrative_chunks(
            profile["concern_tags"], [c["policy_id"]], voyage_client, qdrant_client,
            limit=GROUNDING_CHUNKS_PER_POLICY,
        )
        for c in top3
    }

    # Step 8: narrative generation.
    narrative = generate_narrative(profile, top3, layer1_records, layer2_records, chunks_by_policy)

    return {
        "fallback_tier": eligible["fallback_tier"],
        "excluded": excluded_log,
        "top3": [
            {
                "rank": i + 1,
                "policy_id": c["policy_id"],
                "plan_name": plan_name_for(layer1_records[c["policy_id"]], c["policy_id"]),
                "premium_amount": c["premium_amount"],
                "concern_match_count": c["concern_match_count"],
                "relevance_tier": c.get("relevance_tier"),
                "rerank_score": c.get("rerank_score"),
            }
            for i, c in enumerate(top3)
        ],
        "narrative": narrative,
    }
