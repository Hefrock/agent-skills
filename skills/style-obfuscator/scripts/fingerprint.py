#!/usr/bin/env python3
"""
fingerprint.py — deterministic stylometric fingerprint for a piece of writing
(tier 1 of the style-obfuscator design: flag, don't rewrite).

Computes the classic authorship-attribution feature set — function-word frequency,
sentence-length distribution, punctuation habits, vocabulary richness, an approximate
passive-voice rate, and repeated-phrase detection — over a draft, and optionally
compares it against a reference corpus of your own known writing (your vault, past
posts, anything already attributed to you). None of this needs a model or a network
call: authorship stylometry (Mosteller & Wallace, Burrows' Delta) has always been
built on word-frequency statistics, not semantic understanding — the part that
actually needs judgment is deciding what a similarity score MEANS for a specific
threat model, not computing it. See SKILL.md for why "flag" and "obscure" are
deliberately different tiers, and why only this one is built.

Usage:
    fingerprint.py --file draft.md                                  # standalone report
    fingerprint.py --file draft.md --reference known_writing.md      # + similarity
    fingerprint.py --file draft.md --reference ~/vault/Journal/       # reference = a
                                                                       # directory, all
                                                                       # .md/.txt files
                                                                       # concatenated
    fingerprint.py --text -                                          # stdin
    fingerprint.py --file draft.md --reference known.md --json
    fingerprint.py --file draft.md --reference known.md --emit-findings  # privacy-linter-
                                                                          # shaped Finding
                                                                          # JSON, for piping
                                                                          # into privacy-
                                                                          # threat-oracle
    fingerprint.py --file draft.md --reference known.md --log-dir ~/.style-obfuscator-log
    fingerprint.py --file draft.md --reference known.md --show-phrase-text  # reveal the
                                                                              # verbatim
                                                                              # repeated
                                                                              # phrases
                                                                              # (redacted
                                                                              # by default)

Every numeric feature is a rate per 1,000 words, not a raw count — that's what makes a
280-word LinkedIn post and a 4,000-word README comparable at all.

This does not rewrite anything — see SKILL.md's "What's NOT built here." It also does
not attempt real authorship *matching* against a third party's writing; the reference
corpus here is always your own, and the point is telling you what a matcher would key
on, not running the match for you.

Stdlib only — no NLP library. Tokenization and passive-voice detection are simple regex
heuristics, not POS tagging; see the relevant docstrings for exactly what that misses.
"""

import argparse
import json
import math
import os
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone

# Topic-independent, closed-class words — the actual basis of real stylometry research
# (Mosteller & Wallace's Federalist Papers study, Burrows' Delta) specifically because
# they're used unconsciously and don't vary with subject matter the way content words
# do. ~45 of the most common English function words across determiners, prepositions,
# conjunctions, pronouns, and auxiliary verbs.
FUNCTION_WORDS = [
    "the", "of", "and", "to", "a", "in", "that", "is", "was", "for", "on", "with",
    "as", "it", "at", "by", "this", "but", "from", "or", "an", "which", "not",
    "be", "are", "were", "have", "has", "had", "he", "she", "they", "we", "you",
    "i", "if", "so", "than", "then", "there", "when", "what", "who", "my", "your",
]

# Contracted forms only — any word containing an apostrophe between letters. Deliberately
# not trying to detect the *expanded* equivalent ("do not") to compute a true ratio; see
# the module docstring on scope. An absolute rate is still a real, comparable signal.
CONTRACTION_RE = re.compile(r"\b[a-z]+'[a-z]+\b", re.IGNORECASE)

# Approximate passive voice: an auxiliary form of "be" followed by a past participle.
# Catches the common regular (-ed) case plus a fixed list of frequent irregular
# participles. This is NOT real part-of-speech-based passive detection — it will
# false-positive on an adjectival use ("was excited") and false-negative on any
# irregular participle not in the list below. Treated as a directional signal, not a
# precise count, same honesty standard as privacy-linter's own approximate checks.
IRREGULAR_PARTICIPLES = (
    "written|done|seen|made|known|taken|given|shown|found|held|caught|built|sent|"
    "kept|spoken|chosen|driven|broken|drawn|grown|thrown|worn|torn|born|felt|left|"
    "read|said|told|understood|meant|brought|bought|taught|thought|heard|led"
)
PASSIVE_VOICE_RE = re.compile(
    rf"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ed\b|(?:{IRREGULAR_PARTICIPLES})\b)",
    re.IGNORECASE,
)

WORD_RE = re.compile(r"[A-Za-z']+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
EM_DASH = "—"

# Numeric features compared/reported generically — see similarity_score()/print_report().
# Each entry is (key, human label). Function-word rates are added to this set at runtime
# (see compute_fingerprint()) since the list is data-driven, not hardcoded twice.
BASE_NUMERIC_FEATURES = [
    ("mean_sentence_length", "Mean sentence length (words)"),
    ("stdev_sentence_length", "Sentence length std. dev."),
    ("mean_paragraph_length", "Mean paragraph length (words)"),
    ("vocabulary_richness", "Vocabulary richness (type-token ratio)"),
    ("em_dash_per_1k", "Em dashes / 1,000 words"),
    ("comma_per_1k", "Commas / 1,000 words"),
    ("semicolon_per_1k", "Semicolons / 1,000 words"),
    ("exclamation_per_1k", "Exclamation points / 1,000 words"),
    ("parenthetical_per_1k", "Parentheticals / 1,000 words"),
    ("contraction_per_1k", "Contractions / 1,000 words"),
    ("passive_voice_per_1k", "Approx. passive voice / 1,000 words (heuristic)"),
]


def tokenize_words(text):
    """Lowercase word tokens — letters and internal apostrophes only. A regex
    tokenizer, not a real one: markdown syntax (headers, list markers, code
    fences) is not stripped first, so heavily-structured markdown will skew
    results — this is meant to run against prose (a LinkedIn draft, a PR
    description, README prose), not a table or code block. Documented, not
    silently wrong."""
    return [w.lower() for w in WORD_RE.findall(text)]


def split_sentences(text):
    """Splits on sentence-ending punctuation followed by whitespace and a
    capital letter, digit, or quote — a heuristic, not a real sentence
    boundary detector (it will over-split on abbreviations like "Dr. Smith"
    and under-split on a run-on with no ending punctuation). Good enough for
    a relative, self-vs-reference comparison; not a claim of precise counts."""
    stripped = text.strip()
    if not stripped:
        return []
    return [s for s in SENTENCE_SPLIT_RE.split(stripped) if s.strip()]


def split_paragraphs(text):
    """Splits on one or more blank lines."""
    return [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def top_repeated_phrases(words, n=5, min_count=2):
    """The most frequent 3- and 4-word phrases that repeat at least
    `min_count` times — a real stylometric tell (a favorite transition,
    a verbal tic) that word-frequency features alone don't surface.
    No stopword filtering: exact-phrase repetition matters regardless of
    whether the words are function words or content words."""
    counts = Counter()
    for n_gram in (3, 4):
        for i in range(len(words) - n_gram + 1):
            counts[" ".join(words[i:i + n_gram])] += 1
    repeated = [(phrase, c) for phrase, c in counts.items() if c >= min_count]
    repeated.sort(key=lambda pair: (-pair[1], pair[0]))
    return repeated[:n]


def compute_fingerprint(text):
    """Returns a flat dict: word_count/sentence_count/paragraph_count (raw),
    every entry in BASE_NUMERIC_FEATURES plus one `fw_<word>_per_1k` per
    FUNCTION_WORDS entry (all comparable rates), and top_phrases (not a rate
    — reported separately, never folded into the numeric comparison). Never
    raises on empty/tiny input — every rate degrades to 0.0 rather than a
    ZeroDivisionError, since a short LinkedIn post is exactly the kind of
    input this tool needs to handle gracefully, not just a full README."""
    words = tokenize_words(text)
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    n_words = len(words)

    def per_1k(count):
        return round(count / n_words * 1000, 2) if n_words else 0.0

    sentence_lengths = [len(tokenize_words(s)) for s in sentences]
    mean_sentence_length = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0.0
    if len(sentence_lengths) > 1:
        variance = sum((x - mean_sentence_length) ** 2 for x in sentence_lengths) / (len(sentence_lengths) - 1)
        stdev_sentence_length = math.sqrt(variance)
    else:
        stdev_sentence_length = 0.0

    paragraph_lengths = [len(tokenize_words(p)) for p in paragraphs]
    mean_paragraph_length = sum(paragraph_lengths) / len(paragraph_lengths) if paragraph_lengths else 0.0

    vocabulary_richness = round(len(set(words)) / n_words, 4) if n_words else 0.0

    word_counts = Counter(words)
    fp = {
        "word_count": n_words,
        "sentence_count": len(sentences),
        "paragraph_count": len(paragraphs),
        "mean_sentence_length": round(mean_sentence_length, 2),
        "stdev_sentence_length": round(stdev_sentence_length, 2),
        "mean_paragraph_length": round(mean_paragraph_length, 2),
        "vocabulary_richness": vocabulary_richness,
        "em_dash_per_1k": per_1k(text.count(EM_DASH)),
        "comma_per_1k": per_1k(text.count(",")),
        "semicolon_per_1k": per_1k(text.count(";")),
        "exclamation_per_1k": per_1k(text.count("!")),
        "parenthetical_per_1k": per_1k(text.count("(")),
        "contraction_per_1k": per_1k(len(CONTRACTION_RE.findall(text))),
        "passive_voice_per_1k": per_1k(len(PASSIVE_VOICE_RE.findall(text))),
    }
    for fw in FUNCTION_WORDS:
        fp[f"fw_{fw}_per_1k"] = per_1k(word_counts.get(fw, 0))
    fp["top_phrases"] = top_repeated_phrases(words)
    return fp


def _numeric_feature_keys(fingerprint):
    return [k for k in fingerprint if k not in ("word_count", "sentence_count", "paragraph_count", "top_phrases")]


def similarity_score(draft_fp, reference_fp):
    """0-100: how closely draft_fp's numeric features match reference_fp's,
    averaged equally across every rate/mean feature (including all function-
    word rates) — NOT weighted by how "important" a feature seems, since
    that judgment call is exactly what real stylometry calibration research
    hasn't settled (see leak-taxonomy.md's discussion of this same open
    question for privacy-linter's own deferred stylometric class). Per-
    feature: 100 * (1 - |a-b| / max(a, b, 1.0)) — the max(..., 1.0) floor
    keeps two near-zero rates (e.g. both drafts have ~0 exclamation points)
    from being treated as maximally different due to noise in tiny counts."""
    keys = _numeric_feature_keys(draft_fp)
    if not keys:
        return 0.0
    scores = []
    for k in keys:
        a, b = draft_fp[k], reference_fp.get(k, 0.0)
        denom = max(abs(a), abs(b), 1.0)
        scores.append(1 - min(1.0, abs(a - b) / denom))
    return round(100 * sum(scores) / len(scores), 1)


def feature_deltas(draft_fp, reference_fp):
    """Per-feature (key, draft_value, reference_value, pct_match) sorted by
    pct_match descending — the "closely matches your known style" list is
    just this list's head, "most different" is its tail. pct_match uses the
    same formula as similarity_score() so the two are consistent."""
    keys = _numeric_feature_keys(draft_fp)
    rows = []
    for k in keys:
        a, b = draft_fp[k], reference_fp.get(k, 0.0)
        denom = max(abs(a), abs(b), 1.0)
        pct_match = round(100 * (1 - min(1.0, abs(a - b) / denom)), 1)
        rows.append((k, a, b, pct_match))
    rows.sort(key=lambda row: -row[3])
    return rows


# Severity tiers for --emit-findings. Below EMIT_FINDINGS_DEFAULT_THRESHOLD, no finding
# is emitted at all — a weak similarity score isn't a stylometric matching cue, the same
# way privacy-linter's scan_diff.py reports nothing for clean content instead of a
# synthetic all-clear entry. Above it, severity scales with how strong the match is.
# These are a starting point, not calibrated science — see privacy-threat-oracle's own
# decision-rubric.md on the same open question — which is exactly why the threshold is a
# CLI flag and not hardcoded as settled.
EMIT_FINDINGS_DEFAULT_THRESHOLD = 60.0
EMIT_FINDINGS_MEDIUM = 70.0
EMIT_FINDINGS_HIGH = 85.0


def severity_for_score(score, threshold=EMIT_FINDINGS_DEFAULT_THRESHOLD):
    """Classifies a similarity score into "none"/"low"/"medium"/"high" —
    shared by emit_findings() (which drops "none" entirely, since a weak
    match isn't a finding) and write_run_log() (which keeps "none", since a
    trend log needs every run, not just the flagged ones, to show whether
    similarity is rising or falling over time)."""
    if score < threshold:
        return "none"
    if score >= EMIT_FINDINGS_HIGH:
        return "high"
    if score >= EMIT_FINDINGS_MEDIUM:
        return "medium"
    return "low"


def emit_findings(label, fp, reference_fp, threshold=EMIT_FINDINGS_DEFAULT_THRESHOLD):
    """Returns a list of 0 or 1 dicts shaped exactly like privacy-linter's
    scan_diff.py Finding (severity, leak_class, finding, reason, location) —
    the wire format privacy-threat-oracle's --from-linter-json already reads
    (it only requires a string "leak_class" field, but matching the full
    shape keeps this a real Finding, not a lookalike). Below `threshold`,
    returns [] rather than a low-severity finding."""
    score = similarity_score(fp, reference_fp)
    severity = severity_for_score(score, threshold)
    if severity == "none":
        return []

    deltas = [row for row in feature_deltas(fp, reference_fp) if row[1] != 0 or row[2] != 0]
    top_features = ", ".join(_feature_label(key) for key, *_rest in deltas[:3]) or "no single feature dominates"

    return [{
        "severity": severity,
        "leak_class": "stylometric",
        "finding": f"Stylometric similarity to reference corpus: {score}%",
        "reason": f"Draft's writing style matches the reference on: {top_features}. "
                  "A match this close is what an authorship matcher would key on to link "
                  "this draft back to the reference corpus.",
        "location": label,
    }]


# --- Run logging (for longitudinal trend tracking) ------------------------------------
# Mirrors privacy-linter's own --log-dir discipline exactly: one durable timestamped
# record per run, score/severity/labels only — never the fingerprint's numeric feature
# vector (which is itself a real stylometric signal, the whole point of this tool) and
# never top_phrases (verbatim substrings of the actual draft). A trend log that leaked
# either would undermine the reason this tool exists in the first place.

def write_run_log(log_dir, label, reference_label, score, severity):
    """Appends one timestamped JSON record under log_dir. Silently creates
    log_dir if it doesn't exist yet. Never raises on a write failure — a
    full disk or a permissions problem here must never block or crash an
    actual fingerprint run, same discipline as privacy-linter's own
    write_run_log()."""
    try:
        os.makedirs(log_dir, exist_ok=True)
        now = datetime.now(timezone.utc)
        filename = now.strftime("%Y%m%dT%H%M%S%f") + f"-{uuid.uuid4().hex[:8]}.json"
        record = {
            "timestamp": now.isoformat(),
            "label": label,
            "reference_label": reference_label,
            "similarity_score": score,
            "severity": severity,
        }
        with open(os.path.join(log_dir, filename), "w", encoding="utf-8") as f:
            json.dump(record, f)
    except OSError as e:
        print(f"Warning: could not write run log to {log_dir}: {e}", file=sys.stderr)


def _feature_label(key):
    for k, label in BASE_NUMERIC_FEATURES:
        if k == key:
            return label
    if key.startswith("fw_") and key.endswith("_per_1k"):
        return f'"{key[3:-7]}" / 1,000 words'
    return key


def walk_text_files(root):
    """Vault-relative .md/.txt paths under root, skipping dotfiles/dirs —
    same convention wiki-librarian's check_vault.py and wiki-privacy-audit's
    check_vault_privacy.py already use, reused here rather than inventing a
    third file-discovery routine for the same kind of directory."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith(".") or not (name.endswith(".md") or name.endswith(".txt")):
                continue
            out.append(os.path.join(dirpath, name))
    return sorted(out)


def load_reference_text(path):
    """path may be a single file or a directory — a directory's .md/.txt
    files are concatenated (double-newline separated) into one reference
    corpus, since the whole point of a reference is "everything I know is
    really you," not a single sample. Unreadable individual files are
    skipped with a stderr warning, never a crash — same "a bad file doesn't
    take down the batch" discipline this repo's other directory-walking
    tools already apply."""
    if os.path.isdir(path):
        chunks = []
        for file_path in walk_text_files(path):
            try:
                with open(file_path, encoding="utf-8", errors="replace") as f:
                    chunks.append(f.read())
            except OSError as e:
                print(f"Warning: skipping unreadable reference file {file_path}: {e}", file=sys.stderr)
        return "\n\n".join(chunks)
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _redacted_phrases(top_phrases):
    """top_phrases holds verbatim 3-4 word substrings of the actual draft —
    a real content-leak vector if a fingerprint report is ever shared,
    logged, or piped somewhere (undermining the exact thing this tool is
    for). Redacted by default in both the text and --json report: shown as
    a count + n-gram-length summary, never the phrase text itself, unless
    --show-phrase-text opts in for the user's own interactive terminal."""
    if not top_phrases:
        return {"count": 0, "lengths": [], "redacted": True}
    lengths = sorted({len(phrase.split()) for phrase, _count in top_phrases})
    return {"count": len(top_phrases), "lengths": lengths, "redacted": True}


def _fingerprint_for_report(fp, show_phrase_text):
    if show_phrase_text:
        return fp
    return {k: (v if k != "top_phrases" else _redacted_phrases(v)) for k, v in fp.items()}


def print_report(label, fp, reference_label=None, reference_fp=None, json_out=False, show_phrase_text=False):
    if json_out:
        payload = {"label": label, "fingerprint": _fingerprint_for_report(fp, show_phrase_text)}
        if reference_fp is not None:
            payload["reference_label"] = reference_label
            payload["reference_fingerprint"] = _fingerprint_for_report(reference_fp, show_phrase_text)
            payload["similarity_score"] = similarity_score(fp, reference_fp)
        print(json.dumps(payload, indent=2))
        return

    print(f"=== Stylometric Fingerprint: {label} ===")
    print(f"Words: {fp['word_count']} | Sentences: {fp['sentence_count']} | Paragraphs: {fp['paragraph_count']}")
    if fp["word_count"] < 100:
        print("(warning: under 100 words — rates below are noisy at this length)")
    print(f"Mean sentence length: {fp['mean_sentence_length']} words (σ={fp['stdev_sentence_length']})")
    print(f"Vocabulary richness (type-token ratio): {fp['vocabulary_richness']}")
    print(f"Em dash rate: {fp['em_dash_per_1k']} / 1,000 words")
    print(f"Comma rate: {fp['comma_per_1k']} / 1,000 words")
    print(f"Semicolon rate: {fp['semicolon_per_1k']} / 1,000 words")
    print(f"Exclamation rate: {fp['exclamation_per_1k']} / 1,000 words")
    print(f"Contraction rate: {fp['contraction_per_1k']} / 1,000 words")
    print(f"Approx. passive voice rate: {fp['passive_voice_per_1k']} / 1,000 words (heuristic, not POS-based)")
    top_fw = sorted(FUNCTION_WORDS, key=lambda w: -fp[f"fw_{w}_per_1k"])[:8]
    print("Most frequent function words: " + ", ".join(f"{w} ({fp[f'fw_{w}_per_1k']})" for w in top_fw))
    if show_phrase_text:
        if fp["top_phrases"]:
            print("Repeated phrases: " + "; ".join(f'"{p}" ({c}x)' for p, c in fp["top_phrases"]))
        else:
            print("Repeated phrases: none found")
    else:
        summary = _redacted_phrases(fp["top_phrases"])
        if summary["count"]:
            lengths = "/".join(str(n) for n in summary["lengths"])
            print(f"Repeated phrases: {summary['count']} found ({lengths}-word, text redacted — "
                  "pass --show-phrase-text to reveal)")
        else:
            print("Repeated phrases: none found")

    if reference_fp is not None:
        score = similarity_score(fp, reference_fp)
        print(f"\n--- Compared to reference ({reference_label}, {reference_fp['word_count']} words) ---")
        print(f"Overall similarity: {score}%")
        # Both sides at 0 (e.g. neither text ever uses a semicolon) is a real fact but
        # not an actionable one — filtered out of the two headline lists below so they
        # surface features that actually occur, not absences. similarity_score() above
        # deliberately does NOT apply this filter: shared absence is still real signal
        # for the honest overall number, just not worth calling out individually.
        deltas = [row for row in feature_deltas(fp, reference_fp) if row[1] != 0 or row[2] != 0]
        print("Closely matches your reference on:")
        for key, a, b, pct in deltas[:5]:
            print(f"  {_feature_label(key)}: draft {a} vs reference {b} ({pct}% match)")
        print("Most different from your reference on:")
        for key, a, b, pct in deltas[-5:]:
            print(f"  {_feature_label(key)}: draft {a} vs reference {b} ({pct}% match)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", help="Path to the draft to fingerprint.")
    parser.add_argument("--text", help="Read the draft from stdin (pass '-').")
    parser.add_argument("--reference", help="A file or directory of your own known writing to compare against.")
    parser.add_argument("--json", action="store_true", help="Emit the fingerprint (and comparison, if any) as JSON.")
    parser.add_argument("--emit-findings", action="store_true",
                         help="Emit a privacy-linter-shaped Finding list (JSON) instead of a report — "
                              "pipe straight into privacy-threat-oracle's --from-linter-json. "
                              "Requires --reference; emits [] if similarity is below the threshold.")
    parser.add_argument("--emit-findings-threshold", type=float, default=EMIT_FINDINGS_DEFAULT_THRESHOLD,
                         help=f"Minimum similarity score (0-100) to emit a finding at all "
                              f"(default: {EMIT_FINDINGS_DEFAULT_THRESHOLD}).")
    parser.add_argument("--show-phrase-text", action="store_true",
                         help="Show the actual text of repeated phrases in the report/--json output. "
                              "Off by default — top_phrases is verbatim draft text, redacted unless "
                              "you explicitly opt in for your own terminal.")
    parser.add_argument("--log-dir", metavar="DIR",
                         help="Append this run's score/severity as a timestamped JSON record under DIR, "
                              "for trend-tracking over time. Never writes the fingerprint's numeric "
                              "feature vector or top_phrases — score and severity only. Requires --reference.")
    args = parser.parse_args()

    if args.emit_findings and not args.reference:
        print("style-obfuscator: --emit-findings requires --reference "
              "(nothing to compare the draft against).", file=sys.stderr)
        return 2

    if args.log_dir and not args.reference:
        print("style-obfuscator: --log-dir requires --reference "
              "(nothing to compare the draft against, so no score to log).", file=sys.stderr)
        return 2

    if args.text == "-":
        text = sys.stdin.read()
        label = "<stdin>"
    elif args.file:
        with open(args.file, encoding="utf-8", errors="replace") as f:
            text = f.read()
        label = args.file
    else:
        print("style-obfuscator: pass --file PATH or --text -", file=sys.stderr)
        return 2

    fp = compute_fingerprint(text)
    if fp["word_count"] == 0:
        print("style-obfuscator: no words found in the input.", file=sys.stderr)
        return 2

    reference_fp = None
    reference_label = None
    if args.reference:
        reference_text = load_reference_text(args.reference)
        reference_fp = compute_fingerprint(reference_text)
        reference_label = args.reference
        if reference_fp["word_count"] == 0:
            print(f"style-obfuscator: no words found under --reference {args.reference}.", file=sys.stderr)
            return 2

    if args.log_dir:
        score = similarity_score(fp, reference_fp)
        severity = severity_for_score(score, threshold=args.emit_findings_threshold)
        write_run_log(args.log_dir, label, reference_label, score, severity)

    if args.emit_findings:
        findings = emit_findings(label, fp, reference_fp, threshold=args.emit_findings_threshold)
        print(json.dumps(findings, indent=2))
        return 0

    print_report(label, fp, reference_label=reference_label, reference_fp=reference_fp,
                 json_out=args.json, show_phrase_text=args.show_phrase_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
