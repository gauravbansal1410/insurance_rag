# Insurance RAG — Evaluation

Build the ground truth before the judge.

**Status: not yet built.**

- **Golden set (build now, not deferred):** 15–30 hand-verified query + expected-answer pairs, covering simple lookups, eligibility filtering, cross-plan comparisons, and edge cases (below-minimum sum assured requests). Built manually against the actual PDFs using your own domain knowledge. This is the ground-truth anchor — without it, an LLM judge is just comparing the system's output to its own opinion, which proves nothing.
- **Trace log:** append-only, separate Qdrant collection. Captures, per live query: the query text, a snapshot of retrieved chunk text (not just IDs, so a trace survives future re-embedding or re-chunking), and the final generated response. Written asynchronously so a logging failure never breaks the user-facing response. Non-blocking, captures what actually happened on live queries.
- **LLM judge (genuinely fine to defer):** an offline batch job that scores trace-log entries against the golden set. Never touches the live query path, so this is the one piece that really can be added later without rebuilding anything underneath it.
