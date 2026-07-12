#!/usr/bin/env bash
# Runs Layer 1 extraction, verifies its output, then runs Layer 2 derivation from it.
#
# Usage: ./run_pipeline.sh <policy_id> <policy_doc.pdf> <brochure.pdf> <model> [output_dir]
# Example:
#   ./run_pipeline.sh 859 \
#     ../raw_pdfs/term_assurance_plans/policy_doc_859_LIC_saral_jeevan_bima.pdf \
#     ../raw_pdfs/term_assurance_plans/brochure_859_LIC_saral_jeevan_bima.pdf \
#     gemini-flash-lite-latest

set -euo pipefail

if [ "$#" -lt 4 ]; then
  echo "Usage: $0 <policy_id> <policy_doc.pdf> <brochure.pdf> <model> [output_dir]" >&2
  exit 1
fi

POLICY_ID="$1"
POLICY_DOC_PDF="$2"
BROCHURE_PDF="$3"
MODEL="$4"
OUTPUT_DIR="${5:-$(dirname "$0")}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_A="$SCRIPT_DIR/../docs/prompts/prompt_a_pdf.txt"
PROMPT_B="$SCRIPT_DIR/../docs/prompts/prompt_b.txt"

LAYER1_OUT="$OUTPUT_DIR/layer1_${POLICY_ID}.json"
LAYER2_OUT="$OUTPUT_DIR/layer2_${POLICY_ID}.json"

if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "ERROR: GEMINI_API_KEY is not set." >&2
  exit 1
fi

echo "[1/2] Running Layer 1 extraction for policy $POLICY_ID..."
python3 "$SCRIPT_DIR/run_layer1_extraction.py" "$PROMPT_A" "$LAYER1_OUT" "$MODEL" "$POLICY_DOC_PDF" "$BROCHURE_PDF"

if [ ! -s "$LAYER1_OUT" ]; then
  echo "ERROR: Layer 1 output $LAYER1_OUT is missing or empty." >&2
  exit 1
fi
if ! python3 -c "import json; json.load(open('$LAYER1_OUT'))" 2>/dev/null; then
  echo "ERROR: Layer 1 output $LAYER1_OUT is not valid JSON." >&2
  exit 1
fi
echo "[1/2] Layer 1 output verified: $LAYER1_OUT"

echo "[2/2] Running Layer 2 derivation for policy $POLICY_ID..."
python3 "$SCRIPT_DIR/run_layer2_derivation.py" "$PROMPT_B" "$LAYER2_OUT" "$MODEL" "layer1_json=$LAYER1_OUT"

if [ ! -s "$LAYER2_OUT" ]; then
  echo "ERROR: Layer 2 output $LAYER2_OUT is missing or empty." >&2
  exit 1
fi
if ! python3 -c "import json; json.load(open('$LAYER2_OUT'))" 2>/dev/null; then
  echo "ERROR: Layer 2 output $LAYER2_OUT is not valid JSON." >&2
  exit 1
fi
echo "[2/2] Layer 2 output verified: $LAYER2_OUT"

echo "Done. $LAYER1_OUT and $LAYER2_OUT are ready."
