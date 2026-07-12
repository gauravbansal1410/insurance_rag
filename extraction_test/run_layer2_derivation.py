# Layer 2 derivation: takes ONLY a Layer 1 JSON extraction (no PDFs, no source docs at all)
# and asks Gemini to derive the normalized decision-layer JSON from it (concern tags, payout
# mechanics, deterministic filter bounds - see docs/schema.md for the schema).
#
# Source docs are deliberately NOT passed here. An earlier version attached policy_doc/brochure
# PDFs for extra "grounding," but testing found it added no benefit and introduced a real bug:
# the model would sometimes re-derive a Group C bound (e.g. a Sum Assured minimum) straight from
# the raw text instead of copying Layer 1's already-resolved value, reintroducing an ambiguity
# Layer 1 had already fixed. See docs/architecture.md ingestion step 2b for the full writeup.
import os, sys, json
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def run(prompt_path, model, replacements, out_path):
    prompt = open(prompt_path).read()
    # Simple find/replace: each `{{key}}` placeholder in the prompt template gets swapped for
    # the contents of the file at `replacements[key]`. Currently only used for {{layer1_json}}
    # (see docs/prompts/prompt_b.txt), but works for any number of text placeholders.
    for key, filepath in replacements.items():
        prompt = prompt.replace(key, open(filepath).read())

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,  # deterministic-as-possible output; derivation should not be "creative"
            response_mime_type="application/json",  # forces valid JSON syntax, sidesteps the markdown-fence problem
        ),
    )
    data = json.loads(response.text)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {out_path}")

if __name__ == "__main__":
    # usage: python3 run_layer2_derivation.py prompt_b.txt out.json model_name layer1_json=path
    # Each trailing argument must be in key=path form (e.g. layer1_json=layer1_859.json) - the
    # key names the {{placeholder}} in the prompt file to substitute, the path is the file whose
    # contents get inserted there.
    prompt_path, out_path, model = sys.argv[1], sys.argv[2], sys.argv[3]
    replacements = {}
    for arg in sys.argv[4:]:
        tag, path = arg.split("=", 1)
        replacements[f"{{{{{tag}}}}}"] = path
    run(prompt_path, model, replacements, out_path)
