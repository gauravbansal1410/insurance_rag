#!/usr/bin/env python3
"""
Offline precompute for the query pipeline's step 7 (docs/query_architecture.md):
rerank-2.5-lite scores for every chunk passed in, against each of the 8 fixed Group A
concern_tags (query/concern_tags.py) - NOT against the 255 possible concern_tag
*combinations* a user could select. Reranking the full corpus against every combination
would take days at Voyage's free-tier pace; 8 independent tags is tractable and, crucially,
closed - a live query's selected tags just need a max() over these 8 precomputed rows, no
combinatorial blowup as new policies get added (only linear in chunks x 8).

**Approximation, deliberately not exact**: a user who selects 2+ concern_tags gets
narrative_retrieval.py's live path combining them into ONE joined query string
("...for {phrase1}; {phrase2}.") and reranking against that joined string - semantically
not identical to reranking against each phrase separately and taking the max, which is
what query/precomputed_relevance.py does with this precompute's output. This mirrors an
approximation already accepted elsewhere in this codebase
(rerank_and_sort.max_rerank_score_by_policy collapses per-chunk scores to per-policy via
max, not an average) - flagged here rather than presented as identical to the live
combined-query path.

Reuses query/rerank_and_sort.py's batching/retry logic directly (same MAX_BATCH_WORDS,
pacing, RateLimitError retry) rather than re-implementing it - the two paths hit the exact
same Voyage free-tier constraints.

Output: chunking/precomputed_rerank_scores.json, keyed tag -> chunk_id -> {policy_id,
score} - merged with any existing file (read-merge-write, overwrite by chunk_id), so
re-running for one new policy's chunks.json doesn't recompute or discard the rest of the
corpus. Idempotent in the same sense embed_and_load_layer3.py's upsert is.

Usage: python3 precompute_rerank_scores.py <chunks.json> [chunks.json ...]
"""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "query"))
from concern_tags import CONCERN_TAG_PHRASES
from rerank_and_sort import rerank_chunks
from clients import make_voyage_client

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "precomputed_rerank_scores.json")


class _ChunkAdapter:
    """rerank_chunks() reads c.payload[...] (Qdrant ScoredPoint's shape) - chunks loaded
    from a local chunks.json are plain dicts, so wrap them rather than changing
    rerank_chunks' interface (already validated against live Qdrant data)."""
    def __init__(self, chunk_dict):
        self.payload = chunk_dict


def load_chunks(paths):
    chunks = []
    for p in paths:
        with open(p) as f:
            data = json.load(f)
        chunks.extend(data["chunks"])
    return chunks


def load_existing(path=OUTPUT_PATH):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def precompute(chunks, voyage_client, tags=None):
    """Returns tag -> chunk_id -> {policy_id, score}. `tags` defaults to all 8; pass a
    subset for a smaller test run."""
    tags = tags or list(CONCERN_TAG_PHRASES)
    wrapped = [_ChunkAdapter(c) for c in chunks]
    result = {}
    for tag in tags:
        phrase = CONCERN_TAG_PHRASES[tag]
        print(f"Reranking {len(chunks)} chunks against tag '{tag}': \"{phrase}\"", flush=True)
        scored = rerank_chunks(phrase, wrapped, voyage_client)
        result[tag] = {
            chunks[s["chunk_index"]]["chunk_id"]: {
                "policy_id": chunks[s["chunk_index"]]["policy_id"],
                "score": s["relevance_score"],
            }
            for s in scored
        }
        print(f"  done: {len(result[tag])} chunks scored for '{tag}'", flush=True)
    return result


def merge(existing, new):
    for tag, chunk_scores in new.items():
        existing.setdefault(tag, {}).update(chunk_scores)
    return existing


if __name__ == "__main__":
    chunk_paths = sys.argv[1:]
    if not chunk_paths:
        print("Usage: precompute_rerank_scores.py <chunks.json> [chunks.json ...]")
        sys.exit(1)

    chunks = load_chunks(chunk_paths)
    print(f"Loaded {len(chunks)} chunks from {len(chunk_paths)} file(s).")

    voyage = make_voyage_client()
    new_scores = precompute(chunks, voyage)

    existing = load_existing()
    merged = merge(existing, new_scores)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(merged, f, indent=2)
    total_rows = sum(len(v) for v in merged.values())
    print(f"Wrote {OUTPUT_PATH}: {len(merged)} tags, {total_rows} total (tag, chunk) rows.")
