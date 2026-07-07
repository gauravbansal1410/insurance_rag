# Insurance RAG — extraction schemas

Detailed Layer 1 (category-specific extraction) and Layer 2 (normalized decision layer) schemas. Split out of `architecture.md` to keep that document focused on the stable "why" decisions — this file will grow as each plan category gets scoped and is expected to churn more often.

See `architecture.md`'s "Data layers" section for what Layer 1, Layer 2, and Layer 3 (chunk-level narrative embeddings) each are and how they relate.

## Document merge rule (each policy has a brochure + a policy_doc)
policy_doc is authoritative for every field — it runs 3-4x the section count of brochure and includes a full Definitions block brochure lacks. Brochure is used only to supply what policy_doc doesn't carry — confirmed so far to be just the sample illustrative premium table (used by the premium-interpolation query step). Track field-level provenance (policy_doc vs brochure) per field so any future conflict is traceable. Not yet diffed for actual value conflicts on overlapping fields between the two document types — do this before the merge logic is built.

## Extraction-rule caveats (found during term-assurance schema design, likely to recur in other categories)
- Tax-benefit language is identical boilerplate across documents ("consult your tax advisor") with zero discriminative power — deliberately not modeled as a concern tag.
- Mentions of "rider" in a document's own Section 45 legal boilerplate are not real rider compatibility. Only treat a plan as rider-compatible if it names a specific optional rider by UIN. A naive text-match extraction rule will misfire on this.

## Layer 1 — term assurance (built first; locked for this category, other categories not yet scoped at this depth)

    plan_name, uin, plan_category: "term_assurance"

    premium_payment_options: ["single" | "regular" | "limited"]
    ppt_options: []                      // e.g. [5, 10] for limited premium

    sum_assured_min, sum_assured_max     // max nullable — "No Limit, subject
                                          // to underwriting" is real (New Tech
                                          // Term, New Jeevan Amar)
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
    sample_illustrative_premiums         // brochure only, per merge rule
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
