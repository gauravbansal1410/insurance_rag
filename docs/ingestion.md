# Insurance RAG — Ingestion pipeline

One-time / occasional, admin-triggered, not user-facing. See `docs/architecture.md`'s "Data layers" section for what Layer 1, Layer 2, and Layer 3 are and how they relate; `docs/schema.md` for full field schemas and extraction-rule caveats.

## Pipeline

1. **Upload raw PDFs to GitHub.** Current batch (~20–30 files): plain drag-and-drop via github.com. Future scraper-driven updates: a small script using GitHub's Contents API (no git clone, respects the no-clone-on-work-laptop rule).
2. **Layer 1 extraction:** Gemini `flash-lite-latest`, PDF-native input — the source PDFs are uploaded directly via the Files API and attached to the prompt, not converted to text first. Structured JSON against a core schema (premium, term, sum assured, eligibility) plus category-specific extensions (term / money-back / whole-life / endowment each have different benefit structures — decide extension fields before writing the extraction prompt for a new category). Full Layer 1 schema, document merge rule, and extraction-rule caveats: see `docs/schema.md`. Prompt: `docs/prompts/prompt_a_pdf.txt`.
   - **Why Flash-Lite, not Pro:** validated for term assurance — Pro (`gemini-3.1-pro`) has zero free-tier quota on this project's API key, and Flash-Lite with a well-trapped prompt achieved fully consistent output. Stays on Flash-Lite for the $0 cost target.
   - **Why PDF-native input, not `pdftotext` text:** PDF input is materially more reliable at catching every distinct table in a document, particularly when two tables share one heading with no visual break in flattened text. Use PDF input for ingestion; treat text-extraction as a fallback only if PDF upload isn't viable for some future document type. See `docs/progress/20260712-progress.md` for the validation numbers behind this.
3. **Layer 2 derivation:** a second, separate Gemini `flash-lite-latest` call, given ONLY the Layer 1 JSON as input — no source PDFs, no `pdftotext`. Prompt: `docs/prompts/prompt_b.txt`.
   - **Why no source docs:** an earlier version attached policy_doc + brochure PDFs for extra grounding, on the assumption Group A's concern-tag reasoning needed brochure framing Layer 1's schema doesn't capture. Testing found this added no measurable benefit while introducing a real bug — the model would sometimes re-derive a Group C bound from the raw PDFs instead of copying Layer 1's already-resolved value. For term assurance, Layer 1's own fields (`structural_variant`, `maturity_benefit`, `surrender_value_applicable`, etc.) already discriminate the concern-tag space that applies. Revisit if a future plan category's Group A tagging turns out to need brochure-only language Layer 1 doesn't capture (the PDF-grounded variant is kept at `docs/prompts/appendix/prompt_b_pdf_deprecated.txt` for that scenario).
4. **Chunking:** structure-aware — split on the document's own PART/section headers (IRDAI mandates a consistent template across LIC products), not fixed-size windows and not model-based chunking.
5. **Embedding:** Voyage `voyage-law-2` (verify current model name before building — Voyage ships new generations often). Domain-specialized for legal/insurance text, free for a corpus this size.
6. **Load into Qdrant** per the three-layer scheme in `docs/architecture.md`.

## Running the pipeline

See `CLAUDE.md`'s "Build & Run Commands" section — `extraction_test/run_pipeline.sh` runs steps 2 and 3 for one policy end-to-end, verifying each stage's output before proceeding to the next.

## Open questions

- Whether Flash-Lite + PDF input stays reliable once extraction expands beyond term-assurance to money-back / whole-life / endowment categories — only validated on 2 term policies so far.
