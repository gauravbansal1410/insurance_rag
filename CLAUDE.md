# CLAUDE.md — Insurance RAG Project

## Required Reading Before Starting Any Work

**Always read these two documents first**, before making any changes, answering questions, or planning implementations:

1. [`docs/architecture.md`](docs/architecture.md) — System design, component relationships, data flow, and key design decisions.
2. [`docs/infra-baseline.md`](docs/infra-baseline.md) — Infrastructure baseline: services, environments, deployment topology, and operational constraints.

If either file is missing or empty, ask the user before proceeding — the architecture and infra context are prerequisites for working safely in this codebase.

---

## Project Overview

This project is an insurance-domain Retrieval-Augmented Generation (RAG) system. It ingests insurance policy documents, indexes them for semantic search, and uses an LLM to answer user queries grounded in retrieved context.

---

## Commit Conventions

<!-- TODO: Fill in commit conventions.
     Example prompts to consider:
     - Prefix style: feat/fix/chore/docs/refactor?
     - Scope tagging: feat(ingestion): ..., fix(retrieval): ...?
     - Max subject line length?
     - Co-authored-by or sign-off requirements?
-->

_Commit conventions to be defined. Update this section before the first collaborative commit._

---

## Build & Run Commands

<!-- TODO: Fill in build and run commands.
     Example sections to add:
     - Install dependencies: `pip install -r requirements.txt` or `uv sync`
     - Run ingestion pipeline: ...
     - Start API server: ...
     - Run tests: ...
     - Lint/format: ...
-->

_Build and run commands to be documented. Update this section once the dev environment is confirmed._

---

## Key Conventions

- Do not hardcode API keys or credentials anywhere in the codebase. Use environment variables or a secrets manager.
- All document ingestion changes must be validated against the baseline chunking and embedding strategy described in `docs/architecture.md`.
- Do not modify retrieval logic or prompt templates without first reviewing how they interact with the evaluation suite (if one exists).

---

## Directory Structure (high-level)

```
insurance_rag/
├── docs/                  # Architecture and infra docs (read first)
│   ├── architecture.md
│   └── infra-baseline.md
├── raw_docs/              # Source policy documents (do not modify manually)
├── CLAUDE.md              # This file
└── README.md
```

---

## Notes for Future Claude Code Sessions

- This file is the entry point for all AI-assisted work. Keep it up to date as the project evolves.
- If `docs/architecture.md` or `docs/infra-baseline.md` have been updated since your last session, re-read them fully before continuing.
- Prefer small, reviewable commits over large sweeping changes.
