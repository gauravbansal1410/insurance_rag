import os, sys, json
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def run(prompt_path, model, pdf_paths, out_path):
    prompt = open(prompt_path).read()
    uploaded = [client.files.upload(file=p) for p in pdf_paths]
    contents = [prompt] + uploaded

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    data = json.loads(response.text)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {out_path}")

if __name__ == "__main__":
    # usage: python3 run_layer1_extraction.py prompt_a_pdf.txt out.json model_name policy_doc.pdf brochure.pdf
    prompt_path, out_path, model = sys.argv[1], sys.argv[2], sys.argv[3]
    pdf_paths = sys.argv[4:]
    run(prompt_path, model, pdf_paths, out_path)
