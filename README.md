# insurance_rag 📄

A personal project to build an end-to-end, usable Retrieval-Augmented Generation (RAG) system — starting with the Indian life insurance (LIC) use case: help someone find the best policy + rider combination for their situation, based on a short profile and their stated concerns, without needing to know insurance jargon or rider names up front. Personal use first, BYOK-capable (bring your own API key) for possible wider use later.

## Constraints ⚖️
- **Cost target: $0 to near-$0.** Free tiers and self-hosted infra only. Exception: one-time ingestion (extraction + embedding), a few dollars total, not recurring.
- **Infra:** reuses what already exists — see `docs/infra-baseline.md` for the Oracle VM/n8n setup this is built on.
- **No credentials in the browser** except an optional user-supplied BYOK key, held in session memory only, never persisted server-side.
- Source PDFs are public LIC material, no PII — fine to store in this public repo.

## Status 🚧

**Full ingestion pipeline (extraction, chunking, embedding, Qdrant load, precomputed rerank scores) is built and validated end-to-end for the term assurance category only — all 7 policies in that category are now extracted, chunked, embedded, loaded into Qdrant, and precomputed.** Money-back, whole-life, endowment, and rider categories are scoped in the corpus but not yet extracted. Query pipeline steps 3-7 (deterministic eligibility filter, premium interpolation, narrative retrieval, reranking + sort) are built and verified against live data (`query/`); steps 1-2 (frontend slot-filling) and 8-10 (narrative generation, trace log, BYOK) are still design-only — see `docs/ingestion_architecture.md` and `docs/query_architecture.md` for the full pipeline design.

| Category | Policies in corpus | Extraction validated |
|---|---|---|
| Term assurance | 7 | Yes — all 7 extracted end-to-end |
| Money-back | 6 | Not yet |
| Endowment | 11 | Not yet |
| Whole life | 2 | Not yet |
| Riders | 6 | Not yet |

## How it works (high level) ⚙️

Each policy has two source PDFs — a `policy_doc` (authoritative, complete) and a `brochure` (used mainly for its sample premium table). These get run through a two-stage Gemini pipeline:

1. **Layer 1 — extraction**: category-specific structured JSON pulled directly from the two PDFs (premium, eligibility, benefit formulas, etc). See `docs/schema.md`.
2. **Layer 2 — derivation**: a normalized decision-layer JSON derived *only* from Layer 1's output (no source docs) — the layer the query pipeline actually filters/ranks against.
3. **Layer 3 — chunking + embedding**: structure-aware split of `policy_doc`'s own PART/section headers into chunks, embedded with Voyage `voyage-law-2` and loaded into a single Qdrant collection (`insurance_rag_layer3`) shared across all categories — used only by narrative retrieval, not the deterministic filter.
4. **Precomputed rerank scores**: every chunk reranked offline against each of the query pipeline's 8 fixed concern tags, so a live query looks up relevance instead of calling Voyage's reranker per request — see `docs/query_architecture.md`'s "Reranking data source" section for why a live rerank call doesn't meet a live query's latency bar at this corpus's chunk sizes.

`chunking/ingest_policy.sh` runs all of the above for one new policy in a single command. Full design rationale is in `docs/ingestion_architecture.md` (ingestion) and `docs/query_architecture.md` (retrieval/ranking).

## Quickstart 🚀

```bash
pip install google-genai qdrant-client voyageai --break-system-packages
cp .env.example .env   # then fill in GEMINI_API_KEY, VOYAGE_API_KEY, QDRANT_URL

chunking/ingest_policy.sh <file_id> <category> <policy_doc.pdf> <brochure.pdf>   # full pipeline, one command
```

See `CLAUDE.md`'s "Build & Run Commands" section for full details, including how to run each stage individually.

## Documentation 📚

- [`docs/ingestion_architecture.md`](docs/ingestion_architecture.md) — extraction, chunking, embedding, and precompute pipeline detail (all built and validated for term assurance).
- [`docs/query_architecture.md`](docs/query_architecture.md) — query/retrieval pipeline detail (steps 3-7 built and verified; steps 1-2, 8-10 still design-only).
- [`docs/evaluation_architecture.md`](docs/evaluation_architecture.md) — golden set, trace log, LLM judge (not yet built).
- [`docs/schema.md`](docs/schema.md) — data layer model (Layer 1/2/3), Layer 1/2 field schemas, and the extraction-rule caveats found so far (worth reading before touching extraction prompts — several are non-obvious document-formatting traps).
- [`docs/infra-baseline.md`](docs/infra-baseline.md) — infrastructure/deployment baseline.
- [`docs/prompts/`](docs/prompts/) — the production extraction/derivation prompts, plus an `appendix/` of deprecated variants kept for reference.
- [`docs/progress/`](docs/progress/) — dated session logs with testing detail behind the decisions in the docs above.
- [`CLAUDE.md`](CLAUDE.md) — instructions for AI-assisted work on this repo (Claude Code entry point).

## Glossary 🔤
- **RAG (retrieval-augmented generation):** an AI pattern where the system first retrieves relevant text from a document store, then hands that text to a language model to generate an answer grounded in it, rather than the model answering from memory alone.
- **Embedding:** a numerical representation of a piece of text that captures its meaning, allowing a computer to measure how similar two pieces of text are.
- **Vector database:** a database built to store embeddings and quickly find the ones most similar to a given query.
- **Chunking:** splitting a long document into smaller pieces before embedding them, since embedding an entire document at once loses precision.
- **Structure-aware chunking:** splitting a document along its own natural sections/headers rather than at arbitrary fixed lengths.
- **Payload (in Qdrant):** the structured metadata attached to a vector — the tags and fields you can filter on, separate from the vector itself.
- **Deterministic:** logic that always produces the same output for the same input, following fixed rules — as opposed to a language model's output, which can vary.
- **Ephemeral:** temporary by design — meant to be discarded rather than kept, like session data that only needs to exist for one conversation.
- **Reranking:** a second, more precise scoring pass over a shortlist of retrieved candidates, used to reorder them by true relevance before the final few are used.
- **BYOK (bring your own key):** a design where the user supplies their own API key rather than the app using a shared one, so usage and cost are the user's own.
- **Trace log:** a record of what happened during a live request (the query, what was retrieved, what was generated), kept so quality can be reviewed or audited later.
- **Golden set:** a small, hand-verified set of question/correct-answer pairs used as ground truth to check whether the system is performing correctly.
- **Turn-based / slot-filling:** a conversation design where the system asks for one piece of information at a time and keeps track of what's already been answered, rather than requiring everything up front.
