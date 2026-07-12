# Insurance RAG — Query pipeline

Recurring, user-facing, cost-sensitive only on the LLM steps. See `docs/architecture.md`'s "Data layers" section for what Layer 1, Layer 2, and Layer 3 are and how they relate.

**Status: designed, not yet built.**

## Pipeline

1. Frontend collects: basic profile (age, desired cover or income, budget, smoker status) and risk priorities — asked as underlying concerns via plain multi-select, no forced ranking, no rider jargon shown to the user.
2. Turn-based session handler: takes one answer at a time, updates slot-filling state, decides the next question or whether it's ready to search.
3. Deterministic eligibility filter: Qdrant payload filter across base plans + compatible riders using Layer 2 Group C fields. Fast, no LLM — this step does the actual comparison math. Single OR-pass on Layer 2 Group A concern_tags (match-at-least-1), not sequential AND-then-relax queries.
4. Fallback: fewer than 3 matches → automatically relax constraints (sum assured / age / term) and say so explicitly in the result. Applies to Group C eligibility fields only — concern-matching does not get a separate relaxation pass, since it's a sort key (step 7) rather than a hard filter, so there's nothing to relax. Budget is deliberately not relaxed here, since it isn't evaluated until step 5.
5. Premium estimation + budget filter: for each candidate surviving steps 3–4, compute an estimated premium via linear interpolation from that policy's brochure sample-premium table, against the user's actual age/sum-assured/term (not a fixed baseline). Candidates whose profile falls outside the policy's sample table range are excluded from results, with the reason logged — the query still completes. Then filter out any candidate whose interpolated premium exceeds the user's stated budget, same exclude-and-log mechanism, not a query failure. Budget can only be filtered here, not in step 4's eligibility fallback, since it depends on the interpolated premium this step computes — it isn't a static Layer 2 Group C field. Guards against concern-matching + semantic relevance alone systematically favoring feature-rich, expensive policies.
6. Narrative retrieval: Qdrant vector search over Layer 3 chunk-level records, restricted to the policy_ids that survived steps 3–5 (not an independent global search) — top ~15–20 candidates within that surviving set.
7. Reranking + sort: Voyage `rerank-2.5-lite` (free tier, same provider as embeddings) re-scores candidates for semantic relevance. Final order uses `concern_match_count` (from step 3) as the primary sort key. Within each count-block, candidates are grouped into relevance tiers by rerank score: any two candidates whose rerank scores fall within a tolerance band of each other (placeholder threshold: 0.05 — **UNCALIBRATED**, not validated against any real rerank score distribution, since nothing has been run against actual Voyage `rerank-2.5-lite` output yet) are treated as tied on relevance and placed in the same tier; a rerank score gap larger than the threshold puts candidates in different tiers, and the higher-scoring tier always wins. Within a tier, candidates are sorted by interpolated premium (step 5) ascending. This is not blended into one weighted number.
   - **Decision, flagged rather than silently picked:** premium only breaks ties within a relevance tier, never overriding a real relevance gap across tiers — this is why the tolerance band exists instead of an exact-score tiebreak, which would almost never fire given rerank scores are floating point and would leave premium as dead weight in the sort. Keeps ranking explainable for the eventual LLM judge / golden-set evaluation, and avoids inventing an arbitrary weighting formula between concern match, semantic relevance, and price.
8. Narrative generation: Gemini flash-lite explains the top 3 plan + rider combos in plain language with pros/cons, and discloses that shown premiums are reference estimates from linear interpolation, not exact quotes. The only genuinely slow step — design the loading state around it specifically.
9. Trace log write (async) after steps 3–8.
10. BYOK check: client-supplied Gemini key used if present, falls back to your stored n8n credential otherwise.

## Open questions

- Whether to persist the BYOK key locally for personal-only mode, or keep it session-memory-only always.
- Whether premium curves are actually close to linear across a policy's sample-premium points — not checked yet, underlies the premium-interpolation query step (step 5).
- Whether Gemini can reliably interpolate premiums at all — assumed "decent" for v1, to be validated later against the golden set.
- Rerank-score similarity threshold for step 7's premium tiebreaker (currently placeholder 0.05) — needs calibration against real rerank score distributions once steps 5–7 are implemented and run against actual data.
