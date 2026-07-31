# Shared Group A concern_tags -> natural-language phrase mapping (docs/schema.md).
# Used by narrative_retrieval.py (live query text synthesis) and
# chunking/precompute_rerank_scores.py (offline per-tag precompute) - both need the exact
# same mapping, so it lives here once rather than duplicated.
#
# Fixed set, no "other" catch-all - these are interpretive judgments (docs/schema.md),
# not facts extracted from printed text, so the tag set only grows via a deliberate schema
# change, not silently.
CONCERN_TAG_PHRASES = {
    "income_replacement": "replacing my income for my family if I die",
    "debt_linked_cover": "covering an outstanding loan or debt",
    "child_education_fund": "funding my child's education",
    "retirement_income": "building retirement income",
    "estate_legacy_planning": "estate and legacy planning for my heirs",
    "forced_savings_discipline": "a disciplined, structured way to save",
    "medical_critical_illness_addon": "critical illness or medical cover",
    "liquidity_via_policy_loan": "being able to borrow against the policy if I need cash",
}


def synthesize_query_text(concern_tags):
    """Deterministic concern_tags -> natural-language query string, no LLM call. Used by
    the live path (narrative_retrieval.py) when combining multiple tags into one search
    query. The offline precompute path (chunking/precompute_rerank_scores.py) reranks each
    tag's phrase independently instead - see that module's docstring for why."""
    phrases = [CONCERN_TAG_PHRASES[tag] for tag in concern_tags]
    return "Looking for a life insurance policy for " + "; ".join(phrases) + "."
