# Step 8 of the query pipeline (docs/query_architecture.md): Gemini flash-lite explains the
# top 3 ranked candidates (from rank_candidates() or the precomputed-lookup equivalent) in
# plain language, grounded in each policy's actual Layer 3 narrative chunks - never
# hallucinated - and discloses that shown premiums are reference estimates from linear
# interpolation, not exact quotes. The only genuinely slow step in the pipeline.
#
# Scope note: "plan + rider combos" per the doc is not yet possible - steps 3-5 don't
# implement rider eligibility/combination yet (docs/query_architecture.md's "Explicitly
# deferred" section covers rider-selection UI, but the underlying rider matching logic
# itself isn't built either) - this generates for base plans only until that lands.
#
# Uses the same google-genai client pattern as extraction_test/run_layer2_derivation.py.
# Unlike Voyage, Gemini's free tier isn't a practical constraint here - exactly one call per
# query (no per-chunk batching needed), matching this project's validated flash-lite usage
# elsewhere (docs/ingestion_architecture.md step 2).

import os
import re

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-flash-lite-latest"
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "prompts", "prompt_c_narrative.txt")


def plan_name_for(layer1_record, policy_id):
    # `plan_name` is a TOP-LEVEL field on each extracted/layer1_<id>.json (alongside
    # policy_id/uin/plan_category), NOT nested under the "layer1" sub-object where the rest
    # of the extracted fields live - confirmed 2026-08-13 that this function's original
    # version (`layer1_record["layer1"].get("plan_name")`) was reading the wrong path and
    # always silently getting None, since day one. Went unnoticed because the `or` fallback
    # to policy_id quietly "worked" (Gemini still named the plan correctly in its narrative
    # body, sourced from the grounding chunks rather than this hint) - only surfaced once
    # this function started being used for something that shows its return value directly
    # (deterministic headings, the frontend's candidate list) instead of just as one hint
    # among many in a prompt. The `or` here still matters for its original, real reason:
    # `.get("plan_name", default)` only falls back when the KEY is missing, not when it's
    # present with value None - confirmed 2026-08-01 that plan_name was None (not absent)
    # for all 7 term-assurance records at the time, a real extraction gap since fixed (see
    # docs/schema.md) but the defensive fallback is cheap insurance against a future
    # extraction regressing the same way. Public (not _plan_name) as of 2026-08-13 - reused
    # by run_query_pipeline.py's top3 output so the frontend can show a real plan name
    # instead of a raw policy_id (a LIC UIN for some policies, e.g. 954/955 - not something
    # a user should ever have to read).
    return layer1_record.get("plan_name") or policy_id


def _format_candidate(rank, candidate, layer1_record, layer2_record, chunks):
    group_b = layer2_record["layer2"]["group_b"]
    plan_name = plan_name_for(layer1_record, candidate["policy_id"])
    grounding = "\n\n".join(
        f"[{c.payload['section_name']}]\n{c.payload['chunk_text']}" for c in chunks
    ) or "(no source excerpts retrieved for this plan)"

    # Deliberately NOT a markdown "###" heading here - an earlier test run showed Gemini
    # sometimes echoing this context block's own heading format verbatim ("### Candidate 1:
    # 875 (Policy ID: 875)") instead of following the prompt's instruction to head its output
    # with just the plan name - same failure shape as a prompt's own example value leaking
    # into extraction output (docs/progress/20260726-progress.md's trap 14 finding). Plain
    # text here removes the markdown pattern for the model to imitate.
    return (
        f"Candidate {rank} context (internal reference only - do not use this exact heading "
        f"format in your output; use only the real plan name, per the instructions above):\n"
        f"Plan name: {plan_name}, policy_id: {candidate['policy_id']}\n"
        f"- Estimated premium: Rs. {candidate['premium_amount']} ({candidate.get('premium_method', 'n/a')})\n"
        f"- Matches {candidate['concern_match_count']} of the user's stated concerns\n"
        f"- Payout on death: {', '.join(group_b['payout_on_death'])}\n"
        f"- Payout on survival: {', '.join(group_b['payout_on_survival'])}\n"
        f"- Participating: {group_b['is_participating']}, builds cash value: {group_b['builds_cash_value']}\n\n"
        f"Source document excerpts (use only these for factual claims about this plan):\n{grounding}"
    )


def build_prompt(profile, ranked_top3, layer1_records, layer2_records, chunks_by_policy):
    template = open(PROMPT_PATH).read()

    candidates_text = "\n\n".join(
        _format_candidate(
            i + 1, c, layer1_records[c["policy_id"]], layer2_records[c["policy_id"]],
            chunks_by_policy.get(c["policy_id"], []),
        )
        for i, c in enumerate(ranked_top3)
    )
    profile_text = (
        f"Age: {profile['age']}, desired cover: Rs. {profile['sum_assured']}, "
        f"term: {profile['term']} years, budget: Rs. {profile['budget']}/year, "
        f"stated concerns: {', '.join(profile['concern_tags'])}"
    )

    return template.replace("{{profile}}", profile_text).replace("{{candidates}}", candidates_text)


def _relabel_headings(text, ranked_top3, layer1_records):
    """Deterministically rewrites each '### <heading>' line to '### Rank N — <plan name>',
    in candidate order, instead of trusting Gemini's own heading text to actually be the
    real plan name. **Confirmed 2026-08-13 this can't be trusted**: despite the prompt
    explicitly instructing "use ONLY the real plan name... never the policy_id", real output
    still headed every candidate with its raw policy_id (e.g. "### 512N351V02", a LIC UIN a
    user should never have to read) - an instruction-following miss, not a rare fluke. Rank
    and plan name are both already known deterministically before the narrative call even
    happens (rank from list order, name from Layer 1), so there's no reason to depend on
    free-text model output for either - same bias toward determinism the rest of this
    pipeline already follows elsewhere (docs/query_architecture.md).

    Only rewrites when the number of '### ' headings found matches len(ranked_top3) exactly
    - if Gemini's actual output structure doesn't match what the prompt asked for (extra,
    missing, or nested headings), returns text unchanged rather than risking a garbled
    rewrite; a wrong-but-readable heading beats a corrupted document."""
    parts = re.split(r"(?m)^### .*$", text)
    if len(parts) - 1 != len(ranked_top3):
        return text

    rebuilt = [parts[0]]
    for i, (candidate, body) in enumerate(zip(ranked_top3, parts[1:])):
        plan_name = plan_name_for(layer1_records[candidate["policy_id"]], candidate["policy_id"])
        rebuilt.append(f"### Rank {i + 1} — {plan_name}")
        rebuilt.append(body)
    return "".join(rebuilt)


def generate_narrative(profile, ranked_top3, layer1_records, layer2_records, chunks_by_policy,
                        model=None, api_key=None):
    """ranked_top3: rank_candidates()'s output, sliced to the top 3. chunks_by_policy:
    policy_id -> list of Qdrant ScoredPoints (or equivalent .payload-bearing objects) from
    narrative_retrieval.retrieve_narrative_chunks, restricted to just these top-3 policy_ids -
    the grounding text this step is not allowed to contradict or go beyond.

    Raises ValueError on an empty ranked_top3 rather than calling Gemini - confirmed
    2026-08-01 that an empty candidate list produces a prompt with a blank candidates
    section, and Gemini filled the gap by hallucinating entirely fake, non-existent
    insurance products instead of refusing. Callers (e.g. run_query_pipeline.py) should
    check for zero survivors themselves and never reach this function in that case; this is
    a defense-in-depth guard against any future caller doing the same thing accidentally."""
    if not ranked_top3:
        raise ValueError("generate_narrative() called with zero candidates - would produce a prompt with no real grounding, risking hallucinated plans. Caller should handle the zero-candidate case itself instead.")

    model = model or os.environ.get("MODEL", DEFAULT_MODEL)
    client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
    prompt = build_prompt(profile, ranked_top3, layer1_records, layer2_records, chunks_by_policy)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        # temperature 0.3, not 0 - Layer 2 derivation (temperature=0) needs deterministic
        # structured extraction; this is prose explanation, where some natural variation in
        # phrasing is fine and even desirable, but 0.3 stays well short of "creative."
        config=types.GenerateContentConfig(temperature=0.3),
    )
    return _relabel_headings(response.text, ranked_top3, layer1_records)


if __name__ == "__main__":
    from load_extracted import load_layer1, load_layer2
    from eligibility_filter import apply_fallback
    from premium_interpolation import filter_by_budget
    from narrative_retrieval import retrieve_narrative_chunks
    from rerank_and_sort import rank_candidates
    from precomputed_relevance import load_precomputed, lookup_relevance_by_policy
    from clients import make_voyage_client, make_qdrant_client

    layer1 = load_layer1()
    layer2 = load_layer2()

    profile = {
        "age": 30, "sum_assured": 5000000, "term": 20,
        "concern_tags": ["income_replacement", "medical_critical_illness_addon"],
        "premium_payment_option": "regular", "budget": 10000,
    }

    eligible = apply_fallback(profile, layer2)
    survivors, _ = filter_by_budget(eligible["results"], profile, layer1)
    print(f"steps 3-5: {len(survivors)} survivors")

    precomputed = load_precomputed()
    scores, missing = lookup_relevance_by_policy(
        profile["concern_tags"], [s["policy_id"] for s in survivors], precomputed
    )
    print(f"step 7 (precomputed lookup): {len(scores)} scored, {len(missing)} missing: {missing}")

    ranked = rank_candidates(survivors, scores)
    top3 = ranked[:3]
    print("\ntop 3 candidates:")
    for c in top3:
        print(f"  {c['policy_id']}: tier={c['relevance_tier']} premium={c['premium_amount']}")

    voyage = make_voyage_client()
    qdrant = make_qdrant_client()
    chunks_by_policy = {}
    for c in top3:
        pid = c["policy_id"]
        chunks_by_policy[pid] = retrieve_narrative_chunks(
            profile["concern_tags"], [pid], voyage, qdrant, limit=9
        )

    narrative = generate_narrative(profile, top3, layer1, layer2, chunks_by_policy)
    print("\n=== Generated narrative ===\n")
    print(narrative)
