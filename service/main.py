# The Python query service n8n calls into (docs/query_architecture.md's "Runtime
# orchestration" section) - a thin FastAPI wrapper around run_query_pipeline.py's already-
# tested step 3-8 logic. n8n is deliberately NOT reimplementing that logic as native nodes;
# it just calls this one endpoint.
#
# Layer 1/2 JSON and the precomputed rerank table are loaded once at process start, not per
# request - they're static within a process lifetime on this VM; a fresh `git pull` +
# process restart is how updates land (matches the existing "Runtime data source" note).
# Run with: uvicorn main:app --host 127.0.0.1 --port 8000 (from inside service/, with
# GEMINI_API_KEY/VOYAGE_API_KEY/QDRANT_URL sourced into the environment first - same
# `set -a && source .env && set +a` convention as every other script in this repo).

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "query"))

from fastapi import FastAPI
from pydantic import BaseModel

from load_extracted import load_layer1, load_layer2
from precomputed_relevance import load_precomputed
from clients import make_voyage_client, make_qdrant_client

from run_query_pipeline import run_query
from chat_session import new_session, fill_next_field

app = FastAPI(title="insurance_rag query service")

_layer1 = load_layer1()
_layer2 = load_layer2()
_precomputed = load_precomputed()
_voyage = make_voyage_client()
_qdrant = make_qdrant_client()

# In-memory, single-process session store - fine for personal, single-instance use (this
# project's stated scope); would need a real store (Redis, a DB) before running behind
# multiple workers or surviving a process restart. Keyed by whatever session_id the caller
# (n8n's Chat Trigger) supplies.
_sessions = {}


class QueryProfile(BaseModel):
    age: int
    sum_assured: int
    term: int
    premium_payment_option: str
    budget: float
    concern_tags: list[str]
    sum_assured_type: str = "level"  # docs/schema.md - defaults to "level" when unspecified


@app.get("/health")
def health():
    return {"status": "ok", "policies_loaded": len(_layer1)}


@app.post("/query")
def query(profile: QueryProfile):
    return run_query(profile.model_dump(), _layer1, _layer2, _precomputed, _voyage, _qdrant)


class ChatMessage(BaseModel):
    session_id: str
    message: str | None = None  # None to start a fresh session (asks the first question)


@app.post("/chat")
def chat(msg: ChatMessage):
    """Steps 1-2 (turn-based slot-filling) + steps 3-8 (run_query) in one endpoint, for
    n8n's Chat Trigger to call as a single, near-stateless relay - see chat_session.py for
    the deterministic Q&A logic and its intended swap point for a future LLM-based
    frontend."""
    state = _sessions.setdefault(msg.session_id, new_session())
    state, reply, complete = fill_next_field(state, msg.message, layer1_records=_layer1)

    if not complete:
        return {"done": False, "reply": reply}

    profile = {**state["profile"], "sum_assured_type": state["sum_assured_type"]}
    del _sessions[msg.session_id]  # session finished - don't leak memory across queries
    result = run_query(profile, _layer1, _layer2, _precomputed, _voyage, _qdrant)
    return {"done": True, "reply": result["narrative"], "top3": result["top3"]}
