# insurance_rag 📄

A personal project to build an end-to-end, usable Retrieval-Augmented Generation (RAG) system — starting with the Indian life insurance (LIC) use case: help someone find the best policy + rider combination for their situation, based on a short profile and their stated concerns, without needing to know insurance jargon or rider names up front.

## Status 🚧

**Ingestion pipeline (extraction) is built and validated for the term assurance category only.** Money-back, whole-life, endowment, and rider categories are scoped in the corpus but not yet extracted. The query/retrieval side (eligibility filtering, ranking, narrative generation) is designed but not yet built — see `docs/architecture.md` for the full pipeline design.

| Category | Policies in corpus | Extraction validated |
|---|---|---|
| Term assurance | 7 | Yes — tested end-to-end on 2 policies (Saral Jeevan Bima, Yuva Credit Life) |
| Money-back | 6 | Not yet |
| Endowment | 11 | Not yet |
| Whole life | 2 | Not yet |
| Riders | 6 | Not yet |

## How it works (high level)

Each policy has two source PDFs — a `policy_doc` (authoritative, complete) and a `brochure` (used mainly for its sample premium table). These get run through a two-stage Gemini pipeline:

1. **Layer 1 — extraction**: category-specific structured JSON pulled directly from the two PDFs (premium, eligibility, benefit formulas, etc). See `docs/schema.md`.
2. **Layer 2 — derivation**: a normalized decision-layer JSON derived *only* from Layer 1's output (no source docs) — the layer the future query pipeline will actually filter/rank against.

Full design rationale is in `docs/architecture.md`.

## Quickstart 🚀

```bash
pip install google-genai --break-system-packages
cp .env.example .env   # then fill in your own GEMINI_API_KEY

extraction_test/run_pipeline.sh <policy_id> <policy_doc.pdf> <brochure.pdf>
```

See `CLAUDE.md`'s "Build & Run Commands" section for full details, including how to run each stage individually.

## Documentation 📚

- [`docs/architecture.md`](docs/architecture.md) — system design, pipeline stages, and the reasoning behind key decisions (including validated findings from testing, not just the original plan).
- [`docs/schema.md`](docs/schema.md) — Layer 1 / Layer 2 field schemas and the extraction-rule caveats found so far (worth reading before touching extraction prompts — several are non-obvious document-formatting traps).
- [`docs/infra-baseline.md`](docs/infra-baseline.md) — infrastructure/deployment baseline.
- [`docs/prompts/`](docs/prompts/) — the production extraction/derivation prompts, plus an `appendix/` of deprecated variants kept for reference.
- [`CLAUDE.md`](CLAUDE.md) — instructions for AI-assisted work on this repo (Claude Code entry point).
