# CLAUDE.md — Insurance RAG Project

## Required Reading Before Starting Any Work

**Always read these documents first**, before making any changes, answering questions, or planning implementations:

1. [`README.md`](README.md) — Project goal, constraints, corpus, and an index into the docs below.
2. [`docs/infra-baseline.md`](docs/infra-baseline.md) — Infrastructure baseline: services, environments, deployment topology, and operational constraints.
3. [`docs/schema.md`](docs/schema.md) — Data layer model (Layer 1/2/3), Layer 1/2 field schemas, document merge rule, and extraction-rule caveats. Required reading before any ingestion or extraction work.
4. [`docs/ingestion_architecture.md`](docs/ingestion_architecture.md) — Extraction pipeline detail. Required reading before ingestion work specifically.
5. [`docs/query_architecture.md`](docs/query_architecture.md) — Query/retrieval pipeline detail. Required reading before query-pipeline work specifically.

If any of these files is missing or empty, ask the user before proceeding — the architecture, infra, and schema context are prerequisites for working safely in this codebase.

---

## Project Overview

This project is an insurance-domain Retrieval-Augmented Generation (RAG) system. It ingests insurance policy documents, indexes them for semantic search, and uses an LLM to answer user queries grounded in retrieved context.

---

## Commit Conventions

- Never add `Co-Authored-By` or any Claude attribution trailer to commit messages. All commits should be authored as the user only.

---

## Build & Run Commands

- Install dependencies: `pip install google-genai qdrant-client voyageai --break-system-packages`
- One-time setup: `cp .env.example .env`, then fill in your real `GEMINI_API_KEY`, `VOYAGE_API_KEY`, and `QDRANT_URL` (`.env` is gitignored — never commit it). `MODEL` in `.env` defaults to `gemini-flash-lite-latest`, the validated free-tier choice — see `docs/ingestion_architecture.md` step 2 for why. `QDRANT_URL` should point at the SSH-tunneled loopback address (`http://localhost:6333`) — Qdrant on the Oracle VM is bound to `127.0.0.1` only, not publicly reachable — run the `qdrant-tunnel` alias (see `docs/infra-baseline.md`) before any script that talks to Qdrant.
- Run the Layer 1 + Layer 2 ingestion pipeline for one policy:
  ```
  extraction_test/run_pipeline.sh <policy_id> <policy_doc.pdf> <brochure.pdf> [model] [output_dir]
  ```
  Reads `GEMINI_API_KEY`/`MODEL` from `.env` automatically; `[model]` only needs to be passed to override it. Runs Layer 1 extraction (`docs/prompts/prompt_a_pdf.txt`, PDF-native), verifies the output is valid JSON, then runs Layer 2 derivation (`docs/prompts/prompt_b.txt`, JSON-only, no source docs) from it. See `docs/ingestion_architecture.md` for the full pipeline design and validation notes behind these choices.
- To run either stage individually: `extraction_test/run_layer1_extraction.py` or `extraction_test/run_layer2_derivation.py` (each takes `prompt_path out_path model [args...]` — see the usage comment at the top of each file). These read `GEMINI_API_KEY` from the environment directly, not from `.env` — run `set -a && source .env && set +a` first if running them standalone outside `run_pipeline.sh`.
- Run Layer 3 chunking + embedding for one policy (separate from the Layer 1/2 pipeline, not yet folded into `run_pipeline.sh`):
  ```
  chunking/chunk_policy_doc.py <policy_id> <category> <policy_doc.pdf> <out.json>
  chunking/embed_and_load_layer3.py insurance_rag_layer3 <chunks.json> [chunks.json ...]
  ```
  `embed_and_load_layer3.py` reads `VOYAGE_API_KEY`/`QDRANT_URL` from the environment directly (same `set -a && source .env && set +a` convention as above), embeds each chunk with Voyage `voyage-law-2`, and upserts into the shared `insurance_rag_layer3` Qdrant collection (single collection across all categories, filtered by `policy_id`/`category` at query time). Batches and paces embed calls to stay within Voyage's free-tier 3 RPM / 10K TPM cap — expect minutes, not seconds, for a full category. See `docs/ingestion_architecture.md` steps 5-6 and `docs/schema.md`'s "Layer 3 — chunking & embedding caveats" for details.
- API server, tests, lint/format: not yet built.

**Never type the API key value directly into a command, ever — including in a Claude Code session.** Always source it from `.env` (`set -a && source .env && set +a`, which `run_pipeline.sh` already does internally). Typing the literal key into a shell command puts it in plaintext in shell history and, if run via an AI coding assistant, in that assistant's conversation transcript — treat any key that's ever been typed literally as compromised and rotate it in Google AI Studio immediately, don't just stop reusing it.

---

## Key Conventions

- Do not hardcode API keys or credentials anywhere in the codebase. Use environment variables or a secrets manager.
- **Never suggest embedding credentials or tokens directly in git remote URLs** (e.g. `https://<token>@github.com/...`). If a push fails due to missing credentials, instruct the user to authenticate via `osxkeychain` credential helper or interactively — never via a token-in-URL workaround.
- All document ingestion changes must be validated against the baseline chunking and embedding strategy described in `docs/ingestion_architecture.md`.
- Do not modify retrieval logic or prompt templates without first reviewing how they interact with the evaluation suite described in `docs/evaluation_architecture.md` (if one exists yet).

---

## Directory Structure (high-level)

```
insurance_rag/
├── docs/                  # Architecture, infra, and schema docs (read first)
│   ├── ingestion_architecture.md
│   ├── query_architecture.md
│   ├── evaluation_architecture.md
│   ├── infra-baseline.md
│   ├── schema.md
│   ├── prompts/           # Production extraction/derivation prompts (+ appendix/ deprecated variants)
│   └── progress/          # Daily session progress logs (YYYYMMDD-progress.md)
├── raw_pdfs/              # Source policy documents (do not modify manually)
├── extracted/             # Permanent, versioned Layer 1/2 JSON store (per policy)
├── extraction_test/       # Layer 1/2 extraction pipeline scripts only
├── chunking/              # Layer 3 chunking + embedding scripts + outputs
├── CLAUDE.md              # This file
└── README.md              # Project goal, constraints, corpus, and docs index
```

---

## Notes for Future Claude Code Sessions

- This file is the entry point for all AI-assisted work. Keep it up to date as the project evolves.
- If any doc listed under "Required Reading" above has been updated since your last session, re-read it fully before continuing.
- Prefer small, reviewable commits over large sweeping changes.
