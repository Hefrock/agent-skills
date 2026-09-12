#!/usr/bin/env python3
"""Script generation (Phase 5) — turns rank.py's selected stories, once
grounded by evidence.py's pinned claims, into the spoken-word script for
the daily healthcare AI briefing: an ordered list of segments ready for
Phase 7 (audio synthesis).

Same "distill, don't invent" discipline as every prior stage
(ingest.py's factual summaries built from structured fields,
evidence.py's mechanical claim-pinning): narration text here is built by
template from each item's own title/summary/source name, not drafted by
an LLM. No new dependency, no network call, no I/O — pure data
transformation, same as rank.py.

Grounding is enforced, not assumed: evidence.py's own docstring commits
to this stage's contract — "a script draft should never cite a story
that was never actually pinned to its source." generate_script() joins
rank.py's top_three/quick_hits against evidence.py's pinned result by
canonical_id (a value join, not object identity — so callers can hand in
independently-constructed pinned results in tests). Any selected story
with no pinned claim (whether it was skipped for having no summary, or
genuinely failed at the evidence-pinning server) is excluded from the
script and reported in excluded_no_evidence instead — never silently
dropped, exactly the same "don't drop things silently, report explicit
failure buckets" pattern rank.py/evidence.py already use
(dropped_duplicates, not_selected, skipped_no_summary, failed).

Segment shape: {"segment_type", "text", "canonical_id", "claim_id",
"source_id"}. Non-story segments (intro/disclosure/transition/outro)
carry text only — canonical_id/claim_id/source_id are None for those, so
a downstream stage can always find "which pinned claim backs this line
of narration" (or confirm there isn't one, for the connective segments)
by checking claim_id.

Every episode carries a fixed "disclosure" segment right after the
intro, unconditionally — not something a caller opts into per run. Major
podcast platforms (Apple Podcasts, as of 2026) require prominent AI-use
disclosure, both in the audio itself and in episode/show metadata,
whenever AI delivers a material portion of a show's content — and this
pipeline's audio is 100% synthesized speech (audio_synth.py), which
already clears that bar regardless of whether any given segment's words
came from a template or, later, an LLM rewrite. DEFAULT_DISCLOSURE_TEXT
is deliberately a fixed, human-approved constant, never LLM-generated —
the one sentence that has to be reliably accurate every single time
shouldn't itself be something a model could get wrong. distribute.py
reads this segment back out of the script to carry the same disclosure
into episode/feed metadata too (see its own docstring), so the text
lives in exactly one place.

stdlib only, matching this repo's other reference tooling."""

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import source_registry  # noqa: E402

DEFAULT_DISCLOSURE_TEXT = (
    "This is an automated healthcare AI news briefing. Stories are selected, "
    "linked to their original sources, and narrated by an AI pipeline, and "
    "this audio is synthesized speech, not a human voice."
)


def _format_date_for_speech(iso_date: str) -> str:
    # "%-d" (no leading zero) is a glibc/BSD strftime extension, not a
    # portable one — formatting with "%d" and stripping a leading zero by
    # hand avoids depending on that instead.
    d = date.fromisoformat(iso_date)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _source_name(registry: dict, source_key: str) -> str:
    return source_registry.get_source(registry, source_key)["name"]


def _render_story_text(item: dict, registry: dict) -> str:
    return f"{item['title']}. {item['summary']} Source: {_source_name(registry, item['source_key'])}."


def _build_claim_index(pinned: dict) -> dict:
    """canonical_id -> {"claim_id", "source_id"}, from evidence.py's
    pin_evidence_for_stories() result. Only "pinned" entries contribute —
    skipped_no_summary/failed items have no claim to index, so they fall
    through generate_script()'s lookup below the same as if they were
    never in the pinned result at all."""
    index = {}
    for entry in pinned["pinned"]:
        index[entry["item"]["canonical_id"]] = {"claim_id": entry["claim_id"], "source_id": entry["source_id"]}
    return index


def _story_segment(segment_type: str, item: dict, registry: dict, claim: dict) -> dict:
    return {
        "segment_type": segment_type,
        "text": _render_story_text(item, registry),
        "canonical_id": item["canonical_id"],
        "claim_id": claim["claim_id"],
        "source_id": claim["source_id"],
    }


def _connective_segment(segment_type: str, text: str) -> dict:
    return {"segment_type": segment_type, "text": text, "canonical_id": None, "claim_id": None, "source_id": None}


def generate_script(
    rank_result: dict,
    pinned: dict,
    registry: dict,
    run_date: str,
    show_name: str = "Healthcare AI Briefing",
    disclosure_text: str = DEFAULT_DISCLOSURE_TEXT,
) -> dict:
    """rank_result is rank_stories()'s return value (only top_three and
    quick_hits are read here — not_selected/dropped_duplicates/store are
    not this stage's concern). pinned is
    evidence.pin_evidence_for_stories()'s return value for the SAME
    combined set of items (top_three + quick_hits) — callers are expected
    to have already called pin_evidence_for_stories(client, top_three +
    quick_hits, run_id) before this. registry is a loaded
    config/sources.json (via source_registry.load_registry), used only to
    resolve each item's source_key to its human-readable name.

    Returns:
      {
        "run_date": run_date,
        "show_name": show_name,
        "segments": [{"segment_type", "text", "canonical_id", "claim_id", "source_id"}, ...],
        "excluded_no_evidence": [item, ...],  # selected but never pinned — not read aloud
      }

    segment_type is one of "intro", "disclosure", "top_three_item",
    "quick_hits_transition", "quick_hits_item", "outro". "disclosure"
    always appears exactly once, right after "intro" — see this module's
    docstring for why it's unconditional. "quick_hits_transition" is
    only emitted when at least one quick-hit item survives grounding —
    an episode shouldn't tease a segment that turns out to be empty."""
    claim_index = _build_claim_index(pinned)

    included_top_three = []
    included_quick_hits = []
    excluded_no_evidence = []

    for item in rank_result["top_three"]:
        claim = claim_index.get(item["canonical_id"])
        if claim is None:
            excluded_no_evidence.append(item)
        else:
            included_top_three.append((item, claim))

    for item in rank_result["quick_hits"]:
        claim = claim_index.get(item["canonical_id"])
        if claim is None:
            excluded_no_evidence.append(item)
        else:
            included_quick_hits.append((item, claim))

    segments = []

    top_n = len(included_top_three)
    quick_n = len(included_quick_hits)
    segments.append(_connective_segment(
        "intro",
        f"Welcome to the {show_name} for {_format_date_for_speech(run_date)}. "
        f"Today: {top_n} top {'story' if top_n == 1 else 'stories'} "
        f"and {quick_n} quick hit{'' if quick_n == 1 else 's'}.",
    ))
    segments.append(_connective_segment("disclosure", disclosure_text))

    for item, claim in included_top_three:
        segments.append(_story_segment("top_three_item", item, registry, claim))

    if included_quick_hits:
        segments.append(_connective_segment("quick_hits_transition", "Now, quick hits from across the industry."))
        for item, claim in included_quick_hits:
            segments.append(_story_segment("quick_hits_item", item, registry, claim))

    segments.append(_connective_segment("outro", f"That's the {show_name} for {_format_date_for_speech(run_date)}. See you next time."))

    return {
        "run_date": run_date,
        "show_name": show_name,
        "segments": segments,
        "excluded_no_evidence": excluded_no_evidence,
    }
