#!/usr/bin/env bash
# Orchestrates full ingestion for one policy end-to-end: Layer 1+2 extraction -> Layer 3
# chunking -> embedding/Qdrant load -> precomputed rerank scores - so nothing has to be
# remembered/re-typed by hand across separate manual script invocations.
#
# Root-cause fix baked in: the first argument (<file_id>) only names Layer 1/2's output
# files (matching run_pipeline.sh's existing convention, e.g. extracted/layer1_954.json) -
# it is NOT assumed to be the canonical policy_id. After Layer 1 completes, this script
# re-reads the REAL policy_id from that JSON's own "policy_id" field and uses ONLY that for
# every downstream step (chunking, embedding, precompute). This is exactly the check that
# was missing when 954/955 were chunked manually under the wrong policy_id - their real
# policy_id is the LIC UIN, not "954"/"955" (docs/progress/20260731-progress.md) - because
# nothing forced the numeric file id and Layer 1's own extracted value to be cross-checked.
#
# Usage: ./ingest_policy.sh <file_id> <category> <policy_doc.pdf> <brochure.pdf> [model] [output_dir]
# Example:
#   ./ingest_policy.sh 954 term_assurance \
#     ../raw_pdfs/term_assurance_plans/policy_doc_954_LIC_new_tech_term.pdf \
#     ../raw_pdfs/term_assurance_plans/brochure_954_LIC_new_tech_term.pdf
set -euo pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 <file_id> <category> <policy_doc.pdf> <brochure.pdf> [model] [output_dir]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FILE_ID="$1"
CATEGORY="$2"
POLICY_DOC_PDF="$3"
BROCHURE_PDF="$4"
MODEL="${5:-}"
OUTPUT_DIR="${6:-$SCRIPT_DIR/../extracted}"

echo "=== [1/5] Layer 1 + 2 extraction (file id: $FILE_ID) ==="
"$SCRIPT_DIR/../extraction_test/run_pipeline.sh" "$FILE_ID" "$POLICY_DOC_PDF" "$BROCHURE_PDF" "$MODEL" "$OUTPUT_DIR"

LAYER1_OUT="$OUTPUT_DIR/layer1_${FILE_ID}.json"

# Re-derive the REAL policy_id from Layer 1's own output - never assume it equals FILE_ID.
POLICY_ID="$(python3 -c "import json; print(json.load(open('$LAYER1_OUT'))['policy_id'])")"
if [ "$POLICY_ID" != "$FILE_ID" ]; then
  echo "NOTE: real policy_id ($POLICY_ID) differs from file id ($FILE_ID) - using $POLICY_ID for all downstream steps."
fi

CHUNKS_OUT="$SCRIPT_DIR/chunks_${POLICY_ID}.json"

echo "=== [2/5] Layer 3 chunking (policy_id: $POLICY_ID) ==="
python3 "$SCRIPT_DIR/chunk_policy_doc.py" "$POLICY_ID" "$CATEGORY" "$POLICY_DOC_PDF" "$CHUNKS_OUT"

echo "=== [3/5] Embedding + Qdrant load ==="
python3 "$SCRIPT_DIR/embed_and_load_layer3.py" insurance_rag_layer3 "$CHUNKS_OUT"

echo "=== [4/5] Precomputed rerank scores ==="
python3 "$SCRIPT_DIR/precompute_rerank_scores.py" "$CHUNKS_OUT"

# Free (no Gemini/Voyage calls, purely local files) - catches a freshly-derived Layer 2
# referencing a concern_tag outside the intended vocabulary (a prompt-following slip),
# right when it's easiest to trace back to this specific ingestion run. Same failure shape
# as the 954/955 policy_id bug this whole script exists to prevent (docs/schema.md's Group
# A checklist) - a hard failure here, not a warning, since a real drift should stop before
# "Done" is printed.
echo "=== [5/5] Concern-tags sync check ==="
python3 "$SCRIPT_DIR/../check_concern_tags_sync.py"

echo "Done. Policy $POLICY_ID fully ingested: Layer 1/2 -> Layer 3 chunks -> Qdrant -> precomputed rerank scores."
