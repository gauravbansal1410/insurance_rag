#!/usr/bin/env python3
"""
Layer 3 chunking: structure-aware split of a policy_doc PDF into PART/Annexure
sections, ready for embedding. See docs/schema.md Layer 3 definition.

Brochure is deliberately NOT chunked - confirmed its narrative content is a
condensed subset of policy_doc (2.4x shorter, same facts, no unique language
beyond the premium table already captured in Layer 1), plus generic marketing/
corporate-history boilerplate with zero discriminative value.

Header format validated against all 7 term_assurance policy_docs - separator
character is inconsistent across documents ("PART-A", "PART– C: BENEFITS",
"PART E" with no separator at all), hence the flexible regex below rather
than one exact string match. All 7 documents produced the identical 9-section
structure (PART_A/B/C/D/F/G + ANNEXURE_1/2/3), with PART_E dropped every time
(matches schema.md's note that maturity_benefit: none is confirmed across all
term plans).

Sections under MIN_WORDS are dropped (e.g. "PART E ... Not Applicable" for
term plans with no maturity benefit) - zero retrieval value, adds noise. The
cover-page text before the first real header (LIC corporate name, UIN,
"Registration Number: 512", address/greeting block) is treated as a
pseudo-section named PREAMBLE and always dropped unconditionally (not just
when under MIN_WORDS) - it's boilerplate identical across every LIC
document and already captured in Layer 1's plan_name/uin, so it would add
retrieval noise rather than value if embedded. Logged in dropped_sections
like every other exclusion, instead of vanishing silently.

Usage: python3 chunk_policy_doc.py <policy_id> <category> <policy_doc.pdf> <out.json>
"""
import sys, json, re, subprocess, tempfile, os

MIN_WORDS = 10  # sections below this word count are dropped as near-empty

# Matches "PART" (any case - confirmed both "PART" and "Part" appear across
# documents) + optional hyphen/en-dash/space + a letter A-G + optional ": Title"
# OR "Annexure" + a number. MULTILINE so ^/$ anchor per line, not whole string.
# [ \t\f]* (not \s*) around the header so it can never cross a newline and
# swallow the following line's body text as the title - confirmed necessary,
# a bare \s* matched through the blank line after headers with no same-line
# title. \f (form feed / page break) is included because real headers are
# frequently preceded by one, appearing as the first character of the line.
#
# Case-insensitive matching is necessary ("PART" vs "Part" both occur), but
# on its own it also matches wrapped body text where a sentence like "...Part
# C of this Policy Document" happens to fall at the start of a line purely
# from word-wrap, not because it's a real header - confirmed on 4 of 7 real
# documents (875, 876, 877, 878). Leading whitespace/indent is the
# discriminator: real headers sit at indent >=24, these false positives sat
# at indent 4-8. MIN_INDENT of 15 sits safely between the two with margin,
# and was confirmed to correctly filter every false positive found across
# the full 7-document corpus.
MIN_INDENT = 15

HEADER_RE = re.compile(
    r'^([ \t\f]*)(PART)[ \t]*[-–]?[ \t]*([A-G])\b(?:[ \t]*[:–-]?[ \t]*([^\n]*))?$'
    r'|^([ \t\f]*)(Annexure)[ \t]*(\d+)[ \t]*$',
    re.MULTILINE | re.IGNORECASE
)


def pdf_to_text(pdf_path):
    # Write the intermediate .txt to a tempfile, not next to pdf_path -
    # raw_pdfs/ is source material the README marks "do not modify
    # manually", and pdftotext writing a sibling .txt there would pollute it.
    with tempfile.TemporaryDirectory() as tmpdir:
        txt_path = os.path.join(tmpdir, "out.txt")
        subprocess.run(["pdftotext", "-layout", pdf_path, txt_path], check=True)
        with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()


def split_sections(text):
    raw_matches = list(HEADER_RE.finditer(text))
    # Filter out low-indent false positives (wrapped body text that happens to
    # start a line with "Part X") before using match positions as section
    # boundaries - must filter before slicing, not after, or a false positive
    # corrupts the boundaries of its real neighboring sections too.
    matches = []
    for m in raw_matches:
        indent = len(m.group(1)) if m.group(2) else len(m.group(5))
        if indent >= MIN_INDENT:
            matches.append(m)

    sections = []
    # Text before the first real header (cover-page address block, greeting
    # letter) - captured as its own pseudo-section so it goes through the
    # same drop-and-log path as everything else, instead of vanishing
    # silently.
    if matches:
        preamble = text[:matches[0].start()].strip()
        sections.append({"section_name": "PREAMBLE", "section_title": "PREAMBLE", "body": preamble})

    for i, m in enumerate(matches):
        if m.group(2):  # PART match
            name = f"PART_{m.group(3).upper()}"
            title = (m.group(4) or "").strip() or name
        else:  # Annexure match
            name = f"ANNEXURE_{m.group(7)}"
            title = name
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append({"section_name": name, "section_title": title, "body": body})
    return sections


def chunk(policy_id, category, pdf_path):
    text = pdf_to_text(pdf_path)
    sections = split_sections(text)
    chunks, dropped = [], []
    for sec in sections:
        word_count = len(sec["body"].split())
        # PREAMBLE (cover-page corporate name/UIN/registration-number block)
        # is always dropped regardless of word count - it's boilerplate
        # identical across every LIC document and already captured in
        # Layer 1's plan_name/uin, not just a "too short to bother" case.
        if sec["section_name"] == "PREAMBLE" or word_count < MIN_WORDS:
            dropped.append({"section_name": sec["section_name"], "word_count": word_count})
            continue
        chunks.append({
            "chunk_id": f"{policy_id}_{sec['section_name']}",
            "policy_id": policy_id,
            "category": category,
            "source_doc": "policy_doc",
            "section_name": sec["section_name"],
            "section_title": sec["section_title"],
            "word_count": word_count,
            "chunk_text": sec["body"],
        })
    return chunks, dropped, len(sections)


if __name__ == "__main__":
    policy_id, category, pdf_path, out_path = sys.argv[1:5]
    chunks, dropped, total_sections = chunk(policy_id, category, pdf_path)
    with open(out_path, "w") as f:
        json.dump(
            {"policy_id": policy_id, "category": category, "chunks": chunks, "dropped_sections": dropped},
            f, indent=2,
        )
    print(f"Found {total_sections} sections. Saved {len(chunks)} chunks, dropped {len(dropped)} as near-empty -> {out_path}")
