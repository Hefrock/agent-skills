#!/usr/bin/env python3
"""Per-turn grounding contract for the weekly two-host show.

The weekly show reverses a deliberate design choice (narrate.py: no
cross-story synthesis, per-story isolation). This module is the mitigation:
a script is a list of turns, and each turn is checked on its own against
exactly one story's archive entry.

Turn shape:
  {"speaker": "A"|"B", "kind": "claim"|"reactive"|"link", "text": str,
   "claim_id": str|None, "supporting_spans": [str]}

  claim     factual. Must cite exactly one claim_id present in the week pack
            and at least one supporting span (3+ words) that is a verbatim
            substring of THAT entry's headline+summary. The turn TEXT is then
            checked against that same entry: every number must appear in it,
            every capitalized name (anything that is not just a sentence's
            first word) must appear in it, hedges must be kept, and
            negation must neither be added nor dropped.
  reactive  text must be a member of narrate.REACTIVE_TEMPLATES (hand-written,
            never generated). No claim_id.
  link      text must be a member of LINK_TURNS below. No claim_id.

This bounds where a claim's facts can come from; it does not prove the
wording is faithful. Known gaps, by design of a mechanical check: a changed
direction or comparison that uses the story's own words ("rose" for "fell"),
spelled-out numbers ("forty"), lowercase entities, and a paraphrase that
merges two true statements into a false one all pass. A human or a
model-judged second pass is still needed for those; do not describe a
passing script as verified.

Malformed turns (wrong types, missing fields) fail with a reason; they never
raise. Pure functions, stdlib only."""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import narrate  # noqa: E402

SPEAKERS = {narrate.DIALOGUE_SPEAKER_A, narrate.DIALOGUE_SPEAKER_B}
MIN_SPAN_WORDS = 3

# Hand-written and fact-free on purpose. Edit this list, never generate it.
LINK_TURNS = [
    "Let's move on to the next story.",
    "Here's another one from this week.",
    "Next up.",
    "One more to cover.",
]

_REACTIVE_POOL = {text for pool in narrate.REACTIVE_TEMPLATES.values() for text in pool}

# Capitalized only because they start a sentence; allowed without appearing in
# the source. Extend deliberately: a failure here is loud, not silent.
_FUNCTION_WORDS = {
    "the", "a", "an", "this", "that", "these", "those", "it", "its", "they", "their", "there", "then", "here", "now",
    "in", "on", "at", "for", "and", "but", "so", "as", "if", "when", "while", "also", "however", "meanwhile",
    "one", "another", "some", "many", "most", "we", "he", "she", "i", "our", "what", "why", "how", "yes", "well",
    "today", "next", "more", "both", "each", "all", "no", "not", "with", "from", "by", "of", "to",
}

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
_NEGATION_RE = re.compile(r"\b(?:not|no|never|without|neither|nor|cannot)\b|n['’]t\b", re.IGNORECASE)


def _source_text(entry: dict) -> str:
    return f"{entry['headline']}. {entry['summary']}"


def _numbers(text: str) -> set[str]:
    return {m.replace(",", "").rstrip(".") for m in _NUMBER_RE.findall(text)}


def _capitalized_names(text: str) -> list[tuple[str, bool]]:
    """(token, starts_a_sentence) for every capitalized word."""
    names = []
    for m in _WORD_RE.finditer(text):
        word = m.group(0)
        if not word[0].isupper():
            continue
        before = text[: m.start()].rstrip()
        names.append((word, before == "" or before[-1] in ".!?"))
    return names


def _check_claim(turn: dict, pack: dict, text: str) -> list[str]:
    reasons = []
    claim_id = turn.get("claim_id")
    spans = turn.get("supporting_spans")

    entry = None
    if not isinstance(claim_id, str) or not claim_id:
        reasons.append("claim turn needs a string claim_id")
    else:
        entry = pack.get(claim_id)
        if entry is None:
            reasons.append(f"claim_id not in the week pack: {claim_id}")

    spans_ok = isinstance(spans, list) and bool(spans) and all(isinstance(s, str) for s in spans)
    if not spans_ok:
        reasons.append("supporting_spans must be a non-empty list of strings")
    else:
        short = [s for s in spans if len(s.split()) < MIN_SPAN_WORDS]
        if short:
            reasons.append(f"supporting span(s) shorter than {MIN_SPAN_WORDS} words: {short}")

    if entry is None:
        return reasons

    source = _source_text(entry)
    if spans_ok:
        ungrounded = [s for s in spans if s.strip() and not narrate._span_is_grounded(s, source)]
        if ungrounded:
            reasons.append(f"supporting span(s) not found verbatim in the cited story: {ungrounded}")

    if narrate._has_hedge_marker(source) and not narrate._has_hedge_marker(text):
        reasons.append("cited story contains hedging/attribution language the turn dropped")

    source_numbers = _numbers(source)
    missing_numbers = sorted(n for n in _numbers(text) if n not in source_numbers)
    if missing_numbers:
        reasons.append(f"number(s) not in the cited story: {missing_numbers}")

    source_words = {w.lower() for w in _WORD_RE.findall(source)}
    missing_names = []
    for word, starts_sentence in _capitalized_names(text):
        lowered = word.lower()
        if lowered in source_words:
            continue
        if starts_sentence and lowered in _FUNCTION_WORDS:
            continue
        missing_names.append(word)
    if missing_names:
        reasons.append(f"name(s)/entities not in the cited story: {sorted(set(missing_names))}")

    text_negated = _NEGATION_RE.search(text) is not None
    if text_negated and _NEGATION_RE.search(source) is None:
        reasons.append("turn contains negation the cited story does not")
    if spans_ok and any(_NEGATION_RE.search(s) for s in spans) and not text_negated:
        reasons.append("a quoted span is negated but the turn dropped the negation")

    return reasons


def check_turn(turn: dict, pack: dict) -> dict:
    """pack maps claim_id -> archive entry (headline, summary, ...).
    Returns {"passed": bool, "reasons": [...]}; every failed check is
    reported, not just the first. Never raises on malformed input."""
    if not isinstance(turn, dict):
        return {"passed": False, "reasons": ["turn is not an object"]}

    reasons = []
    kind = turn.get("kind")
    text = turn.get("text")
    if not isinstance(text, str) or not text.strip():
        reasons.append("turn text must be a non-empty string")
        text = ""

    speaker = turn.get("speaker")
    if not isinstance(speaker, str) or speaker not in SPEAKERS:
        reasons.append(f"unknown speaker: {speaker!r}")

    if kind == "claim":
        reasons += _check_claim(turn, pack, text)
    elif kind == "reactive":
        if text not in _REACTIVE_POOL:
            reasons.append("reactive turn is not from narrate.REACTIVE_TEMPLATES")
    elif kind == "link":
        if text not in LINK_TURNS:
            reasons.append("link turn is not from weekly_grounding.LINK_TURNS")
    else:
        reasons.append(f"unknown turn kind: {kind!r}")

    if kind in ("reactive", "link") and turn.get("claim_id"):
        reasons.append(f"{kind} turn must not carry a claim_id")

    return {"passed": not reasons, "reasons": reasons}


def check_turns(turns: list[dict], pack: dict) -> dict:
    """Checks every turn. A script with no turns, or no claim turns at all,
    does not pass — an episode with nothing factual in it is a bug, not a
    clean result. results[i]["index"] is the turn's position in turns."""
    results = []
    for i, turn in enumerate(turns):
        result = check_turn(turn, pack)
        results.append({"index": i, **result})
    has_claim_turn = any(isinstance(t, dict) and t.get("kind") == "claim" for t in turns)
    passed = bool(turns) and has_claim_turn and all(r["passed"] for r in results)
    return {"passed": passed, "results": results}
