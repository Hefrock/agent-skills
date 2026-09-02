#!/usr/bin/env python3
"""Narration (Phase 5.5) — rewrites each selected story's mechanical,
templated sentence (script_gen.py's title + summary + source text) into
more natural spoken narration via a Gemini text-generation call,
constrained to that one story's own text only.

Design decided in conversation, not assumed — this module implements a
specific, deliberated tradeoff, not a first idea:

  - Per-story isolation, on purpose. Each generate_narration() call sees
    exactly one story's own already-vetted text — never another story,
    never broader context. This is the single highest-leverage lever the
    research behind this design turned up: a September 2025 study ("Not
    Wrong, But Untrue: LLM Overconfidence in Document-Based Queries")
    found NotebookLM's closed-domain, single-corpus grounding cut
    hallucination to 13% versus 40% for the same documents given to
    ChatGPT/Gemini in a broader context — and this module's context is
    tighter still (one story, not a whole corpus). The real cost of this
    choice: it rules out genuine cross-story synthesis ("this follows
    CMS's move last month") — a deliberate, discussed scope decision,
    not an oversight. That's a larger, separate, not-yet-decided
    follow-on if it happens at all.

  - No mitigation here claims to be a hallucination-proof guarantee.
    13% is NotebookLM's number under the best-known version of this
    exact technique; nothing in the literature claims zero. The real,
    structural guarantee this pipeline gives a listener remains what
    qa_gate.py already enforces — every story segment's claim_id/
    source_id traces to evidence-pinning-mcp's durable, queryable
    provenance log, regardless of how the segment's words were produced.
    Narration only ever changes HOW something is said; it does not
    change whether it's grounded in that structural sense. What this
    module adds is a second, weaker, best-effort layer aimed at whether
    the WORDS are trustworthy, which qa_gate's metadata-level checks
    can't see into.

  - check_narration_grounded() targets the failure mode the same study
    identified as dominant — not invented entities, but "interpretive
    overconfidence": a hedged or attributed claim ("may cause", "some
    experts believe") flattened into a flat assertion. A naive
    length-ratio or capitalized-word heuristic doesn't catch that (an
    earlier draft of this design used exactly that pair, and a self-
    critique caught it as a real gap before implementation started).
    Two real checks instead: (1) every claimed supporting_span must be a
    real, verbatim substring of the source text — "simultaneous citation
    generation" from the RAG literature, mechanically verified rather
    than trusted, which does target fabricated entities/numbers; (2) if
    the source contains hedging/attribution language, the narration must
    too — which targets the dominant failure mode directly. A length-
    ratio bound is kept as a third, cheap, tertiary sanity check, not
    the primary defense it started as.

  - The disclosure a listener hears (script_gen.py's DEFAULT_DISCLOSURE_
    TEXT) is a fixed, human-approved sentence, never generated — and
    stays that way regardless of whether narration is active for a given
    episode. Narration is an enhancement to story segments only; it
    never touches the disclosure segment.

Same split as every prior stage: check_narration_grounded() and
narrate_script()'s orchestration logic are pure, no network, fully
unit-testable; generate_narration() is the one function that touches the
network, using gemini_retry.py's Retry-After-aware backoff — the same
policy audio_synth.py needed after a real rate-limit was hit live,
promoted to shared infrastructure specifically so this module wouldn't
duplicate it.

stdlib only, matching this repo's other reference tooling."""

import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gemini_retry  # noqa: E402

DEFAULT_TEXT_MODEL = "gemini-2.5-flash"
DEFAULT_SUCCESS_THRESHOLD = 0.7
DEFAULT_LENGTH_RATIO_MIN = 0.4
DEFAULT_LENGTH_RATIO_MAX = 2.0

# Substring-matched, case-insensitive, against source and narration text —
# the same "short, hand-curated list, simple matching is the right level
# of sophistication for now" philosophy source_registry.classify_topic_
# scope() already uses, not NLP. Deliberately broad enough to over-trigger
# occasionally (an unnecessary fallback costs a slightly-less-natural
# sentence) rather than under-trigger (a missed flattened hedge costs a
# false claim of certainty) — the asymmetry is intentional given this is
# healthcare content.
HEDGE_MARKERS = [
    "may", "might", "could", "reportedly", "according to", "is expected",
    "are expected", "suggests", "suggest", "believes", "believe",
    "preliminary", "some experts", "potentially", "likely", "unclear",
    "unconfirmed", "estimated",
]


def _normalize_for_span_match(text: str) -> str:
    return " ".join(text.lower().split())


def _span_is_grounded(span: str, source_text: str) -> bool:
    return _normalize_for_span_match(span) in _normalize_for_span_match(source_text)


def _has_hedge_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in HEDGE_MARKERS)


def check_narration_grounded(
    narration: str,
    supporting_spans: list[str],
    source_text: str,
    length_ratio_min: float = DEFAULT_LENGTH_RATIO_MIN,
    length_ratio_max: float = DEFAULT_LENGTH_RATIO_MAX,
) -> dict:
    """Pure, deterministic grounding check over one candidate narration —
    see this module's docstring for why these specific checks and not a
    length/capitalization proxy.

    Returns {"passed": bool, "reasons": [...]} — reasons lists every
    check that failed, not just the first, same "report everything at
    once" discipline qa_gate.run_checks() already uses."""
    reasons = []

    if not narration.strip():
        reasons.append("narration is empty")

    if not supporting_spans:
        reasons.append("no supporting_spans provided")
    else:
        ungrounded = [s for s in supporting_spans if not _span_is_grounded(s, source_text)]
        if ungrounded:
            reasons.append(f"supporting span(s) not found verbatim in source: {ungrounded}")

    if _has_hedge_marker(source_text) and not _has_hedge_marker(narration):
        reasons.append("source contains hedging/attribution language the narration dropped")

    if source_text.strip() and narration.strip():
        ratio = len(narration) / len(source_text)
        if not (length_ratio_min <= ratio <= length_ratio_max):
            reasons.append(f"narration length ratio {ratio:.2f} outside [{length_ratio_min}, {length_ratio_max}]")

    return {"passed": not reasons, "reasons": reasons}
