# Insurance RAG — requirements and architecture (v2, final for new project)

## Goal
Help a user find the best LIC policy + rider combination for their situation, based on a short profile and their stated concerns (not rider names they won't recognize). Personal use first, BYOK-capable for possible wider use later.

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
- Project structure: keep this file and `infra-baseline.md` in a `docs/` folder in the repo. Write a short `CLAUDE.md` (Claude Code's own guidance: 20-200 lines) that tells Claude Code to read `docs/architecture.md` and `docs/infra-baseline.md` before starting work, plus any hard rules (commit conventions, build commands once they exist) - don't paste the full docs into `CLAUDE.md` itself.
- Claude Code (local) and the claude.ai Project (browser) don't share memory - Claude Code's memory is local to this machine and this folder, the Project's is cloud-side and tied to your account. Both need these same two files independently: upload them as Project knowledge in the browser, and commit them into `docs/` for Claude Code to read locally. Keep them identical; update both when either changes, since there's no sync between the two.

## Data layers (kept separate deliberately)

**1. Curated knowledge base — admin-controlled, versioned**
Raw PDFs + extracted structured JSON + embeddings.
- Raw PDFs → GitHub (`/raw_pdfs/`). Stable fetch URL for n8n, free, versioned, and the same destination a future auto-scraper will target.
- Extracted JSON → GitHub, versioned alongside the PDFs.
- Vectors + filterable fields → Qdrant on the Oracle VM.

**Metadata tagging — two granularities, not one:**
- *Chunk-level tags* on narrative text chunks: policy_id, category, section_name. Keeps retrieval from confusing one policy's boilerplate for another's — this corpus is 60–70% identical legal text across documents (Insurance Act sections, grievance mechanisms), so provenance tags matter more here than in a typical RAG project.
- *Profile-level records*, one per policy and one per rider, carrying only structured comparison fields (age bounds, sum assured bounds, premium, `compatible_base_plan_types`) for the deterministic filter. Kept separate from chunk records so eligibility numbers aren't duplicated across every chunk of a policy.

**2. Session state — ephemeral, per-conversation**
Turn-based slot-filling state (age given? cover amount given? priorities given? result generated?). Not on GitHub. Clears when the conversation ends. Also the state machine that makes a future chat interface a frontend-only swap, not a backend rebuild.

**3. Trace / evaluation log — append-only**
Separate Qdrant collection. Captures, per live query: the query text, a snapshot of retrieved chunk text (not just IDs, so a trace survives future re-embedding or re-chunking), and the final generated response. Written asynchronously so a logging failure never breaks the user-facing response.

## Vector database choice — stated plainly, not oversold
Qdrant, chosen for self-hosting fit and native n8n integration — not a rigorous benchmark against alternatives. Pinecone was excluded early because it has no self-hosting option at all below enterprise BYOC, which conflicts with the stated infra preference. Weaviate was the closer, untested alternative — comparable feature set, would likely have worked equally well. Before scaling further: confirm actual free RAM on the specific Oracle VM shape in use. Running n8n + nginx + Qdrant on the older 1GB micro instance is tighter than on the ARM Ampere free tier; at this corpus's scale (low thousands of vectors) Qdrant's own footprint is small, but it's still worth checking rather than assuming.

## Pipeline

### Ingestion — one-time / occasional, admin-triggered, not user-facing
1. Upload raw PDFs to GitHub. Current batch (~20–30 files): plain drag-and-drop via github.com. Future scraper-driven updates: a small script using GitHub's Contents API (no git clone, respects the no-clone-on-work-laptop rule).
2. Extraction: Gemini 3.1 Pro. Structured JSON against a core schema (premium, term, sum assured, eligibility) plus category-specific extensions (term / money-back / whole-life / endowment each have different benefit structures — decide extension fields before writing the extraction prompt).
3. Chunking: structure-aware — split on the document's own PART/section headers (IRDAI mandates a consistent template across LIC products), not fixed-size windows and not model-based chunking.
4. Embedding: Voyage `voyage-law-2` (verify current model name before building — Voyage ships new generations often). Domain-specialized for legal/insurance text, free for a corpus this size.
5. Load into Qdrant per the two-tier tagging scheme above.

### Query — recurring, user-facing, cost-sensitive only on the LLM steps
1. Frontend collects: basic profile (age, desired cover or income, budget, smoker status) and risk priorities — asked as underlying concerns via plain multi-select, no forced ranking, no rider jargon shown to the user.
2. Turn-based session handler: takes one answer at a time, updates slot-filling state, decides the next question or whether it's ready to search.
3. Deterministic eligibility filter: Qdrant payload filter across base plans + compatible riders using the profile-level records. Fast, no LLM — this step does the actual comparison math.
4. Fallback: fewer than 3 matches → automatically relax constraints (sum assured / budget) and say so explicitly in the result.
5. Narrative retrieval: Qdrant vector search over chunk-level records, top ~15–20 candidates.
6. Reranking: Voyage `rerank-2.5-lite` (free tier, same provider as embeddings) re-scores those candidates down to the top 3–5 before generation — targets the boilerplate-similarity risk directly.
7. Narrative generation: Gemini flash-lite explains the top 3 plan + rider combos in plain language with pros/cons. The only genuinely slow step — design the loading state around it specifically.
8. Trace log write (async) after steps 3–7.
9. BYOK check: client-supplied Gemini key used if present, falls back to your stored n8n credential otherwise.

## Evaluation — build the ground truth before the judge
- **Golden set (build now, not deferred):** 15–30 hand-verified query + expected-answer pairs, covering simple lookups, eligibility filtering, cross-plan comparisons, and edge cases (below-minimum sum assured requests). Built manually against the actual PDFs using your own domain knowledge. This is the ground-truth anchor — without it, an LLM judge is just comparing the system's output to its own opinion, which proves nothing.
- **Trace log:** as above, non-blocking, captures what actually happened on live queries.
- **LLM judge (genuinely fine to defer):** an offline batch job that scores trace-log entries against the golden set. Never touches the live query path, so this is the one piece that really can be added later without rebuilding anything underneath it.

## Explicitly deferred, not forgotten
- Rider-selection UI: plain multi-select on concerns, not a ranked list.
- Chat interface: backend is already turn-based to support it — frontend swap only, whenever ready.
- Automated PDF scraping for policy updates: current ingestion is manual/admin-triggered; the scraper will call the same GitHub-upload step later.

## Verify before building (things that shift over time or weren't confirmed)
- ~~Current Gemini 3.1 Pro pricing and rate limits.~~ Resolved 2026-07-07: Gemini 3.1 Pro lost free-tier API access April 1, 2026. Now $2/$12 per 1M tokens up to 200K context, $4/$18 above. No impact on cost target - Pro is scoped to one-time extraction only, already budgeted as paid.
- ~~Current Voyage model names for `voyage-law-2` and `rerank-2.5-lite`, and their free-tier ceilings.~~ Resolved 2026-07-07: both confirmed current, not deprecated.
- ~~Actual free RAM available on the Oracle VM shape currently in use.~~ Resolved 2026-07-07: confirmed adequate, see infra-baseline.md.
- Whether to persist the BYOK key locally for personal-only mode, or keep it session-memory-only always.

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
