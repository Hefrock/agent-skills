#!/usr/bin/env python3
"""Per-turn grounding contract for the weekly two-host show.

The weekly show reverses a deliberate design choice (narrate.py: no
cross-story synthesis, per-story isolation). This module is the mitigation:
a script is a list of turns, and each turn is checked on its own against
exactly one story's archive entry. Cross-story talk is only possible through
fixed, hand-written link turns, so a model can never carry a fact from one
story into another.

Turn shape:
  {"speaker": "A"|"B", "kind": "claim"|"reactive"|"link", "text": str,
   "claim_id": str|None, "supporting_spans": [str]}

  claim     factual. Must cite exactly one claim_id present in the week pack,
            at least one supporting span that is a verbatim substring of THAT
            entry's headline+summary, and keep any hedge the source carries.
  reactive  text must be a member of narrate.REACTIVE_TEMPLATES (hand-written,
            never generated). No claim_id.
  link      text must be a member of LINK_TURNS below. No claim_id.

The span and hedge checks reuse narrate.py's helpers so the weekly show and
the daily narration share one definition of "grounded". What this does NOT
check: that every number or named entity in a claim turn appears in the
source, or that a paraphrase is faithful beyond the quoted span. It bounds
where a claim can come from; it does not prove the wording is correct.

Pure functions, stdlib only."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import narrate  # noqa: E402

SPEAKERS = {narrate.DIALOGUE_SPEAKER_A, narrate.DIALOGUE_SPEAKER_B}

# Hand-written and fact-free on purpose. Edit this list, never generate it.
LINK_TURNS = [
    "Let's move on to the next story.",
    "Here's another one from this week.",
    "Next up.",
    "One more to cover.",
]

_REACTIVE_POOL = {text for pool in narrate.REACTIVE_TEMPLATES.values() for text in pool}


def _source_text(entry: dict) -> str:
    return f"{entry['headline']}. {entry['summary']}"


def check_turn(turn: dict, pack: dict) -> dict:
    """pack maps claim_id -> archive entry (headline, summary, ...).
    Returns {"passed": bool, "reasons": [...]}; every failed check is
    reported, not just the first."""
    reasons = []
    kind = turn.get("kind")
    text = turn.get("text") or ""

    if turn.get("speaker") not in SPEAKERS:
        reasons.append(f"unknown speaker: {turn.get('speaker')!r}")
    if not text.strip():
        reasons.append("turn text is empty")

    if kind == "claim":
        claim_id = turn.get("claim_id")
        entry = pack.get(claim_id) if claim_id else None
        if not claim_id:
            reasons.append("claim turn has no claim_id")
        elif entry is None:
            reasons.append(f"claim_id not in the week pack: {claim_id}")
        spans = turn.get("supporting_spans") or []
        if not spans:
            reasons.append("claim turn has no supporting_spans")
        if entry is not None:
            source = _source_text(entry)
            ungrounded = [s for s in spans if not narrate._span_is_grounded(s, source)]
            if ungrounded:
                reasons.append(f"supporting span(s) not found verbatim in the cited story: {ungrounded}")
            if narrate._has_hedge_marker(source) and not narrate._has_hedge_marker(text):
                reasons.append("cited story contains hedging/attribution language the turn dropped")
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
    has_claim_turn = any(t.get("kind") == "claim" for t in turns)
    passed = bool(turns) and has_claim_turn and all(r["passed"] for r in results)
    return {"passed": passed, "results": results}
