# run_extraction.py
import os, sys, json
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def run(prompt_path, model, replacements, out_path):
    prompt = open(prompt_path).read()
    for key, filepath in replacements.items():
        prompt = prompt.replace(key, open(filepath).read())

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",  # forces valid JSON syntax, sidesteps the markdown-fence problem
        ),
    )
    data = json.loads(response.text)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {out_path}")

if __name__ == "__main__":
    # usage: python3 run_extraction.py prompt_a.txt out.json model_name policy_doc=path brochure=path
    prompt_path, out_path, model = sys.argv[1], sys.argv[2], sys.argv[3]
    replacements = {}
    for arg in sys.argv[4:]:
        tag, path = arg.split("=", 1)
        replacements[f"{{{{{tag}}}}}"] = path
    run(prompt_path, model, replacements, out_path)
