# Shared client construction for the query pipeline's Voyage/Qdrant-calling steps
# (narrative_retrieval.py step 6, rerank_and_sort.py step 7) - kept out of those modules
# so each stays focused on its own step's logic, not env-var reading or client config.
#
# Reads VOYAGE_API_KEY/QDRANT_URL from the environment directly (same
# `set -a && source .env && set +a` convention as embed_and_load_layer3.py), not from
# .env. QDRANT_URL must be the SSH-tunneled loopback address - run the qdrant-tunnel
# alias first (docs/infra-baseline.md).
#
# REQUEST_TIMEOUT_SECONDS: neither client raises promptly on a stalled connection without
# an explicit timeout - confirmed 2026-07-31 when a debug script hung 15+ minutes on a
# healthy tunnel with 0% CPU, no error, nothing to retry against. A real request-serving
# path must fail fast into the caller's own retry/backoff, not hang indefinitely.

import os
import voyageai
from qdrant_client import QdrantClient

REQUEST_TIMEOUT_SECONDS = 30


def make_voyage_client():
    return voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"], timeout=REQUEST_TIMEOUT_SECONDS)


def make_qdrant_client():
    return QdrantClient(url=os.environ["QDRANT_URL"], timeout=REQUEST_TIMEOUT_SECONDS)
