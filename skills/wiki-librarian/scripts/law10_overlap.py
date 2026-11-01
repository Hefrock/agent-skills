#!/usr/bin/env python3
"""Law 10 (distill, don't dump) — overlap check against warehoused source text.

A note body's length says little about whether it was distilled: a short
note can be a pasted page, and a long one can be careful original writing.
What does distinguish them is how much of the note's wording appears
verbatim in the source it points to. This module measures that, using
8-word shingles against the text the warehouse extracted
(`text_path` in the manifest).

Three measurements, all reported — none replaces judgment:
  - copied_outside_quotes_pct: share of the note's words (excluding
    blockquote lines and quoted spans of 20+ characters) that sit inside an
    8-word run also present in the source. Intended excerpts don't count.
  - longest_run_words: longest unbroken copied run, quotations included.
  - quote_chars: characters inside quotations (a quote budget).

Thresholds are provisional (fail >35% copied or a 120-word run; warn >20%,
an 80-word run, or >1500 quoted characters). Length is informational only,
except for very large sources. If no source text is available the result is
"unchecked" — never a pass.

stdlib only. The pure functions take strings, so they work under an
MCP-only workflow: copy a note's body to a file, then run the CLI.
"""

import argparse
import json
import os
import re
import sys
import unicodedata

SHINGLE = 8
FAIL_COPIED_PCT = 35.0
FAIL_RUN_WORDS = 120
WARN_COPIED_PCT = 20.0
WARN_RUN_WORDS = 80
WARN_QUOTE_CHARS = 1500
MIN_QUOTE_SPAN = 20
LARGE_SOURCE_CHARS = 20000
LARGE_SOURCE_NOTE_SHARE = 0.25
FALLBACK_WARN_CHARS = 10000  # used only when no source text is available

_STRAIGHT_QUOTE = re.compile(r'"[^"\n]{%d,}"' % MIN_QUOTE_SPAN)
_CURLY_QUOTE = re.compile(r"“[^”\n]{%d,}”" % MIN_QUOTE_SPAN)


def normalize(text: str) -> str:
    """Fold ligatures/width variants, quote styles and line-break hyphens."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("­", "")  # soft hyphen
    text = re.sub(r"-\s*\n\s*", "", text)  # PDF line-break hyphenation
    return text


def tokens(text: str):
    return re.findall(r"[a-z0-9]+", normalize(text).lower())


def split_quotes(body: str):
    """Return (text outside quotations, number of characters inside them)."""
    body = normalize(body)
    kept, quote_chars = [], 0
    for line in body.split("\n"):
        if line.lstrip().startswith(">"):
            quote_chars += len(line.lstrip().lstrip(">").strip())
        else:
            kept.append(line)
    text = "\n".join(kept)
    for pattern in (_STRAIGHT_QUOTE, _CURLY_QUOTE):
        quote_chars += sum(len(m) - 2 for m in pattern.findall(text))
        text = pattern.sub(" ", text)
    return text, quote_chars


def _shingles(toks, n=SHINGLE):
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def _covered(toks, source_shingles, n=SHINGLE):
    covered = [False] * len(toks)
    for i in range(len(toks) - n + 1):
        if tuple(toks[i:i + n]) in source_shingles:
            for j in range(i, i + n):
                covered[j] = True
    return covered


def _longest_run(covered):
    best = cur = 0
    for c in covered:
        cur = cur + 1 if c else 0
        best = max(best, cur)
    return best


def measure(body: str, source_text: str) -> dict:
    src = _shingles(tokens(source_text))
    outside_text, quote_chars = split_quotes(body)
    outside = tokens(outside_text)
    outside_cov = _covered(outside, src)
    all_cov = _covered(tokens(body), src)
    return {
        "body_chars": len(body),
        "source_chars": len(source_text),
        "outside_words": len(outside),
        "copied_outside_quotes_pct": round(100.0 * sum(outside_cov) / len(outside), 1) if outside else 0.0,
        "longest_run_words": _longest_run(all_cov),
        "quote_chars": quote_chars,
    }


def classify(m: dict) -> dict:
    """Turn measurements into {"status": pass|warn|fail, "reasons": [...]}."""
    fail, warn = [], []
    pct, run = m["copied_outside_quotes_pct"], m["longest_run_words"]
    if pct > FAIL_COPIED_PCT:
        fail.append(f"{pct}% of the wording outside quotations is copied (fail above {FAIL_COPIED_PCT:g}%)")
    elif pct > WARN_COPIED_PCT:
        warn.append(f"{pct}% copied outside quotations (warn above {WARN_COPIED_PCT:g}%)")
    if run >= FAIL_RUN_WORDS:
        fail.append(f"{run}-word verbatim run (fail at {FAIL_RUN_WORDS})")
    elif run >= WARN_RUN_WORDS:
        warn.append(f"{run}-word verbatim run (warn at {WARN_RUN_WORDS})")
    if m["quote_chars"] > WARN_QUOTE_CHARS:
        warn.append(f"{m['quote_chars']} characters in quotations (warn above {WARN_QUOTE_CHARS})")
    if (m["source_chars"] >= LARGE_SOURCE_CHARS
            and m["body_chars"] > LARGE_SOURCE_NOTE_SHARE * m["source_chars"]):
        warn.append(f"note is {100 * m['body_chars'] / m['source_chars']:.0f}% of a large source's length")
    status = "fail" if fail else "warn" if warn else "pass"
    return {"status": status, "reasons": fail + warn}


def check_note(body: str, source_text):
    """Full result for one note. source_text None means no text to compare."""
    # Blank counts as missing: the warehouse writes a 0-byte .txt for types it
    # cannot extract (e.g. EPUB), and measuring against '' would pass a dump.
    if source_text is None or not source_text.strip():
        warn = len(body) > FALLBACK_WARN_CHARS
        return {
            "status": "unchecked",
            "reasons": ["no extracted source text available; overlap not measured"]
            + ([f"body is {len(body)} characters (length fallback warns above {FALLBACK_WARN_CHARS})"] if warn else []),
            "length_fallback_warn": warn,
            "body_chars": len(body),
        }
    m = measure(body, source_text)
    return {**m, **classify(m)}


def load_source_text(warehouse_root: str, doc_id: str):
    """Look up a vault `sha256:<hex>` doc_id in manifest.json; None if unavailable.

    The manifest is keyed by bare hex and is authoritative for paths
    (see wiki-warehouse/references/warehouse-schema.md). Vault doc_ids may
    be truncated (fixtures), so fall back to a unique prefix match.
    """
    try:
        with open(os.path.join(warehouse_root, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return None
    # Real manifests wrap entries as {"docs": {...}, "schema": "..."} (what
    # bin/audit.py reads); the schema doc shows the bare mapping. Accept both.
    docs = manifest.get("docs") if isinstance(manifest.get("docs"), dict) else manifest
    key = doc_id.split(":", 1)[-1].lower()
    entry = docs.get(key)
    if entry is None:
        matches = [k for k in docs if key and k.startswith(key)]
        entry = docs[matches[0]] if len(matches) == 1 else None
    if not isinstance(entry, dict) or not entry.get("text_path"):
        return None
    try:
        with open(os.path.join(warehouse_root, entry["text_path"]), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("body_file", help="File holding the note body (frontmatter optional)")
    parser.add_argument("doc_id", help="The note's doc_id, e.g. sha256:621821bc...")
    parser.add_argument("--warehouse", required=True, help="Path to the knowledge-warehouse checkout")
    args = parser.parse_args()

    with open(args.body_file, encoding="utf-8") as f:
        raw = f.read()
    if raw.startswith("---\n") and "\n---" in raw[4:]:
        raw = raw[raw.find("\n---", 4) + 4:].lstrip("\n")
    result = check_note(raw, load_source_text(args.warehouse, args.doc_id))
    print(json.dumps(result, indent=2))
    sys.exit(1 if result["status"] == "fail" else 0)


if __name__ == "__main__":
    main()
