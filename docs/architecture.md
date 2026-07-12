# Insurance RAG — architecture index

Project goal, constraints, and corpus are in [`../README.md`](../README.md). This file is a thin index into the design docs below — each owns one area in full, so there's a single place to update rather than a fact repeated across files.

## Docs
- [`docs/schema.md`](schema.md) — the data layer model (Layer 1/2/3) both ingestion and query build on, plus full Layer 1/2 field schemas and extraction-rule caveats.
- [`docs/ingestion.md`](ingestion.md) — extraction pipeline (built and validated for term assurance).
- [`docs/query.md`](query.md) — query/retrieval pipeline, including vector database choice (designed, not yet built).
- [`docs/evaluation.md`](evaluation.md) — golden set, trace log, LLM judge (not yet built).
- [`docs/infra-baseline.md`](infra-baseline.md) — infrastructure/deployment baseline (not project-specific — shared across projects on this setup).
- [`docs/prompts/`](prompts/) — production extraction/derivation prompts, plus an `appendix/` of deprecated variants.
- [`docs/progress/`](progress/) — dated session logs with the testing detail and validation numbers behind the decisions in the docs above.
