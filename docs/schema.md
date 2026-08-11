# Insurance RAG — extraction schemas

Detailed Layer 1 (category-specific extraction) and Layer 2 (normalized decision layer) schemas, plus the data layer model both `docs/ingestion_architecture.md` and `docs/query_architecture.md` build on. This file will grow as each plan category gets scoped and is expected to churn more often than the rest of the docs.

## Curated knowledge base (admin-controlled, versioned)
Raw PDFs + extracted structured JSON + embeddings.
- Raw PDFs → GitHub (`/raw_pdfs/`). Stable fetch URL for n8n, free, versioned, and the same destination a future auto-scraper will target.
- Extracted JSON → GitHub, versioned alongside the PDFs, at `/extracted/` (Layer 1 + Layer 2, one file per policy per layer) — the permanent store; `extraction_test/` holds only the pipeline scripts that produce it.
- Vectors + filterable fields → Qdrant on the Oracle VM, collection `insurance_rag_layer3` — one collection shared across all categories, filtered by `policy_id`/`category` at query time rather than split per category, matching how Layer 2's Group C fields already work. Built and validated for term assurance: 63 points loaded (7 policies × 9 chunks each).
- Precomputed rerank scores → GitHub, `chunking/precomputed_rerank_scores.json` — every Layer 3 chunk reranked offline against each of the query pipeline's 8 fixed `concern_tags`, so the live query path (`docs/query_architecture.md` step 7) looks up relevance instead of calling Voyage's reranker per request. See the caveat below for the schema and the approximation this relies on.

**Metadata — three layers, not two:**
- *Layer 1 — category-specific extraction.* Schema varies by plan type (term / money-back / whole-life / endowment each have different benefit structures). One record per policy, extracted directly from source documents. Full schema below (term-assurance built first; other categories not yet scoped at this depth).
- *Layer 2 — normalized decision layer.* Identical schema across all categories, computed at ingestion time from Layer 1 via a separate Gemini call, given only Layer 1's JSON (see `docs/ingestion_architecture.md` for why source docs are deliberately withheld here). Some fields are direct copies of Layer 1 bounds (deterministic filter facts), some are restructured Layer 1 language (payout mechanics), and some are genuinely interpretive judgments (concern tags). This is the layer the query pipeline's deterministic filter and sort logic actually run against — Layer 1 is the source of truth, Layer 2 is what's queried. Full schema below.
- *Layer 3 — chunk-level narrative embeddings.* Structure-aware chunks of the raw policy_doc text, tagged with policy_id, category, section_name, embedded with Voyage `voyage-law-2` and loaded into Qdrant collection `insurance_rag_layer3`. Keeps retrieval from confusing one policy's boilerplate for another's — this corpus is 60–70% identical legal text across documents (Insurance Act sections, grievance mechanisms), so provenance tags matter more here than in a typical RAG project. Fed by the same source document as Layer 1, but chunking/embedding is a separate ingestion sub-step from Layer 1/2 extraction (`chunking/chunk_policy_doc.py` then `chunking/embed_and_load_layer3.py`). Used only by narrative retrieval (`docs/query_architecture.md` step 6), not the deterministic filter.

## Layer 3 — chunking & embedding caveats
- `section_name` (e.g. `PART_C`, `ANNEXURE_1`) is derived purely from the header's position/letter as found by `chunking/chunk_policy_doc.py`'s regex — it is NOT content-verified. It relies on the assumption that a given PART letter maps to the same section meaning across documents (e.g. `PART_C` = Benefits). Confirmed true across all 7 term-assurance policy_docs (`PART_B`=Definitions, `PART_C`=Benefits, `PART_D`=Conditions, `PART_F`=Other Terms, `PART_G`=Grievance/Statutory Provisions, consistent in every one — consistent with IRDAI's mandated policy-document template), but this has only been checked within the term-assurance category. If a future category or document doesn't follow the same letter-to-meaning mapping, `section_name` would silently mislabel content and nothing in the script would catch it. `section_title`, by contrast, is extracted from the document's actual header text (when present) and stays correct even if the letter mapping shifts — treat `section_title` as the more trustworthy field for any semantic reasoning ("give me the Benefits section"), and re-validate the letter mapping per category before relying on `section_name` alone once chunking extends beyond term assurance.
- Voyage's free tier (no payment method on file) caps embed calls at 3 RPM / 10K TPM, and TPM is a rolling 60-second window, not a per-request reset — pacing batches by the RPM interval alone (~21s) is not enough, a batch's tokens need a full 60s+ to clear the window before the next one fires, confirmed the hard way on 2026-07-26 (a 21s-spaced batch still hit `RateLimitError`). `chunking/embed_and_load_layer3.py` batches under a conservative word budget and spaces batches 65s apart with retry-with-backoff as a fallback. Expect embedding a full category to take minutes, not seconds, at this rate — the 7-policy/63-chunk term-assurance run took ~16 batches.

## Layer 3 — precomputed rerank scores (`chunking/precomputed_rerank_scores.json`)
Produced by `chunking/precompute_rerank_scores.py`, keyed `tag -> chunk_id -> {policy_id, score}` for each of the query pipeline's 8 fixed `concern_tags` (`query/concern_tags.py`) — not per concern_tag *combination*. Reranking the full corpus against every possible combination (255 non-empty subsets of 8 tags) would take days at Voyage's free-tier pace; scoring each tag independently is tractable and grows only linearly as new policies are ingested. At query time, `query/precomputed_relevance.py` combines a user's selected tags by taking the **max** precomputed score across them per chunk, then per policy — a deliberate approximation, not identical to reranking one joined query string against each chunk (which is what the live fallback path still does). Consistent with an approximation already used elsewhere in this design (`rerank_and_sort.max_rerank_score_by_policy` already collapses per-chunk scores to per-policy via max, not an average). Exists because a live per-query Voyage rerank call doesn't meet a live query's latency bar at this corpus's chunk sizes — see `docs/query_architecture.md`'s "Reranking data source" section for the full rationale. Refreshed by re-running the precompute script (merges into the existing file by `chunk_id`, doesn't recompute the whole corpus) — normally triggered automatically by `chunking/ingest_policy.sh` when a new policy is ingested.

## Layer 1 derived artifact — precomputed premium lookup (`query/premium_lookup.json`)
Produced by `query/build_premium_lookup.py` — **one entry per policy in the script's own `POLICIES` list, `premium_payment_option: "regular"` + `sum_assured_type: "level"` only per entry** (see `docs/query_architecture.md` step 5 and open questions for the full design rationale). 876 and 859 as of 2026-08-08 — generalized from an earlier 876-only version once 859 needed the same build with different scope values (own reference SA, own ground-truth CSV) and a different rebate-table shape; adding a policy is a new `POLICIES` entry, not a script rewrite. Each entry merges that policy's own Layer 1 `sample_illustrative_premiums` rows (regular/level only, all at its own `sum_assured_min` — each policy's 0%-rebate baseline) with `docs/progress/ground-truth/<policy_id>_scraped_premiums.csv` (real quotes scraped from LIC's live calculator — kept out of Layer 1/2/3, per the ground-truth CSV's own status below). Two independently-modeled axes, not one interpolation formula, confirmed via real data rather than assumed:

    { "876": {
        "premium_payment_option": "regular", "sum_assured_type": "level",
        "age_term_grid": [ { "age": int, "term": int, "premium": number,
                              "source": "brochure" | "scraped" }, ... ],
                                            // bilinear-interpolated at query time
                                            // (query/premium_lookup.py) — confirmed
                                            // NOT linear in age (mortality cost
                                            // accelerates); a query (age, term)
                                            // outside this grid's known bracket on
                                            // either axis is excluded, never
                                            // extrapolated
        "sa_scaling": {
          "type": "percent_of_tabular_premium",
          "reference_sum_assured": 5000000,
          "age_bands": [ { "band": "up_to_30_years" | "31_to_45_years", "age_min", "age_max",
                            "sa_bands": [ { "min": int, "max": int | null,
                                            "rebate_pct": number,
                                            "qa_check": [ { "sum_assured", "formula_multiplier",
                                                            "empirical_multiplier", "sample_count",
                                                            "match": bool }, ... ] }, ... ] }, ... ]
        }
    } }

`sa_scaling` is a direct FORMULA parsed from Layer 1's own `rebate_structures.high_sum_assured_rebate_table`, not fit to scraped data — real ground truth feeds only each `sa_band`'s `qa_check`, never the multiplier itself. **The formula's shape is policy-specific, not one universal equation** — `sa_scaling.type` (copied straight from the rebate table's own `type`, see the extraction-rule caveat above) tells `query/premium_lookup.py`'s `compute_sa_adjustment()` which formula to apply, confirmed necessary 2026-08-05 once 859 turned out to need a genuinely different one, not just different numbers:
  - `"percent_of_tabular_premium"` (876/877): `premium(SA) = base_premium x (SA / reference_SA) x (1 - rebate_pct/100)`. Sum_assured bands are extracted with an explicit numeric `max` per band, **inclusive** (e.g. `876`'s own `sa_bands` run `5000000..9999999`, `10000000..19999999`, ...) — no lower/upper-bound-inclusivity convention to get wrong, unlike the string-band-name parsing this replaced.
  - `"per_mille_of_sum_assured_rupees"` (859): `premium(SA) = base_premium x (SA / reference_SA) - (rate_per_mille/1000) x SA` — a flat Rupee deduction off the SA-proportional premium, not a percent-of-premium discount. `sa_bands` entries carry `rate_per_mille` instead of `rebate_pct`; no `age_bands` wrapper at all, since 859's real table has no age split (`"dimensions": []`). Its `qa_check` entries are shaped differently from the percent type's ratio-based check (`{sum_assured, age, term, formula_premium, actual_premium, match}` rather than `{sum_assured, formula_multiplier, empirical_multiplier, sample_count, match}`) — this formula subtracts an absolute Rupee amount rather than applying a pure ratio, so it isn't age/term-invariant the same way a ratio is, and each check needs to carry the exact (age, term) it was computed at rather than being averaged across scrapes. Validated 2026-08-08 with 4 real cross-SA points (10L/25L at age 30/term 20, 15L/25L at age 45/term 15, spanning all three nonzero rate bands): all 4 match the formula exactly, including SA=25L checked at two different ages — confirms no hidden age-dependence the current formula doesn't model.
  - Either shape's `sa_bands` can have a genuine gap between printed bands (e.g. 859's own table has no band at all for BSA 9.5L–10L) — a sum_assured falling in that gap is excluded, never interpolated across it.
  - Covers the policy's full sum_assured range (through the open-ended `max: null` band), not just whatever SA values happened to get scraped. Sum_assured below `reference_sum_assured` is excluded (shouldn't occur in practice — `eligibility_filter.py` already enforces the real `min_sum_assured` upstream).

`"built_from": { ... }` — provenance: point/row counts from each source, for a quick sanity check without re-diffing the whole file.

Validated via `query/validate_premium_lookup.py [policy_id]` (leave-one-out against a policy's own real age x term points — the SA axis is no longer empirical, so isn't part of this check; defaults to 876): 876 median error 1.06%, mean 3.53% — the mean is pulled up by one real finding, not a defect: term 15 is the thinnest slice of 876's grid (only 4 scraped ages), so removing its one mid-range anchor leaves a wide age gap that a straight-line interpolation misjudges given the curve's real curvature (64.78% error on that single held-out point). **859 (built 2026-08-08 from a real 52-point scrape covering its full valid (age, term) triangle — entry_age 18-65, policy_term 5-40, capped by `age + term <= age_at_maturity_max`): median error 1.66%, mean 2.64%, max 11.12%** — no thin-anchor-driven outlier like 876's, since 859's domain is a clean triangle (not a scattered shape) so the scrape could cover it exhaustively at 5-year steps rather than needing 876's scatter-then-gap-fill approach. Independently spot-checked against a real LIC quote well outside the scraped grid (age 31/term 20/SA 25L, 5x the reference SA — a half-yearly quote converted to an annual-equivalent via 859's own `mode_loadings`): predicted ₹10,420 vs. an implied ₹10,286, a 1.3% spread. Every policy/payment-option/sum_assured_type combination not in `premium_lookup.json`'s scope still uses the original live two-point linear interpolation in `query/premium_interpolation.py` — the dispatch is scope-guarded per entry (each entry's own stored `premium_payment_option`/`sum_assured_type`, no separate hardcoded scope constant), not a global replacement (see `docs/query_architecture.md`). Extending the SA-scaling formula to another policy needs only that policy's own `rebate_structures` to be real structured data — **as of 2026-08-08, all 7 term-assurance policies have this** (876/859 fixed 2026-08-05; 875/877/878/954/955 fixed 2026-08-08 — see the extraction-rule caveat above) — no scraping required for that axis, for any of them. **875's lookup is now built too (2026-08-08)** — the full 33-point scrape landed, leave-one-out median error 1.23%, mean 2.64%, worst case at term=15 (10-12% on the thinnest ages, the same edge-sensitivity pattern 876's own term-15 slice showed, not a new issue). Its reference-SA point (age 30/term 20) matches the brochure exactly, and the precomputed dispatch fires correctly in `premium_interpolation.py`'s hand-check suite. See `docs/query_architecture.md`'s open questions for what 875's real curve says about whether 876's mortality-curve shape transfers to a structurally similar sibling policy. The age x term surface, by contrast, still needs each policy's own real ground truth (brochures don't publish enough points), UNLESS its shape has been confirmed to transfer from an already-built sibling — see below.

**878 built 2026-08-11** from a real 24-point scrape (restricted to terms 15-30 so its Limited Premium/PPT=10 payment structure — 878 has no Regular option at all — stays valid throughout): 25 age/term points, leave-one-out median error 2.25%, mean 4.00%, worst case at term=15 (12.70% — the domain-floor edge-sensitivity pattern every policy's grid has shown at its own thinnest term, not a new issue).

**877 built the same day WITHOUT its own full scrape** — `query/build_premium_lookup.py`'s new `DERIVED_POLICIES` list rescales an already-built sibling's `age_term_grid` by a factor calibrated against a small set of real anchor points, instead of building from a full scrape of its own. Valid only because shape-transfer was confirmed first (877-vs-878, both `credit_life`: 0.1-2.7% deviation at every one of 877's 6 real spot-check points — see `docs/query_architecture.md`'s open questions). 877's `age_term_grid` = 878's grid x 1.262 (the calibrated scale factor; the 6 real anchor ratios ranged 1.2416-1.2766, matching the earlier finding directly), with the 6 real anchor points substituted back in at their own (age, term) — `age_term_grid` entries carry `"source": "derived_from_878"` for every point that isn't a real anchor, so this is auditable, not silently blended in as if it were scraped data. `sa_scaling` is still built normally from 877's own real, independently-extracted rebate table — only the age x term axis is derived, since that's the only axis shape-transfer actually applies to (the SA-scaling formula was already known to be policy-specific, never assumed to transfer). `built_from.scale_factor`/`anchor_ratio_min`/`anchor_ratio_max` are logged into the built artifact itself, not just this doc.

## Ground-truth validation datasets (not extracted content — deliberately outside Layer 1/2/3)
`docs/progress/ground-truth/` holds manually-scraped real data used to validate extraction/interpolation accuracy — never loaded into Qdrant, never treated as an extracted field. `876_scraped_premiums.csv`: real premium quotes pulled from LIC's live online quote calculator for policy 876 (Digi Term), schema designed to scale across policies (`category`, `gender`, `smoker_status`, `sum_assured_type`, split `ppt`/`policy_term`, `scraped_date`/`source` for provenance) — see `docs/query_architecture.md`'s open questions for what it's been used to confirm so far, and the premium-lookup artifact above for what it feeds. `859_scraped_premiums.csv` (added 2026-08-08): same schema, 52 real quotes covering its full valid (age, term) domain at the reference SA (scraped in one pass rather than 876's scatter-then-gap-fill approach, since 859's domain is a clean triangle) plus 4 real cross-SA quotes at higher sum assureds (10L/15L/25L, added same day) to QA-check the SA-scaling formula the same way 876's ground truth does — see the premium-lookup artifact above. `875_scraped_premiums.csv` (added 2026-08-08): 33 real quotes, its full valid (age, term) domain, all at its `sum_assured_min` reference — used for its own lookup and for the shape-transfer comparison against 876 (see `docs/query_architecture.md`'s open questions). `877_scraped_premiums.csv` (6-point spot-check, added 2026-08-10, not extended further — see the premium-lookup artifact above for why): `structural_variant: credit_life`, no Regular Premium option at all (confirmed against its own Coverage page), scraped under Limited Premium/PPT=10/8% loan interest instead, matching 877's own brochure sample-illustrative-premium convention. `878_scraped_premiums.csv` (started as the same 6-point spot-check 2026-08-10, extended to a full 24-point grid 2026-08-11, restricted to terms 15-30 so PPT=10 stays valid throughout): same shape/scope as 877's, `structural_variant: credit_life`, no Regular option. The 6 overlapping points between the spot-check and the full scrape matched exactly, confirming scrape reproducibility. `954_scraped_premiums.csv` (added 2026-08-10): 7-point spot-check, regular premium, its own `sum_assured_min` reference (50L — no free brochure cross-check available at this SA, since 954's own brochure sample table uses a 1Cr baseline instead). `955_scraped_premiums.csv` (added 2026-08-10): 7-point spot-check, regular premium, its own `sum_assured_min` reference (25L) — one flagged discrepancy: the live calculator's UI labeled this quote "Online", but 955's own brochure states offline-only purchase through licensed agents; recorded `distribution_channel: offline`, trusting the brochure over the calculator UI's label (the calculator's own labeling isn't guaranteed to reflect a real distribution-channel price differential the way the printed rebate table does). 877's 6-point spot-check now feeds a *derived* lookup (see the premium-lookup artifact above) rather than needing its own full grid; 954/955 are still spot-checks only, pending the same treatment.

## Document merge rule (each policy has a brochure + a policy_doc)
policy_doc is authoritative for every field — it runs 3-4x the section count of brochure and includes a full Definitions block brochure lacks. Brochure is used only to supply what policy_doc doesn't carry — confirmed so far to be just the sample illustrative premium table (used by the premium-interpolation query step). Track field-level provenance (policy_doc vs brochure) per field so any future conflict is traceable. First real conflict confirmed 2026-07-12: Saral Jeevan Bima's brochure has a redline/tracked-change artifact ("15 30 days") where policy_doc clearly states 30 — resolved via the redline-artifact caveat below, not a one-off.

## Field dot-path convention
Any extraction-note field reference (`low_confidence_fields`, `field_specific_notes`, `field_conflicts`) uses the full path from the top-level key — e.g. `"layer1.sum_assured_max"`, `"layer1.rebate_structures.high_sum_assured_rebate_table"`, `"layer1.sample_illustrative_premiums[3].payment_mode"` — never a bare field name. Bare names are ambiguous once multiple similarly-named fields exist across nested objects.

## Extraction-rule caveats (found during term-assurance schema design, likely to recur in other categories)
- Tax-benefit language is identical boilerplate across documents ("consult your tax advisor") with zero discriminative power — deliberately not modeled as a concern tag.
- Mentions of "rider" in a document's own Section 45 legal boilerplate are not real rider compatibility. Only treat a plan as rider-compatible if it names a specific optional rider by UIN. A naive text-match extraction rule will misfire on this.
- Sample premium tables can have multiple columns per age row (Single, Regular, Limited PPT variants) — extract each column as its own row, don't collapse a table row into one value.
- A stated max (or min) Sum Assured is often followed by an exception clause — either case-by-case underwriting discretion ("may be considered subject to Reinsurer decision") or a conditional lower/higher bound gated on a specific age band + purpose (e.g. a lower minimum for ages 21-45 tied to an approved loan). The GENERAL rule's number always goes in the field; the exception is noted separately (`extraction_notes.field_specific_notes`), never silently substituted into the field or dropped.
- "No surrender value... however [conditional refund] shall be payable" → `surrender_value_applicable: false`. The conditional refund belongs in `policy_cancellation_value_formula`, a separate field — don't let its presence flip `surrender_value_applicable` to true.
- `"%o"` immediately after a decimal in a rebate/rate context is very likely a corrupted per-mille symbol (‰), not literal percent — a `pdftotext`/PDF-rendering artifact seen across multiple LIC documents. Keep the field's value exactly as printed (don't silently substitute the character); flag the ambiguity via `field_specific_notes` instead.
- Documents can contain more than one sample-premium table, sometimes two tables back-to-back under one shared heading with no visual break — the only signal a second table has started is a new sentence restating a different Sum Assured/Term. A naive "stop after the first table found" extraction will silently drop the second one. This is also where PDF-native input meaningfully outperforms `pdftotext` text input (see `docs/ingestion_architecture.md` step 2) — the flattened text loses enough visual structure that even an explicit two-pass "inventory then extract" prompting strategy only caught both tables ~50% of the time on text input, vs. reliably on PDF input.
- Two adjacent numbers with no separator (e.g. "15 30 days") is likely a redline/tracked-change artifact — an old value not fully removed during editing. Don't silently pick one number; flag both via `field_specific_notes` and cross-check against the other source document if possible.
- Free Look Period (the return window) and a refund-processing timeline ("premium refunded within 15 days of receipt of the request") are different concepts that can appear close together in text — don't conflate them into the same field.
- `premium_payment_option: "single"` always implies `payment_mode: "lump_sum"` — a Single Premium is paid once in full, never on an annual/half-yearly/monthly schedule.
- `policy_id` is NOT `plan_name` and must not simply repeat it — an unguided schema caused exactly this on early test runs (e.g. `policy_id: "LIC's Saral Jeevan Bima"`, identical to `plan_name`). `policy_id` is the numeric Plan Number printed on the brochure's cover page (e.g. "859"), usually directly beneath the plan name alongside the UIN — not the UIN itself either. Fall back to the UIN only if the Plan Number genuinely isn't found on either document, and flag that fallback via `low_confidence_fields`.
- **Found 2026-08-01, confirmed already resolved by 2026-08-08 (not by anything in this project's own AI-assisted sessions — see below).** `plan_name` was `None` for all 7 term-assurance Layer 1 records at the time this was written — a real, universal extraction gap despite `plan_name` being a required top-level field. Went unnoticed until query pipeline step 8 (narrative generation) needed a human-readable plan name and got the literal string "None" instead (a downstream code bug — `.get("plan_name", default)` not handling a present-but-`None` value — was fixed then; see `docs/query_architecture.md`'s open questions history). **Checked directly 2026-08-08: every one of the 7 policies' current `extracted/layer1_*.json` has a real, correct `plan_name` — this is stale, not current.** Git history traces the fix to `8494330` ("Re-extract Layer 1 for 875, 876, 954, 955 with sum_assured_type", 2026-07-27) predating this note's own "found 2026-08-01" date — the underlying data was already fixed as a side effect of an unrelated re-extraction before the note was even written, and nobody reconciled the doc against current data since. Root cause of the *original* gap still not investigated (moot now, but worth knowing this doc can drift out of sync with actual file contents — verify against the files directly before trusting an "open" data-gap claim here, don't just cite the doc).
- Every LIC `policy_doc`'s opening page prints "Registration Number: 512" — LIC's own constant IRDAI corporate registration number, identical across every LIC product, not plan-specific. It also happens to match the numeric UIN prefix on many plans (e.g. "512" in `512N351V02`), making it look plausible as a fallback identifier. Found on New Tech-Term (954) and New Jeevan Amar (955), both of which have a blank/unfilled "Plan Number" field in their Schedule and no plan number printed anywhere else — the model extracted the Registration Number ("512") as `policy_id` for both instead of falling back to the UIN, and without flagging `low_confidence_fields` at all. Never treat the Registration Number as `policy_id`, including as a fallback — the correct fallback when no real Plan Number exists is the UIN (per the caveat above), always flagged via `low_confidence_fields`.
- Some documents offer a choice between a "Level Sum Assured" and an "Increasing Sum Assured" death benefit structure and print a full separate sample-premium table for each, at identical age/Sum Assured/Term/`premium_payment_option` combinations — every field looks the same except `premium_amount` (the increasing-SA table always pricier). Found on 4/7 term-assurance policies (875, 876, 954, 955) 2026-07-27, confirmed against the raw PDFs — before the `sum_assured_type` field existed, these rows were extracted as exact-duplicate-looking entries with conflicting `premium_amount` and no way to tell them apart downstream (query pipeline testing caught this as an "ambiguous row" case before the root cause was identified). Tag every row's `sum_assured_type` from the document's own heading immediately before each table (see `prompt_a_pdf.txt` trap 16) — never infer it from table order or position, since nothing guarantees which structure's table is printed first.
- **Found 2026-08-01, fixed properly 2026-08-05:** `rebate_structures.high_sum_assured_rebate_table` was a flattened prose string, not structured data, on all 7/7 term-assurance policies — not an isolated gap. `prompt_a_pdf.txt` line 72's own schema typed this field `string | null`, so the model was extracting exactly as instructed; not a model quality issue. Two distinct failure shapes found on audit: 875/878/954 are pure placeholders with zero real numbers (e.g. "As specified in the brochure..."); 859/877/955 got the real numbers but flattened into one prose sentence. Table *shape* also genuinely differs per policy (876/877's 4-SA-band x 2-age-band percentage table vs. 859's flat per-mille-of-BSA multiplier vs. 955's 3-age-tier table) — any fix must mirror each document's own table structure, not force one shape onto all.
  - **2026-08-01's fix was hand-transcription, not a real fix — corrected 2026-08-05.** 876 was manually retyped from the PDF into JSON rather than re-extracted, and the prompt update (trap 17, `object | null`) was written but never actually run against any policy — so nothing in this project had ever produced this field from the pipeline itself. Caught when the user pushed back on doing the same hand-edit for 859: *"we can't go around fixing the extraction result of each policy document... what is even the point of structured extraction."* Right fix: strengthen the prompt further, then let Gemini actually produce the structured data.
  - Trap 17 now requires a top-level `"type"` key (`"percent_of_tabular_premium"` | `"per_mille_of_sum_assured_rupees"` | `"other"`, with a `type_note` explaining any `"other"` shape) so a downstream reader can dispatch on the rebate formula's real shape instead of inferring it from prose `units` text. Both known types must bottom out in an explicit numeric `sa_bands` array (`{min, max (null = open-ended), ...}`, **inclusive max**) — no more string-encoded band names like `"50L_to_1Cr"` to parse, and no lower/upper-bound-inclusivity convention to document or get wrong.
  - Re-ran real Layer 1 + Layer 2 extraction (not a hand edit) for both 859 and 876 against the strengthened prompt, verified before adopting: 859's re-extracted numbers matched an independent manual PDF check exactly. **Correction, 2026-08-08: this entry previously claimed the re-extraction also fixed two "bonus" bugs (`entry_age_min`/`entry_age_max` null, `plan_name` null) — false, caught while investigating a different policy.** Both fields were already correct in the pre-existing file (`age_at_entry_min: 18`, `age_at_entry_max: 65`, `plan_name: "LIC's Saral Jeevan Bima"`, confirmed against git history) — the "bug" was checking a field name (`entry_age_min`/`entry_age_max`) that never existed in this schema (the real name is `age_at_entry_min`/`age_at_entry_max`, per the Layer 1 schema below), which trivially returns `None` regardless of what the data actually holds. The only real fix from this re-extraction was the rebate table itself. 876's re-extracted rebate percentages matched the existing scraped-ground-truth QA checks exactly (all 4 pass); its extraction *did* omit two investigation notes from 2026-08-01's manual pass (the `31_to_50_years` vs `31_to_45_years` age-band inconsistency, and the `policy_term_max` Increasing-SA banding gap noted in Open Questions below) that came from deeper manual cross-checking, not just table transcription — those were restored by hand since they're still true findings, not re-derived from a naive prompt read.
  - **875 (Yuva Term) re-extracted 2026-08-08**, closing one of the original "pure placeholder" bucket entries. Verified against the raw brochure PDF directly before adopting: same `percent_of_tabular_premium` shape as 876 (4 SA bands x 2 age bands x level/increasing x regular/single), genuinely different rates (e.g. up_to_30/level/regular: 876 is 0/18/30/37, 875 is 0/18/33/40) — confirms these are two real, independently-priced rebate tables, not a copy-paste error on either extraction. Structure held consistent with 876/859 this time (`"table"`, explicit numeric `sa_bands`) without needing a second prompt-tightening pass, unlike 876's first attempt.
  - **All 7 term-assurance policies now have real, typed `rebate_structures` (877/878/954/955 re-extracted 2026-08-08, closing this gap entirely).** 878 (pure placeholder) and 954 (pure placeholder) both came back clean on the first attempt, verified against their PDFs exactly (878: 4-band x 2-age, same shape as 876/875; 954: 3-SA-band x 3-age-tier, a third distinct shape). 955 (real numbers, previously flattened to prose) also came back clean first try, matching its own 3-age-tier structure exactly.
  - **877 needed 3 extraction attempts and one narrow hand-correction — the most extraction trouble any policy has given this project.** The rebate table itself was correct on every attempt (including correctly capturing that 877's SA bands start at 20L, not 50L like 876/875/878 — a real, policy-specific difference, not an error). But other fields kept failing in different ways each run: attempt 1 had `sum_assured_max` wrong by 10x (500000000 vs the brochure's clearly-printed 50000000) *and* a sample-premium row that violated the prompt's own trap 12 rule (`premium_payment_option: "single"` paired with `payment_mode: "annual"`, which trap 12 explicitly forbids); attempt 2 fixed both but reproduced a pre-existing error in `premium_payment_options` (wrongly including `"regular"` — the brochure states only Limited/Single are offered, confirmed by direct PDF text: "Premiums can be paid either under Limited Premium or Single Premium payment options"); attempt 3 fixed that too, but mislabeled one specific sample-premium cell (age 40, the PPT-15 column) as `limited_ppt_10` — a *different* wrong label than attempt 1's, which had mislabeled the same cell `single`. All 3 attempts got the cell's `premium_amount` (11,650) correct, only the column label was wrong, each time differently. This one field was corrected by hand against the brochure's own table and flagged via `extraction_notes.field_specific_notes` with the full reasoning — the one exception to this project's "never hand-fix extraction" rule so far, justified narrowly: not a shortcut to avoid re-running the pipeline, but a documented correction after 3 independent pipeline runs and cross-checking a pre-existing extraction that happened to have this one cell right.
  - **Lesson: verifying only the target field (the rebate table) isn't enough — re-extraction is a full re-run, and other fields can regress even when the one field you're focused on comes back clean every time.** Every re-extraction in this project from 2026-08-05 onward has been diffed field-by-field against the prior file and cross-checked against the source PDF before adopting, which is what caught 877's issues — a re-extraction that only checked the rebate table would have silently shipped a 10x-wrong `sum_assured_max`.
  - The first attempt at strengthening trap 17 still wasn't tight enough: re-extracting 876 produced a technically-correct-but-differently-shaped object (`"options"` instead of `"table"`, SA bands as nested string keys like `"50l_to_less_than_1cr"` instead of the numeric array) — same real numbers, but not parseable by a generic reader without either hardcoding that run's exact key names or re-adding string-band parsing. Tightened trap 17 once more to pin the `sa_bands` leaf shape explicitly regardless of how many `dimensions` precede it, re-ran, got a consistent, generically-navigable structure. Extraction prompts that leave a structural choice this open will get a structurally different (if numerically correct) answer on every run — pin exactly the leaf shape a downstream reader depends on, not just "structured, not prose."
  - The other 5 policies' JSON (875, 877, 878, 954, 955) still hold the old prose/placeholder strings and haven't been re-extracted against the current prompt yet — do that before building a premium lookup for any of them, same verify-before-adopting process as above (877/955's real numbers would otherwise be lost by a naive re-run rather than reshaped, same risk 859 was under 2026-08-01).

## Layer 1 — term assurance (built first; locked for this category, other categories not yet scoped at this depth)

    policy_id                            // brochure's Plan Number, not plan_name
                                          // or UIN — see extraction-rule caveats above
    plan_name, uin, plan_category: "term_assurance"

    premium_payment_options: ["single" | "regular" | "limited"]
    ppt_options: []                      // e.g. [5, 10] for limited premium

    sum_assured_min, sum_assured_max     // max nullable — "No Limit, subject
                                          // to underwriting" is real (New Tech
                                          // Term, New Jeevan Amar). Both are
                                          // the GENERAL rule's value only —
                                          // age-band/purpose-conditional
                                          // exceptions or case-by-case
                                          // underwriting discretion go in
                                          // extraction_notes.field_specific_notes,
                                          // never substituted into the field
                                          // (confirmed on Yuva Credit Life:
                                          // general min 50L, conditional 20L
                                          // for ages 21-45 tied to a housing loan)
    sum_assured_multiples                // tiered by band in some plans

    age_at_entry_min, age_at_entry_max
    age_at_maturity_min                  // nullable — present in Yuva Term /
                                          // Digi Term / both Credit Life,
                                          // absent elsewhere
    age_at_maturity_max
    policy_term_min, policy_term_max

    death_benefit_formula: {
      regular_limited_premium: "highest of [10x annualized premium |
                                  105% premiums paid | absolute SA]",
      single_premium: "higher of [125% single premium | absolute SA]"
    }
    maturity_benefit: "none"             // confirmed all 6 term plans reviewed
                                          // so far, kept as a field since it
                                          // varies by category

    surrender_value_applicable: boolean
    policy_cancellation_value_formula    // conditional: present for
                                          // limited/single premium, absent
                                          // for regular
    grace_period_days: { yearly_halfyearly: int, monthly: int }
    free_look_period_days
    suicide_exclusion: { months: int, payout_pct_single: int,
                          payout_pct_regular_limited: int }
    rebate_structures: { high_sum_assured_rebate_table,
                          online_sale_rebate_table, mode_loadings }
    death_benefit_instalment_option: boolean
    sample_illustrative_premiums         // brochure only, per merge rule;
                                          // documents may carry multiple
                                          // tables (different SA/term
                                          // baselines, or split by channel/
                                          // payment mode) — extract every
                                          // row from every table, don't stop
                                          // at the first. Each row:
                                          //   premium_payment_option: "single" |
                                          //     "limited_ppt_5" | "limited_ppt_10" |
                                          //     "limited_ppt_15" | "regular"
                                          //   payment_mode: "annual" |
                                          //     "half_yearly" | "monthly" |
                                          //     "lump_sum"
                                          //   distribution_channel: "offline" |
                                          //     "online" | "not_specified"
                                          //     (not_specified when a table
                                          //     doesn't split by channel)
                                          //   sum_assured_type: "level" |
                                          //     "increasing" — added 2026-07-27
                                          //     after finding 4/7 term policies
                                          //     (875, 876, 954, 955) print a full
                                          //     second sample-premium table for an
                                          //     increasing-sum-assured option,
                                          //     identical on every other field —
                                          //     without this tag those rows are
                                          //     indistinguishable duplicates with
                                          //     conflicting premium_amount. Read
                                          //     from the document's own heading
                                          //     (see prompt_a_pdf.txt trap 16),
                                          //     never inferred from table order.
                                          //     Defaults to "level" when a
                                          //     document offers no such choice.
                                          //   premium_amount: number
                                          //     (renamed from annual_premium)
                                          //   derived: boolean — false for
                                          //     everything pulled straight
                                          //     from a table; true only for
                                          //     values computed downstream
                                          //     from online_sale_rebate_table,
                                          //     never computed at extraction time
    compatible_riders: []                // empty valid — most term plans
                                          // reviewed so far have none, only
                                          // plans naming a specific rider by
                                          // UIN are true positives (see
                                          // extraction-rule caveats above)

    NOT YET VERIFIED — flagged, do not treat as extracted fact:
    waiting_period_days                  // confirmed 45 days for Saral Jeevan
                                          // Bima only, not checked on other 5
    outstanding_loan_schedule_reference  // credit-life field, decreasing
                                          // cover confirmed to exist, formula
                                          // shape not extracted

    extraction_notes: {                  // sits alongside layer1, not inside it
      source_provenance: {},             // which fields came from policy_doc
                                          // vs brochure
      field_conflicts: [{ field, policy_doc_value, brochure_value }],
      low_confidence_fields: [string],   // bare dot-paths only (see dot-path
                                          // convention above)
      fields_not_found: [string],
      field_specific_notes: [{ field, note }]  // structured home for every
                                          // "extraction trap" caveat above —
                                          // keeps the field name and its
                                          // explanatory note as two separate
                                          // pieces of data, never merged into
                                          // one string
    }

## Layer 2 — normalized decision layer (identical schema across all categories)

Derived from Layer 1 at ingestion time. Group C is a direct copy of Layer 1 bounds. Group B restructures Layer 1's payout language into a controlled vocabulary. Group A is the only genuinely interpretive layer.

    Group A — concern_tags (array):
      "income_replacement", "debt_linked_cover", "child_education_fund",
      "retirement_income", "estate_legacy_planning",
      "forced_savings_discipline", "medical_critical_illness_addon",
      "liquidity_via_policy_loan"

      No "other" catch-all in this group — unlike Group B, these tags are
      interpretive judgments, not facts extracted from printed text. An
      "other" bucket here fails silently (a policy just never matches a
      concern-based search) rather than surfacing for review. The correction
      mechanism is manual: read documents, find a real recurring concern the
      tags miss, add it as a named tag.

      **Checklist for adding/removing a tag** (this vocabulary is duplicated
      in 3 places by necessity, not by accident — each serves a different
      consumer, so there's no single file to edit):
      1. Edit the list here AND in `docs/prompts/prompt_b.txt`'s
         `group_a_concern_tags` field (the actual Gemini prompt — this is
         what tells the model which tags it may assign).
      2. Edit `query/concern_tags.py`'s `CONCERN_TAG_PHRASES` (add/remove the
         entry, with a query-time phrase for a new tag).
      3. Re-run Layer 2 derivation (`extraction_test/run_layer2_derivation.py`,
         from each policy's existing Layer 1 JSON — no need to re-run Layer 1)
         for every already-extracted policy, so their `group_a_concern_tags`
         reflect the new vocabulary.
      4. Re-run `chunking/precompute_rerank_scores.py` so the new/changed tag
         has real scores in `chunking/precomputed_rerank_scores.json` — a
         removed tag's old rows are harmless dead weight, a new tag has zero
         rows (and will be silently un-matchable) until this runs.
      Steps 3-4 cost real Gemini/Voyage time and are **not automated** — the
      954/955 `policy_id` bug (`docs/progress/20260731-progress.md`) was
      exactly this class of mistake (a manual, easy-to-forget re-sync step),
      so run `python3 check_concern_tags_sync.py` from the repo root after
      any tag change — it checks steps 1-4 landed everywhere without calling
      Gemini or Voyage, and points back here if anything drifted.
      `chunking/ingest_policy.sh` also runs this check automatically as its
      last step for every new-policy ingestion (not just tag changes) — a
      free safety net against a freshly-derived Layer 2 assigning a
      concern_tag outside the vocabulary, independent of this checklist.

    Group B — payout mechanics (arrays — confirmed necessary, real plans
    combine values, e.g. Jeevan Umang pays periodic survival benefit AND
    lump sum at maturity simultaneously):
      payout_on_death: ["lump_sum" | "instalments_available" |
                         "decreasing_schedule" | "other"]
      payout_on_survival: ["none" | "lump_sum_at_maturity" |
                            "periodic_survival_benefit" | "bonus_accrual" |
                            "other"]
      payout_notes: string | null   // required if "other" present above
      is_participating: boolean
      builds_cash_value: boolean
      cash_value_loan_available: boolean
      cover_basis: "fixed" | "decreasing_loan_linked"

    Group C — deterministic pre-filter facts (direct copy from Layer 1):
      min_age, max_age, min_sum_assured, max_sum_assured, min_term, max_term
      compatible_riders: []

## Open questions

- **`policy_term_max` can't represent an Increasing-Sum-Assured-specific banding (found 2026-08-01, 876 only checked so far):** Layer 1 has a single scalar `policy_term_max` per policy, but 876's brochure (section 2(g), page 3-4) shows this scalar (40) applies only to the Level Sum Assured death benefit option. Under Increasing Sum Assured, maximum Policy Term is instead banded jointly by age-at-entry AND Basic Sum Assured range (e.g. Regular/Limited Premium, age 36-45 at Rs.50L-<1Cr caps at PT 26, not 40; age 40-45 at Rs.1Cr-<2.5Cr caps at PT 21), with a separate set of bands again for Single Premium — flagged on 876 via `extraction_notes.field_specific_notes` rather than fixed, since representing this needs a structured field (age band x SA band x payment-type -> max term), not a scalar, and that's a real schema change, not a one-off data fix. Not yet checked whether this also applies to the other 3 policies that offer an Increasing SA option (875, 954, 955) — do that before designing the structured replacement, since the banding shape may differ per policy. Until this is added, any query-pipeline eligibility check against `policy_term_max` for a `sum_assured_type: "increasing"` candidate is checking too permissive a bound.
