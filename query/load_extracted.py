# Loads Layer 1 / Layer 2 JSON from /extracted/ into memory, keyed by policy_id read
# from each file's own JSON content - not parsed from the filename, since a couple of
# files carry historical non-canonical names (layer1_859_flashlite_pdf_v2_run1.json etc,
# see docs/progress/). No Qdrant, no GitHub fetch - see docs/query_architecture.md's
# runtime data source note for why this reads a local clone instead.
import glob, json, os

EXTRACTED_DIR = os.path.join(os.path.dirname(__file__), "..", "extracted")


def _load(pattern, extracted_dir):
    records = {}
    for path in glob.glob(os.path.join(extracted_dir, pattern)):
        with open(path) as f:
            data = json.load(f)
        records[data["policy_id"]] = data
    return records


def load_layer1(extracted_dir=EXTRACTED_DIR):
    return _load("layer1_*.json", extracted_dir)


def load_layer2(extracted_dir=EXTRACTED_DIR):
    return _load("layer2_*.json", extracted_dir)


if __name__ == "__main__":
    layer1 = load_layer1()
    layer2 = load_layer2()
    print(f"Loaded {len(layer1)} layer1 records: {sorted(layer1)}")
    print(f"Loaded {len(layer2)} layer2 records: {sorted(layer2)}")
