# Layer 1 extraction: sends a prompt + attached PDFs (policy_doc + brochure) to Gemini,
# gets back the category-specific extraction JSON (see docs/schema.md for the schema
# and docs/prompts/prompt_a_pdf.txt for the extraction rules/traps).
#
# PDFs are uploaded via the Files API and attached natively (not converted to text first) -
# testing found this meaningfully more reliable than pdftotext-extracted text for catching
# every table in a document, especially when two tables share one heading with no visual
# break in flattened text. See docs/architecture.md ingestion step 2 for the full comparison.
import os, sys, json
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def run(prompt_path, model, pdf_paths, out_path):
    prompt = open(prompt_path).read()
    # Each PDF becomes a separate uploaded file object; Gemini treats the list of
    # [prompt, file1, file2, ...] as one multi-part message, with the files available for
    # both text and layout/table-structure understanding.
    uploaded = [client.files.upload(file=p) for p in pdf_paths]
    contents = [prompt] + uploaded

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,  # deterministic-as-possible output; extraction should not be "creative"
            response_mime_type="application/json",  # forces valid JSON syntax, sidesteps the markdown-fence problem
        ),
    )
    data = json.loads(response.text)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {out_path}")

if __name__ == "__main__":
    # usage: python3 run_layer1_extraction.py prompt_a_pdf.txt out.json model_name policy_doc.pdf brochure.pdf
    # Every argument after model_name is treated as a PDF path to attach, in order given
    # (prompt_a_pdf.txt expects policy_doc first, brochure second - see the prompt's final
    # paragraph, which tells the model which attachment is which).
    prompt_path, out_path, model = sys.argv[1], sys.argv[2], sys.argv[3]
    pdf_paths = sys.argv[4:]
    run(prompt_path, model, pdf_paths, out_path)
