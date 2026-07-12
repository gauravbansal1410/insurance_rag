# CLAUDE.md — Insurance RAG Project

## Required Reading Before Starting Any Work

**Always read these documents first**, before making any changes, answering questions, or planning implementations:

1. [`docs/architecture.md`](docs/architecture.md) — System design, component relationships, data flow, and key design decisions. Links out to the other docs below, which have the pipeline-stage detail.
2. [`docs/infra-baseline.md`](docs/infra-baseline.md) — Infrastructure baseline: services, environments, deployment topology, and operational constraints.
3. [`docs/schema.md`](docs/schema.md) — Layer 1 (category-specific extraction) and Layer 2 (normalized decision layer) schemas, document merge rule, and extraction-rule caveats. Required reading before any ingestion or extraction work.
4. [`docs/ingestion.md`](docs/ingestion.md) — Extraction pipeline detail. Required reading before ingestion work specifically.
5. [`docs/query.md`](docs/query.md) — Query/retrieval pipeline detail. Required reading before query-pipeline work specifically.

If any of these files is missing or empty, ask the user before proceeding — the architecture, infra, and schema context are prerequisites for working safely in this codebase.

---

## Project Overview

This project is an insurance-domain Retrieval-Augmented Generation (RAG) system. It ingests insurance policy documents, indexes them for semantic search, and uses an LLM to answer user queries grounded in retrieved context.

---

## Commit Conventions

- Never add `Co-Authored-By` or any Claude attribution trailer to commit messages. All commits should be authored as the user only.

---

## Build & Run Commands

- Install dependencies: `pip install google-genai --break-system-packages`
- One-time setup: `cp .env.example .env`, then fill in your real `GEMINI_API_KEY` (`.env` is gitignored — never commit it). `MODEL` in `.env` defaults to `gemini-flash-lite-latest`, the validated free-tier choice — see `docs/architecture.md` ingestion step 2 for why.
- Run the Layer 1 + Layer 2 ingestion pipeline for one policy:
  ```
  extraction_test/run_pipeline.sh <policy_id> <policy_doc.pdf> <brochure.pdf> [model] [output_dir]
  ```
  Reads `GEMINI_API_KEY`/`MODEL` from `.env` automatically; `[model]` only needs to be passed to override it. Runs Layer 1 extraction (`docs/prompts/prompt_a_pdf.txt`, PDF-native), verifies the output is valid JSON, then runs Layer 2 derivation (`docs/prompts/prompt_b.txt`, JSON-only, no source docs) from it. See `docs/architecture.md`'s "Ingestion" section for the full pipeline design and validation notes behind these choices.
- To run either stage individually: `extraction_test/run_layer1_extraction.py` or `extraction_test/run_layer2_derivation.py` (each takes `prompt_path out_path model [args...]` — see the usage comment at the top of each file). These read `GEMINI_API_KEY` from the environment directly, not from `.env` — run `set -a && source .env && set +a` first if running them standalone outside `run_pipeline.sh`.
- API server, tests, lint/format: not yet built.

**Never type the API key value directly into a command, ever — including in a Claude Code session.** Always source it from `.env` (`set -a && source .env && set +a`, which `run_pipeline.sh` already does internally). Typing the literal key into a shell command puts it in plaintext in shell history and, if run via an AI coding assistant, in that assistant's conversation transcript — treat any key that's ever been typed literally as compromised and rotate it in Google AI Studio immediately, don't just stop reusing it.

---

## Key Conventions

- Do not hardcode API keys or credentials anywhere in the codebase. Use environment variables or a secrets manager.
- **Never suggest embedding credentials or tokens directly in git remote URLs** (e.g. `https://<token>@github.com/...`). If a push fails due to missing credentials, instruct the user to authenticate via `osxkeychain` credential helper or interactively — never via a token-in-URL workaround.
- All document ingestion changes must be validated against the baseline chunking and embedding strategy described in `docs/ingestion.md`.
- Do not modify retrieval logic or prompt templates without first reviewing how they interact with the evaluation suite described in `docs/evaluation.md` (if one exists yet).

---

## Directory Structure (high-level)

```
insurance_rag/
├── docs/                  # Architecture, infra, and schema docs (read first)
│   ├── architecture.md
│   ├── ingestion.md
│   ├── query.md
│   ├── evaluation.md
│   ├── infra-baseline.md
│   ├── schema.md
│   ├── prompts/           # Production extraction/derivation prompts (+ appendix/ deprecated variants)
│   └── progress/          # Daily session progress logs (YYYYMMDD-progress.md)
├── raw_pdfs/              # Source policy documents (do not modify manually)
├── extraction_test/       # Ingestion pipeline scripts + test outputs
├── CLAUDE.md              # This file
└── README.md
```

---

## Notes for Future Claude Code Sessions

- This file is the entry point for all AI-assisted work. Keep it up to date as the project evolves.
- If `docs/architecture.md`, `docs/infra-baseline.md`, or `docs/schema.md` have been updated since your last session, re-read them fully before continuing.
- Prefer small, reviewable commits over large sweeping changes.
