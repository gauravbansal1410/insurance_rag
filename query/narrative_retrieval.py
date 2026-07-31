# Step 6 of the query pipeline (docs/query_architecture.md): Qdrant vector search over
# Layer 3 chunk-level records, restricted to the policy_ids that survived steps 3-5 (not
# an independent global search) - Qdrant does semantic search only inside an
# already-narrowed set, never the eligibility work itself.
#
# Query text is synthesized deterministically from the user's Group A concern_tags
# (docs/schema.md) rather than free text - no LLM call in this step, matches steps 3-5's
# fast/deterministic design. Embedded with voyage-law-2's input_type="query", not
# "document" - Voyage's asymmetric embedding scheme means query and document text for the
# same model are embedded differently for retrieval; embed_and_load_layer3.py used
# input_type="document" for the chunks, so a query embedded with "document" would
# silently degrade match quality rather than error.
#
# Client construction (env vars, timeout) lives in clients.py, shared with
# rerank_and_sort.py - this module only does retrieval.

import time
from voyageai.error import RateLimitError
from qdrant_client.models import Filter, FieldCondition, MatchAny
from concern_tags import synthesize_query_text

MODEL = "voyage-law-2"
COLLECTION = "insurance_rag_layer3"
DEFAULT_LIMIT = 20  # docs/query_architecture.md step 6: "top ~15-20 candidates"

# Same free-tier 3 RPM / 10K TPM cap embed_and_load_layer3.py hits (docs/schema.md Layer
# 3 caveats) - a single query embed call is tiny, but it shares the same rolling window as
# whatever else has called Voyage recently in this process/session.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 65


def embed_query(query_text, voyage_client, model=MODEL):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = voyage_client.embed([query_text], model=model, input_type="query")
            return result.embeddings[0]
        except RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(RETRY_BACKOFF_SECONDS)


def retrieve_narrative_chunks(concern_tags, policy_ids, voyage_client, qdrant_client,
                               collection=COLLECTION, limit=DEFAULT_LIMIT):
    """Returns Qdrant ScoredPoints (payload includes policy_id, category, section_name,
    chunk_text), vector-searched but filtered to only policy_ids - the set that already
    survived steps 3-5's eligibility/budget filtering."""
    query_text = synthesize_query_text(concern_tags)
    vector = embed_query(query_text, voyage_client)

    policy_filter = Filter(must=[FieldCondition(key="policy_id", match=MatchAny(any=policy_ids))])
    response = qdrant_client.query_points(
        collection_name=collection,
        query=vector,
        query_filter=policy_filter,
        limit=limit,
        with_payload=True,
    )
    return response.points


if __name__ == "__main__":
    from clients import make_voyage_client, make_qdrant_client

    voyage = make_voyage_client()
    qdrant = make_qdrant_client()

    print("--- Query text synthesis (no network call) ---")
    text = synthesize_query_text(["income_replacement", "medical_critical_illness_addon"])
    print(text)

    print("\n--- Retrieval: income_replacement + critical illness, restricted to 875/877/859 ---")
    hits = retrieve_narrative_chunks(
        concern_tags=["income_replacement", "medical_critical_illness_addon"],
        policy_ids=["875", "877", "859"],
        voyage_client=voyage,
        qdrant_client=qdrant,
        limit=10,
    )
    for h in hits:
        print(f"  score={h.score:.4f}  policy_id={h.payload['policy_id']}  section={h.payload['section_name']}")

    print("\n--- Filter check: restricting to a policy_id not in the corpus should return nothing ---")
    hits_empty = retrieve_narrative_chunks(
        concern_tags=["income_replacement"],
        policy_ids=["999999"],
        voyage_client=voyage,
        qdrant_client=qdrant,
        limit=10,
    )
    print(f"  {len(hits_empty)} hits (expected 0)")
