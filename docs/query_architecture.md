# Insurance RAG — Query pipeline

Recurring, user-facing, cost-sensitive only on the LLM steps. See `docs/schema.md` for what Layer 1, Layer 2, and Layer 3 are and how they relate.

**Status: designed, not yet built.**

## Session state (ephemeral, per-conversation)
Turn-based slot-filling state (age given? cover amount given? priorities given? result generated?). Not on GitHub. Clears when the conversation ends. Also the state machine that makes a future chat interface a frontend-only swap, not a backend rebuild.

## Vector database choice — stated plainly, not oversold
Qdrant, chosen for self-hosting fit and native n8n integration — not a rigorous benchmark against alternatives. Pinecone was excluded early because it has no self-hosting option at all below enterprise BYOC, which conflicts with the stated infra preference (see `docs/infra-baseline.md`). Weaviate was the closer, untested alternative — comparable feature set, would likely have worked equally well. Before scaling further: confirm actual free RAM on the specific Oracle VM shape in use (see `docs/infra-baseline.md` for current confirmed specs); at this corpus's scale (low thousands of vectors) Qdrant's own footprint is small, but it's still worth checking rather than assuming.

## Pipeline

1. Frontend collects: basic profile (age, desired cover or income, budget, smoker status) and risk priorities — asked as underlying concerns via plain multi-select, no forced ranking, no rider jargon shown to the user.
2. Turn-based session handler: takes one answer at a time, updates slot-filling state, decides the next question or whether it's ready to search.
3. Deterministic eligibility filter: in-memory Python filter over Layer 2 JSON (loaded from `/extracted/` at process/session start), applied independently to base plans and compatible riders — not a Qdrant payload filter. No vector-search need exists for structured field filtering at this corpus's scale (32 policies max across all categories once fully extracted), so Layer 2 is never loaded into Qdrant; Qdrant stays scoped to Layer 3 narrative-chunk vector search (step 6) only. Fast, no LLM — this step does the actual comparison math. Checks Layer 2 Group C bounds (age/sum_assured/term). Concern-tag matching (Group A) is a hard gate here, not just a sort input: at least one Group A concern_tags overlap with the user's stated concerns is required to pass — a single OR-pass (match-at-least-1), not sequential AND-then-relax queries. A rider passing this filter is never inherited from its base plan passing — each is checked independently against the user's age/sum_assured/term and concern tags, since a compatible rider can have its own narrower eligibility bounds than its base plan.
4. Fallback, two-tier: fewer than 3 matches after step 3 →
   - **Tier 1:** relax Group C bounds only (sum assured / age / term); the concern-tag gate stays firm. Flag this explicitly in the result.
   - **Tier 2** (still fewer than 3 after Tier 1): also drop the concern-tag gate, allowing zero-overlap candidates through. Flag this explicitly and distinctly from Tier 1 (e.g. "no plans directly match your stated concerns, showing closest eligible options by age/cover/term").
   Budget is deliberately not relaxed at either tier, since it isn't evaluated until step 5.
5. Premium estimation + budget filter: for each candidate surviving steps 3–4, compute an estimated premium via deterministic Python linear interpolation (a manual two-point formula — no LLM, no numpy dependency needed at this data size) between the two nearest points in that policy's Layer 1 `sample_illustrative_premiums` (not Layer 2), against the user's actual age/sum-assured/term (not a fixed baseline). Interpolation is matched within the same `premium_payment_option` column only (single / limited_ppt_5 / limited_ppt_10 / limited_ppt_15 / regular) — never interpolated across different payment-option columns, since they aren't the same curve. Candidates whose profile falls outside the policy's sample table range for that column are excluded from results, with the reason logged — the query still completes. Then filter out any candidate whose interpolated premium exceeds the user's stated budget, same exclude-and-log mechanism, not a query failure. Budget can only be filtered here, not in step 4's eligibility fallback, since it depends on the interpolated premium this step computes — it isn't a static Layer 2 Group C field. Guards against concern-matching + semantic relevance alone systematically favoring feature-rich, expensive policies.
6. Narrative retrieval: Qdrant vector search over Layer 3 chunk-level records, restricted to the policy_ids that survived steps 3–5 (not an independent global search) — top ~15–20 candidates within that surviving set.
7. Reranking + sort: Voyage `rerank-2.5-lite` (free tier, same provider as embeddings) re-scores candidates for semantic relevance. Final order uses `concern_match_count` (from step 3) as the primary sort key. Within each count-block, candidates are grouped into relevance tiers by rerank score: any two candidates whose rerank scores fall within a tolerance band of each other (placeholder threshold: 0.05 — **UNCALIBRATED**, not validated against any real rerank score distribution, since nothing has been run against actual Voyage `rerank-2.5-lite` output yet) are treated as tied on relevance and placed in the same tier; a rerank score gap larger than the threshold puts candidates in different tiers, and the higher-scoring tier always wins. Within a tier, candidates are sorted by interpolated premium (step 5) ascending. This is not blended into one weighted number.
   - **Decision, flagged rather than silently picked:** premium only breaks ties within a relevance tier, never overriding a real relevance gap across tiers — this is why the tolerance band exists instead of an exact-score tiebreak, which would almost never fire given rerank scores are floating point and would leave premium as dead weight in the sort. Keeps ranking explainable for the eventual LLM judge / golden-set evaluation, and avoids inventing an arbitrary weighting formula between concern match, semantic relevance, and price.
8. Narrative generation: Gemini flash-lite explains the top 3 plan + rider combos in plain language with pros/cons, and discloses that shown premiums are reference estimates from linear interpolation, not exact quotes. The only genuinely slow step — design the loading state around it specifically.
9. Trace log write (async) after steps 3–8.
10. BYOK check: client-supplied Gemini key used if present, falls back to your stored n8n credential otherwise.

## Runtime data source for steps 3–5

The live query pipeline (n8n, Oracle VM) reads Layer 1/2 JSON for steps 3–5 from a local git clone of this repo on the VM, kept current via `git pull` — a manual, end-of-session step for now, not automated (see `docs/infra-baseline.md`'s end-of-session checklist). Uses the same scoped fine-grained PAT and local (non-global) git identity already established for this repo's clone-safe exception in `docs/infra-baseline.md`. The live query path must never fetch from GitHub's raw endpoint per query — that pattern belongs to ingestion/admin-time tooling only, not a request-serving path.

## Explicitly deferred, not forgotten
- Rider-selection UI: plain multi-select on concerns, not a ranked list.
- Chat interface: backend is already turn-based to support it — frontend swap only, whenever ready.

## Open questions

- Whether to persist the BYOK key locally for personal-only mode, or keep it session-memory-only always.
- Whether premium curves are actually close to linear across a policy's sample-premium points — not checked yet, underlies the premium-interpolation query step (step 5).
- Rerank-score similarity threshold for step 7's premium tiebreaker (currently placeholder 0.05) — needs calibration against real rerank score distributions once steps 5–7 are implemented and run against actual data.
- Voyage's free-tier 3 RPM / 10K TPM cap (see `docs/schema.md`'s Layer 3 caveats) is the real constraint on concurrent live-query throughput — steps 6 (query embedding) and 7 (reranking) both call Voyage — not VM compute. Worth revisiting if this moves past solo personal use.
