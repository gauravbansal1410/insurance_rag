# Insurance RAG — requirements and architecture (v2, final for new project)

## Goal
Help a user find the best LIC policy + rider combination for their situation, based on a short profile and their stated concerns (not rider names they won't recognize). Personal use first, BYOK-capable for possible wider use later.

## Docs map
This file covers the stable, cross-cutting "why" decisions. Pipeline-stage detail lives in its own doc, each of which links back here for the Data layers definitions:
- [`docs/ingestion.md`](ingestion.md) — extraction pipeline (built and validated for term assurance).
- [`docs/query.md`](query.md) — query/retrieval pipeline (designed, not yet built).
- [`docs/evaluation.md`](evaluation.md) — golden set, trace log, LLM judge (not yet built).
- [`docs/schema.md`](schema.md) — Layer 1/2 field schemas and extraction-rule caveats.
- [`docs/infra-baseline.md`](infra-baseline.md) — infrastructure/deployment baseline.
- [`docs/progress/`](progress/) — dated session logs with testing detail and validation numbers behind the decisions summarized here.

## Constraints
- Cost target: $0 to near-$0. Free tiers and self-hosted infra only. Exception: one-time ingestion (extraction + embedding), a few dollars total, not recurring.
- Infra: reuse what already exists — Oracle Cloud Always Free VM running n8n (HTTPS via nginx + Let's Encrypt), the existing public GitHub repo pattern, existing Gemini API usage.
- No credentials in the browser except an optional user-supplied BYOK (bring your own key) key, held in session memory only, never persisted server-side.

## Corpus
- 20+ LIC policies: term, money-back, whole life, endowment.
- 7–8 riders, each compatible with a subset of base plan types — riders are first-class records, not text buried inside a policy's JSON.
- Source PDFs currently local (Mac), ~6 months old. Public LIC material, no PII — fine to store in a public repo.

## Repo & local dev setup
- Dedicated repo: `github.com/gauravbansal1410/insurance_rag` - separate from the `learning-ai-agents` monorepo on purpose, since that repo holds interview-prep content and this one doesn't. Never merge the two.
- This repo is cloned locally (breaking the general no-clone-on-work-laptop default, which still applies to `learning-ai-agents`). The exception is scoped to this repo specifically because it has no sensitive content, not a change to the general rule.
- Suggested local path: `~/Desktop/claude/personal/insurance_rag`.
- Auth: a fine-grained GitHub personal access token scoped to only this repo (Contents read/write), not a broad token or account-wide SSH key.
- Git identity: set locally per-repo (`git config user.name` / `user.email`, no `--global` flag), not inherited from any global config that might point at a work identity.
- Project structure: keep this file and `infra-baseline.md` in a `docs/` folder in the repo. `CLAUDE.md` (Claude Code's own guidance) tells Claude Code to read `docs/architecture.md`, `docs/infra-baseline.md`, and `docs/schema.md` before starting work, plus any hard rules (commit conventions, build commands) - don't paste the full docs into `CLAUDE.md` itself.
- Claude Code (local) and the claude.ai Project (browser) don't share memory - Claude Code's memory is local to this machine and this folder, the Project's is cloud-side and tied to your account. Both need these same two files independently: upload them as Project knowledge in the browser, and commit them into `docs/` for Claude Code to read locally. Keep them identical; update both when either changes, since there's no sync between the two.

## Data layers (kept separate deliberately)

**1. Curated knowledge base — admin-controlled, versioned**
Raw PDFs + extracted structured JSON + embeddings.
- Raw PDFs → GitHub (`/raw_pdfs/`). Stable fetch URL for n8n, free, versioned, and the same destination a future auto-scraper will target.
- Extracted JSON → GitHub, versioned alongside the PDFs.
- Vectors + filterable fields → Qdrant on the Oracle VM.

**Metadata — three layers, not two:**
- *Layer 1 — category-specific extraction.* Schema varies by plan type (term / money-back / whole-life / endowment each have different benefit structures). One record per policy, extracted directly from source documents. Full schema in `docs/schema.md` (term-assurance built first; other categories not yet scoped at this depth).
- *Layer 2 — normalized decision layer.* Identical schema across all categories, computed at ingestion time from Layer 1 via a separate Gemini call, given only Layer 1's JSON (see `docs/ingestion.md` for why source docs are deliberately withheld here). Some fields are direct copies of Layer 1 bounds (deterministic filter facts), some are restructured Layer 1 language (payout mechanics), and some are genuinely interpretive judgments (concern tags). This is the layer the query pipeline's deterministic filter and sort logic actually run against — Layer 1 is the source of truth, Layer 2 is what's queried. Full schema in `docs/schema.md`.
- *Layer 3 — chunk-level narrative embeddings.* Structure-aware chunks of the raw policy_doc text, tagged with policy_id, category, section_name. Keeps retrieval from confusing one policy's boilerplate for another's — this corpus is 60–70% identical legal text across documents (Insurance Act sections, grievance mechanisms), so provenance tags matter more here than in a typical RAG project. Fed by the same source document as Layer 1, but chunking/embedding is a separate ingestion sub-step from Layer 1/2 extraction. Used only by narrative retrieval (query step 6), not the deterministic filter.

**2. Session state — ephemeral, per-conversation**
Turn-based slot-filling state (age given? cover amount given? priorities given? result generated?). Not on GitHub. Clears when the conversation ends. Also the state machine that makes a future chat interface a frontend-only swap, not a backend rebuild.

**3. Trace / evaluation log — append-only**
Separate Qdrant collection. Captures, per live query: the query text, a snapshot of retrieved chunk text (not just IDs, so a trace survives future re-embedding or re-chunking), and the final generated response. Written asynchronously so a logging failure never breaks the user-facing response.

## Vector database choice — stated plainly, not oversold
Qdrant, chosen for self-hosting fit and native n8n integration — not a rigorous benchmark against alternatives. Pinecone was excluded early because it has no self-hosting option at all below enterprise BYOC, which conflicts with the stated infra preference. Weaviate was the closer, untested alternative — comparable feature set, would likely have worked equally well. Before scaling further: confirm actual free RAM on the specific Oracle VM shape in use. Running n8n + nginx + Qdrant on the older 1GB micro instance is tighter than on the ARM Ampere free tier; at this corpus's scale (low thousands of vectors) Qdrant's own footprint is small, but it's still worth checking rather than assuming.

## Explicitly deferred, not forgotten
- Rider-selection UI: plain multi-select on concerns, not a ranked list.
- Chat interface: backend is already turn-based to support it — frontend swap only, whenever ready.
- Automated PDF scraping for policy updates: current ingestion is manual/admin-triggered; the scraper will call the same GitHub-upload step later.

## Glossary
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
