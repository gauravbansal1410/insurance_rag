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
- **Found 2026-08-01, not yet fixed:** `plan_name` is `None` for **all 7** term-assurance Layer 1 records, not an isolated case — a real, universal extraction gap despite `plan_name` being a required top-level field. Went unnoticed until query pipeline step 8 (narrative generation) needed a human-readable plan name and got the literal string "None" instead (a downstream code bug — `.get("plan_name", default)` not handling a present-but-`None` value — was fixed, but the underlying data gap wasn't; see `docs/query_architecture.md`'s open questions). Root cause not yet investigated — worth checking whether this affected the original extraction prompt/traps for this field specifically, since it's a basic, almost certainly-present-on-every-cover-page value, unlike genuinely ambiguous fields like Sum Assured exceptions.
- Every LIC `policy_doc`'s opening page prints "Registration Number: 512" — LIC's own constant IRDAI corporate registration number, identical across every LIC product, not plan-specific. It also happens to match the numeric UIN prefix on many plans (e.g. "512" in `512N351V02`), making it look plausible as a fallback identifier. Found on New Tech-Term (954) and New Jeevan Amar (955), both of which have a blank/unfilled "Plan Number" field in their Schedule and no plan number printed anywhere else — the model extracted the Registration Number ("512") as `policy_id` for both instead of falling back to the UIN, and without flagging `low_confidence_fields` at all. Never treat the Registration Number as `policy_id`, including as a fallback — the correct fallback when no real Plan Number exists is the UIN (per the caveat above), always flagged via `low_confidence_fields`.
- Some documents offer a choice between a "Level Sum Assured" and an "Increasing Sum Assured" death benefit structure and print a full separate sample-premium table for each, at identical age/Sum Assured/Term/`premium_payment_option` combinations — every field looks the same except `premium_amount` (the increasing-SA table always pricier). Found on 4/7 term-assurance policies (875, 876, 954, 955) 2026-07-27, confirmed against the raw PDFs — before the `sum_assured_type` field existed, these rows were extracted as exact-duplicate-looking entries with conflicting `premium_amount` and no way to tell them apart downstream (query pipeline testing caught this as an "ambiguous row" case before the root cause was identified). Tag every row's `sum_assured_type` from the document's own heading immediately before each table (see `prompt_a_pdf.txt` trap 16) — never infer it from table order or position, since nothing guarantees which structure's table is printed first.
- **Found and fixed 2026-08-01:** `rebate_structures.high_sum_assured_rebate_table` was a flattened prose string, not structured data, on all 7/7 term-assurance policies — not an isolated gap. `prompt_a_pdf.txt` line 72's own schema typed this field `string | null`, so the model was extracting exactly as instructed; not a model quality issue. Two distinct failure shapes found on audit: 875/878/954 are pure placeholders with zero real numbers (e.g. "As specified in the brochure..."); 859/877/955 got the real numbers but flattened into one prose sentence. Table *shape* also genuinely differs per policy (876/877's 4-SA-band x 2-age-band percentage table vs. 859's flat per-mille-of-BSA multiplier vs. 955's 3-age-tier table) — any fix must mirror each document's own table structure, not force one shape onto all. Fixed for 876 only so far (see the Layer 1 term-assurance schema section below for the structured example) and in the prompt (now `object | null`, trap 17 added) for future/re-extractions; the other 6 policies' JSON still hold the old prose strings and need their own source-document check before re-extracting, since 859/877/955's real numbers would otherwise be lost by a naive re-run rather than reshaped.

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
