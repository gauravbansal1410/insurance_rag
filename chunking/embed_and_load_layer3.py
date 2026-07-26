#!/usr/bin/env python3
"""
Layer 3 load: embeds chunk_text from chunking/chunks_<policy_id>.json files
with Voyage voyage-law-2 and upserts into a single Qdrant collection shared
across all categories (filtered by policy_id/category at query time, same
pattern Layer 2's Group C fields already use - see docs/schema.md).

Reads VOYAGE_API_KEY and QDRANT_URL from the environment directly, not from
.env - run `set -a && source .env && set +a` first, same convention as
run_layer1_extraction.py / run_layer2_derivation.py. QDRANT_URL is expected
to be the SSH-tunneled loopback address (http://localhost:6333), since
Qdrant on the Oracle VM is bound to 127.0.0.1 only - see docs/infra-baseline.md.

Point ID is a uuid5 derived deterministically from chunk_id (Qdrant requires
int or UUID point ids, chunk_id strings like "859_PART_C" aren't valid as-is)
- re-running this script on the same chunk_id always upserts the same point
rather than creating a duplicate.

Batches the embed calls: a Voyage account with no payment method on file is
capped at 3 RPM / 10K TPM (confirmed 2026-07-26 - a single request covering
all 18 chunks across 859+877, ~24K estimated tokens, hit RateLimitError).
TPM is a rolling window, not a per-request reset - a first attempt spacing
batches 21s apart (long enough for the 3 RPM cap alone) still hit
RateLimitError on the second batch, because the first batch's tokens hadn't
rolled out of the 60s TPM window yet. Batches are grouped under a
conservative word budget (proxy for token count, no local tokenizer
dependency) and paced a full 65s apart, with a retry-with-backoff fallback
in case the window still misaligns - rather than asking for a payment
method to be added just to hit these free-tier numbers, which would
conflict with the project's $0 cost target.

Usage: python3 embed_and_load_layer3.py <collection_name> <chunks.json> [chunks.json ...]
"""
import sys, os, json, uuid, time
import voyageai
from voyageai.error import RateLimitError
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance

MODEL = "voyage-law-2"
VECTOR_SIZE = 1024  # voyage-law-2 output dimension, confirmed 2026-07-26
CHUNK_ID_NAMESPACE = uuid.UUID("a3f5c9d2-1b4e-4a7f-9c3d-6e8f1a2b3c4d")  # fixed, arbitrary - just needs to be stable across runs

MAX_BATCH_WORDS = 5000   # conservative vs. the 10K TPM cap (~1.3 tokens/word)
SECONDS_BETWEEN_BATCHES = 65  # TPM is a rolling 60s window, not per-request - needs a full window, not just the 3 RPM spacing
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 65


def batch_chunks(chunks, max_words):
    batches, current, current_words = [], [], 0
    for c in chunks:
        w = c["word_count"]
        if current and current_words + w > max_words:
            batches.append(current)
            current, current_words = [], 0
        current.append(c)
        current_words += w
    if current:
        batches.append(current)
    return batches


def load_chunks(paths):
    chunks = []
    for p in paths:
        with open(p) as f:
            data = json.load(f)
        chunks.extend(data["chunks"])
    return chunks


def ensure_collection(client, name):
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def main():
    collection_name = sys.argv[1]
    chunk_paths = sys.argv[2:]
    if not chunk_paths:
        print("Usage: embed_and_load_layer3.py <collection_name> <chunks.json> [chunks.json ...]")
        sys.exit(1)

    voyage_key = os.environ["VOYAGE_API_KEY"]
    qdrant_url = os.environ["QDRANT_URL"]

    chunks = load_chunks(chunk_paths)
    print(f"Loaded {len(chunks)} chunks from {len(chunk_paths)} file(s).")

    voyage = voyageai.Client(api_key=voyage_key)
    batches = batch_chunks(chunks, MAX_BATCH_WORDS)
    print(f"Split into {len(batches)} batch(es) (<= {MAX_BATCH_WORDS} words each) for the 3 RPM / 10K TPM free-tier cap.")

    embeddings = []
    for i, batch in enumerate(batches):
        texts = [c["chunk_text"] for c in batch]
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                embed_result = voyage.embed(texts, model=MODEL, input_type="document")
                break
            except RateLimitError:
                if attempt == MAX_RETRIES:
                    raise
                print(f"  Batch {i + 1}/{len(batches)}: rate-limited, retry {attempt}/{MAX_RETRIES} after {RETRY_BACKOFF_SECONDS}s...")
                time.sleep(RETRY_BACKOFF_SECONDS)
        embeddings.extend(embed_result.embeddings)
        print(f"  Batch {i + 1}/{len(batches)}: embedded {len(batch)} chunks.")
        if i < len(batches) - 1:
            time.sleep(SECONDS_BETWEEN_BATCHES)

    if len(embeddings) != len(chunks):
        print(f"ERROR: embedded {len(embeddings)} vectors for {len(chunks)} chunks - mismatch, aborting.")
        sys.exit(1)
    print(f"Embedded {len(embeddings)} chunks via {MODEL} (dim={len(embeddings[0])}).")

    qdrant = QdrantClient(url=qdrant_url)
    ensure_collection(qdrant, collection_name)

    points = []
    for chunk, vector in zip(chunks, embeddings):
        point_id = str(uuid.uuid5(CHUNK_ID_NAMESPACE, chunk["chunk_id"]))
        payload = {k: v for k, v in chunk.items() if k != "chunk_text"}
        payload["chunk_text"] = chunk["chunk_text"]
        points.append(PointStruct(id=point_id, vector=vector, payload=payload))

    result = qdrant.upsert(collection_name=collection_name, points=points, wait=True)
    if result.status != "completed":
        print(f"ERROR: upsert did not complete cleanly - status: {result.status}")
        sys.exit(1)

    count = qdrant.count(collection_name=collection_name, exact=True).count
    print(f"Upserted {len(points)} points, collection now has {count} points total")


if __name__ == "__main__":
    main()
