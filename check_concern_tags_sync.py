#!/usr/bin/env python3
"""
Checks whether the Group A concern_tags vocabulary is in sync across the places it must
independently match - see docs/schema.md's "Checklist for adding/removing a tag" for why
there are several places at all, and what to do if this check finds drift.

Checked, all purely local (no Gemini or Voyage calls - re-deriving Layer 2 and
re-precomputing both cost real API time, so this only reports drift, never fixes it):
  1. docs/prompts/prompt_b.txt's group_a_concern_tags list (what Gemini is told it may
     assign) vs query/concern_tags.py's CONCERN_TAG_PHRASES keys (the canonical live set).
  2. chunking/precomputed_rerank_scores.json's top-level tag keys vs the canonical set -
     missing means a new tag has no precomputed scores yet; extra means a removed tag's
     scores are still sitting there (harmless, but stale).
  3. Every extracted/layer2_*.json's actual group_a_concern_tags values, checked for any
     tag no longer in the canonical set - a sign those policies were derived under an old
     vocabulary and need Layer 2 re-derived.

Usage: python3 check_concern_tags_sync.py
Exit code 0 if everything matches, 1 if any drift is found.
"""
import json
import os
import re
import sys
import glob

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO_ROOT, "query"))
from concern_tags import CONCERN_TAG_PHRASES  # noqa: E402

CANONICAL = set(CONCERN_TAG_PHRASES)

PROMPT_B_PATH = os.path.join(REPO_ROOT, "docs", "prompts", "prompt_b.txt")
PRECOMPUTED_PATH = os.path.join(REPO_ROOT, "chunking", "precomputed_rerank_scores.json")
EXTRACTED_GLOB = os.path.join(REPO_ROOT, "extracted", "layer2_*.json")


def tags_in_prompt_b():
    text = open(PROMPT_B_PATH).read()
    match = re.search(r'"group_a_concern_tags"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if not match:
        return None
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def tags_used_in_layer2():
    used = {}
    for path in glob.glob(EXTRACTED_GLOB):
        data = json.load(open(path))
        policy_id = data.get("policy_id", os.path.basename(path))
        for tag in data.get("layer2", {}).get("group_a_concern_tags", []):
            used.setdefault(tag, []).append(policy_id)
    return used


def tags_in_precomputed():
    if not os.path.exists(PRECOMPUTED_PATH):
        return None
    return set(json.load(open(PRECOMPUTED_PATH)).keys())


def main():
    problems = []

    prompt_tags = tags_in_prompt_b()
    if prompt_tags is None:
        problems.append(f"Could not find a group_a_concern_tags list in {PROMPT_B_PATH} - check its format.")
    elif prompt_tags != CANONICAL:
        only_prompt = sorted(prompt_tags - CANONICAL)
        only_canonical = sorted(CANONICAL - prompt_tags)
        problems.append(
            "prompt_b.txt's tag list differs from query/concern_tags.py.\n"
            f"    only in prompt_b.txt: {only_prompt or 'none'}\n"
            f"    only in concern_tags.py: {only_canonical or 'none'}"
        )

    precomputed_tags = tags_in_precomputed()
    if precomputed_tags is None:
        problems.append(f"{PRECOMPUTED_PATH} not found - nothing precomputed yet.")
    elif precomputed_tags != CANONICAL:
        only_file = sorted(precomputed_tags - CANONICAL)
        only_canonical = sorted(CANONICAL - precomputed_tags)
        problems.append(
            "precomputed_rerank_scores.json's tag keys differ from query/concern_tags.py.\n"
            f"    only in precomputed file (stale?): {only_file or 'none'}\n"
            f"    only in concern_tags.py (not precomputed yet): {only_canonical or 'none'}"
        )

    orphaned = {tag: pids for tag, pids in tags_used_in_layer2().items() if tag not in CANONICAL}
    for tag, pids in orphaned.items():
        problems.append(
            f"Tag '{tag}' appears in already-extracted Layer 2 data for policy_id(s) {pids} "
            "but is no longer in query/concern_tags.py - those policies need Layer 2 re-derived."
        )

    if problems:
        print("Concern tags are OUT OF SYNC:\n")
        for p in problems:
            print(f"- {p}\n")
        print("See docs/schema.md's Group A checklist for what to do about each of these.")
        sys.exit(1)

    print(
        f"In sync: {len(CANONICAL)} tags match across query/concern_tags.py, prompt_b.txt, "
        "precomputed_rerank_scores.json, and all extracted Layer 2 data."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
