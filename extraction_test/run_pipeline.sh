#!/usr/bin/env bash
# Orchestrates one policy through the two-stage ingestion pipeline:
#   1. Layer 1 extraction  (run_layer1_extraction.py) - reads the raw policy_doc/brochure PDFs,
#      produces the category-specific extraction JSON (see docs/schema.md).
#   2. Layer 2 derivation  (run_layer2_derivation.py) - reads ONLY Layer 1's JSON output (no PDFs -
#      see docs/architecture.md ingestion step 2b for why source docs are deliberately withheld here),
#      produces the normalized decision-layer JSON used by the query pipeline.
# Each stage's output is checked (file non-empty + valid JSON) before moving to the next step, so a
# malformed Layer 1 result can't silently produce garbage Layer 2 output.
#
# Reads GEMINI_API_KEY and MODEL from .env at the repo root if present (see .env.example).
# The <model> argument is optional if MODEL is set in .env; an explicit argument always wins.
#
# Usage: ./run_pipeline.sh <policy_id> <policy_doc.pdf> <brochure.pdf> [model] [output_dir]
# Example:
#   ./run_pipeline.sh 859 \
#     ../raw_pdfs/term_assurance_plans/policy_doc_859_LIC_saral_jeevan_bima.pdf \
#     ../raw_pdfs/term_assurance_plans/brochure_859_LIC_saral_jeevan_bima.pdf

set -euo pipefail  # exit on error, exit on unset variable, fail a pipeline if any stage fails

if [ "$#" -lt 3 ]; then
  echo "Usage: $0 <policy_id> <policy_doc.pdf> <brochure.pdf> [model] [output_dir]" >&2
  exit 1
fi

# Resolve paths relative to this script's own location (not the caller's cwd), so it works
# whether invoked as ./run_pipeline.sh from inside extraction_test/ or via a full/relative path
# from elsewhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load GEMINI_API_KEY / MODEL from .env if it exists. `set -a` exports every variable sourced
# from here (so the python subprocesses below inherit GEMINI_API_KEY automatically without
# needing their own dotenv logic); `set +a` turns that auto-export back off afterward so it
# doesn't leak into unrelated variables set later in this script.
ENV_FILE="$SCRIPT_DIR/../.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

POLICY_ID="$1"
POLICY_DOC_PDF="$2"
BROCHURE_PDF="$3"
# Precedence: explicit 4th CLI argument wins; otherwise fall back to MODEL from .env (loaded above).
MODEL="${4:-${MODEL:-}}"
OUTPUT_DIR="${5:-$SCRIPT_DIR}"

PROMPT_A="$SCRIPT_DIR/../docs/prompts/prompt_a_pdf.txt"
PROMPT_B="$SCRIPT_DIR/../docs/prompts/prompt_b.txt"

LAYER1_OUT="$OUTPUT_DIR/layer1_${POLICY_ID}.json"
LAYER2_OUT="$OUTPUT_DIR/layer2_${POLICY_ID}.json"

if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "ERROR: GEMINI_API_KEY is not set (checked environment and $ENV_FILE)." >&2
  exit 1
fi
if [ -z "$MODEL" ]; then
  echo "ERROR: no model specified (checked 4th argument and MODEL in $ENV_FILE)." >&2
  exit 1
fi

# --- Stage 1: Layer 1 extraction ---
echo "[1/2] Running Layer 1 extraction for policy $POLICY_ID..."
python3 "$SCRIPT_DIR/run_layer1_extraction.py" "$PROMPT_A" "$LAYER1_OUT" "$MODEL" "$POLICY_DOC_PDF" "$BROCHURE_PDF"

# -s checks the file exists AND is non-empty (an API error can produce a 0-byte file without
# the python script itself throwing, depending on how the failure surfaces).
if [ ! -s "$LAYER1_OUT" ]; then
  echo "ERROR: Layer 1 output $LAYER1_OUT is missing or empty." >&2
  exit 1
fi
# Belt-and-braces check: json.dump() should always produce valid JSON, but this catches the
# case where a prior failed/partial run left a stale or truncated file at this path.
if ! python3 -c "import json; json.load(open('$LAYER1_OUT'))" 2>/dev/null; then
  echo "ERROR: Layer 1 output $LAYER1_OUT is not valid JSON." >&2
  exit 1
fi
echo "[1/2] Layer 1 output verified: $LAYER1_OUT"

# --- Stage 2: Layer 2 derivation (only reached if Stage 1 passed both checks above) ---
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
