# insurance_rag 📄

A personal project to build an end-to-end, usable Retrieval-Augmented Generation (RAG) system — starting with the Indian life insurance (LIC) use case: help someone find the best policy + rider combination for their situation, based on a short profile and their stated concerns, without needing to know insurance jargon or rider names up front. Personal use first, BYOK-capable (bring your own API key) for possible wider use later.

## Constraints ⚖️
- **Cost target: $0 to near-$0.** Free tiers and self-hosted infra only. Exception: one-time ingestion (extraction + embedding), a few dollars total, not recurring.
- **Infra:** reuses what already exists — see `docs/infra-baseline.md` for the Oracle VM/n8n setup this is built on.
- **No credentials in the browser** except an optional user-supplied BYOK key, held in session memory only, never persisted server-side.
- Source PDFs are public LIC material, no PII — fine to store in this public repo.

## Status 🚧

**Ingestion pipeline (extraction) is built and validated for the term assurance category only.** Money-back, whole-life, endowment, and rider categories are scoped in the corpus but not yet extracted. The query/retrieval side (eligibility filtering, ranking, narrative generation) is designed but not yet built — see `docs/ingestion_architecture.md` and `docs/query_architecture.md` for the full pipeline design.

| Category | Policies in corpus | Extraction validated |
|---|---|---|
| Term assurance | 7 | Yes — tested end-to-end on 2 policies (Saral Jeevan Bima, Yuva Credit Life) |
| Money-back | 6 | Not yet |
| Endowment | 11 | Not yet |
| Whole life | 2 | Not yet |
| Riders | 6 | Not yet |

## How it works (high level) ⚙️

Each policy has two source PDFs — a `policy_doc` (authoritative, complete) and a `brochure` (used mainly for its sample premium table). These get run through a two-stage Gemini pipeline:

1. **Layer 1 — extraction**: category-specific structured JSON pulled directly from the two PDFs (premium, eligibility, benefit formulas, etc). See `docs/schema.md`.
2. **Layer 2 — derivation**: a normalized decision-layer JSON derived *only* from Layer 1's output (no source docs) — the layer the future query pipeline will actually filter/rank against.

Full design rationale is in `docs/ingestion_architecture.md` (extraction) and `docs/query_architecture.md` (the future retrieval/ranking side).

## Quickstart 🚀

```bash
pip install google-genai --break-system-packages
cp .env.example .env   # then fill in your own GEMINI_API_KEY

extraction_test/run_pipeline.sh <policy_id> <policy_doc.pdf> <brochure.pdf>
```

See `CLAUDE.md`'s "Build & Run Commands" section for full details, including how to run each stage individually.

## Documentation 📚

- [`docs/ingestion_architecture.md`](docs/ingestion_architecture.md) — extraction pipeline detail (built and validated for term assurance).
- [`docs/query_architecture.md`](docs/query_architecture.md) — query/retrieval pipeline detail (designed, not yet built).
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
