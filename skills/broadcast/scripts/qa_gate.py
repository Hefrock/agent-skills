#!/usr/bin/env python3
"""QA gate (Phase 6) — the checkpoint between script generation (Phase 5)
and audio synthesis (Phase 7): a script that fails this gate must not be
synthesized or published.

Two tiers, matching the split every prior stage in this pipeline has
used between pure logic and a thin network/server call:

  - run_checks() / gate(): structural checks over script_gen.py's own
    output — no network, no client needed. These re-verify invariants
    generate_script() already enforces internally (every story segment
    is grounded, no story from excluded_no_evidence leaked in, intro/
    outro present, no empty narration, no duplicate story). Re-checking
    them here is deliberate defense in depth, the same posture
    source_registry.validate_registry() takes for a hand-edited
    config/sources.json: a stage's own internal correctness shouldn't be
    the only thing standing between a bug and a published episode,
    especially once a script has been serialized to disk and read back
    in by a later pipeline run rather than passed directly in memory.

  - the optional claims-still-pinned check (only run when a started
    EvidencePinningClient is passed to gate()): re-verifies every story
    segment's claim_id against the real evidence-pinning-mcp server,
    catching a claim that was pinned during script generation but has
    since been flagged (e.g. by a human reviewer, via flag_claim) before
    audio synthesis runs — a real time-of-check/time-of-use gap this
    pipeline can genuinely have, since script generation and audio
    synthesis are separate pipeline stages, not one atomic step.

stdlib only, matching this repo's other reference tooling."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from evidence_pinning_client import EvidencePinningError  # noqa: E402

STORY_SEGMENT_TYPES = {"top_three_item", "quick_hits_item"}


def _check(name: str, passed: bool, detail: str = "") -> dict:
    return {"check": name, "passed": passed, "detail": detail}


def _check_has_intro(segments: list[dict]) -> dict:
    passed = bool(segments) and segments[0]["segment_type"] == "intro"
    return _check("has_intro", passed, "" if passed else "first segment is not an intro segment")


def _check_has_outro(segments: list[dict]) -> dict:
    passed = bool(segments) and segments[-1]["segment_type"] == "outro"
    return _check("has_outro", passed, "" if passed else "last segment is not an outro segment")


def _check_no_empty_text(segments: list[dict]) -> dict:
    empty = [s["segment_type"] for s in segments if not s.get("text", "").strip()]
    passed = not empty
    return _check("no_empty_text", passed, "" if passed else f"empty narration text in segment(s): {empty}")


def _check_story_segments_grounded(segments: list[dict]) -> dict:
    ungrounded = [
        s["segment_type"]
        for s in segments
        if s["segment_type"] in STORY_SEGMENT_TYPES
        and (s.get("claim_id") is None or s.get("source_id") is None or s.get("canonical_id") is None)
    ]
    passed = not ungrounded
    return _check(
        "story_segments_grounded", passed, "" if passed else f"story segment(s) missing claim_id/source_id/canonical_id: {ungrounded}"
    )


def _check_no_duplicate_canonical_ids(segments: list[dict]) -> dict:
    story_ids = [s["canonical_id"] for s in segments if s["segment_type"] in STORY_SEGMENT_TYPES]
    duplicates = sorted({cid for cid in story_ids if story_ids.count(cid) > 1})
    passed = not duplicates
    return _check("no_duplicate_stories", passed, "" if passed else f"canonical_id(s) cited more than once: {duplicates}")


def _check_quick_hits_transition_consistency(segments: list[dict]) -> dict:
    quick_hit_indices = [i for i, s in enumerate(segments) if s["segment_type"] == "quick_hits_item"]
    transition_indices = [i for i, s in enumerate(segments) if s["segment_type"] == "quick_hits_transition"]

    if quick_hit_indices:
        passed = len(transition_indices) == 1 and transition_indices[0] < quick_hit_indices[0]
        detail = "" if passed else "quick_hits_item segment(s) present without exactly one preceding quick_hits_transition segment"
    else:
        passed = len(transition_indices) == 0
        detail = "" if passed else "quick_hits_transition segment present with no quick_hits_item segments following it"

    return _check("quick_hits_transition_consistency", passed, detail)


def _check_no_excluded_stories_leaked(script: dict) -> dict:
    excluded_ids = {item["canonical_id"] for item in script.get("excluded_no_evidence", [])}
    segment_ids = {s["canonical_id"] for s in script["segments"] if s["canonical_id"] is not None}
    leaked = sorted(excluded_ids & segment_ids)
    passed = not leaked
    return _check("no_excluded_stories_leaked", passed, "" if passed else f"excluded_no_evidence canonical_id(s) still cited in segments: {leaked}")


def run_checks(script: dict) -> list[dict]:
    """Structural checks only — no network, no client. Returns a list of
    {"check", "passed", "detail"} dicts, one per check, always run in
    full (a later check isn't skipped just because an earlier one
    failed, so gate() output shows every problem at once, not just the
    first)."""
    segments = script["segments"]
    return [
        _check_has_intro(segments),
        _check_has_outro(segments),
        _check_no_empty_text(segments),
        _check_story_segments_grounded(segments),
        _check_no_duplicate_canonical_ids(segments),
        _check_quick_hits_transition_consistency(segments),
        _check_no_excluded_stories_leaked(script),
    ]


def _check_claims_still_pinned(script: dict, client) -> dict:
    flagged_or_missing = []
    for segment in script["segments"]:
        if segment["segment_type"] not in STORY_SEGMENT_TYPES:
            continue
        try:
            claim = client.verify_claim(segment["claim_id"])
        except EvidencePinningError:
            flagged_or_missing.append(segment["claim_id"])
            continue
        if claim["status"] != "pinned":
            flagged_or_missing.append(segment["claim_id"])

    passed = not flagged_or_missing
    detail = "" if passed else f"claim_id(s) no longer verifiably pinned (flagged or missing): {flagged_or_missing}"
    return _check("claims_still_pinned", passed, detail)


def gate(script: dict, client=None) -> dict:
    """Runs run_checks() and, if a started EvidencePinningClient is
    passed as client, also re-verifies every story segment's claim
    against the real server. Returns {"checks": [...], "passed": bool}
    — passed is True only if every check passed."""
    checks = run_checks(script)
    if client is not None:
        checks.append(_check_claims_still_pinned(script, client))
    return {"checks": checks, "passed": all(c["passed"] for c in checks)}
