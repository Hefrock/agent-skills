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

Model choice confirmed live, not assumed — five real rounds of
reconnaissance via _diagnose_narration.py (deleted before this PR lands):
gemini-2.5-flash 404'd (deprecated for new callers, pointed to
gemini-3.6-flash); gemini-3.6-flash 503'd twice ("high demand") then hit
a 30s client read timeout on a third attempt; gemini-flash-latest — an
alias Google documents as always tracking their current recommended
flash model — hit the SAME 503, real evidence it currently resolves to
that same newest, overloaded model rather than routing around it.
gemini-3.5-flash, one generation behind the contested newest tier, then
succeeded cleanly: responseSchema was honored exactly as requested
({"narration": ..., "supporting_spans": [...]} came back as real JSON,
not free-form prose needing extraction), and on the one real example
tried, the model preserved a hedge ("may apply") the naive rewrite could
easily have flattened, with every supporting_span a genuine verbatim
substring of the source. DEFAULT_TEXT_MODEL below reflects that — the
newest tier may well stabilize later, but there's no live evidence for it
yet, and this pipeline's convention is to build on what's actually been
observed working, not on a name's assumed recency.

narrate_script()'s two-tier fallback (per-segment AND episode-level) is a
deliberate design decision, not the only reasonable one — it's the
resolution to a self-critique gap this module's design went through
before being built (see this project's own history: "no policy for a
'patchy' episode" was flagged as a real, unaddressed gap in an earlier
draft). Per-segment: a single story's narration failing its grounding
check doesn't sink the episode — that story's original, already-vetted
mechanical text (script_gen.py's own template output) is used instead,
exactly like any other per-item failure bucket in this pipeline
(rank.py's dropped_duplicates, evidence.py's skipped_no_summary).
Episode-level: if the FRACTION of attempted narrations that passed
grounding falls below success_threshold (~70%, the value decided in
conversation, not a default nobody chose), the entire episode reverts to
its all-mechanical script instead of shipping a script where narration
quality/consistency wildly varies story to story within one episode — a
day where the model is frequently failing grounding suggests something
systematically off (a bad day for the model, an unusual batch of source
text), not a handful of independent flukes worth patching around
individually.

stdlib only, matching this repo's other reference tooling."""

import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gemini_retry  # noqa: E402

DEFAULT_TEXT_MODEL = "gemini-3.5-flash"
DEFAULT_SUCCESS_THRESHOLD = 0.7
DEFAULT_LENGTH_RATIO_MIN = 0.4
DEFAULT_LENGTH_RATIO_MAX = 2.0

# Two-host dialogue mode (--host-style dialogue) — see this module's
# docstring for why a second AI voice is never free-generated here. Every
# "reactive" turn is picked from this fixed, hand-authored pool, never
# produced by a Gemini call: the whole point is that Host B can never
# invent a claim, because Host B's words are never generated at all. This
# keeps narrate_fn's per-story grounding discipline (see above) completely
# unchanged — dialogue mode is a presentation layer on top of the same
# single grounded narration, not a second source of prose to verify.
REACTIVE_TEMPLATES = {
    "top_three_item": [
        "That's a significant one.",
        "Let's dig into what that means.",
        "Worth pausing on this.",
    ],
    "quick_hits_item": [
        "Interesting.",
        "Noted.",
        "That's worth tracking.",
    ],
}
# Fallback pool for any segment_type not listed above (there currently is
# none, since narrate_script() only ever calls this for STORY_SEGMENT_TYPES
# — see script_gen.py — but pick_reactive_turn() stays defined for any
# segment_type rather than raising, matching this pipeline's general
# "degrade, don't crash on an unexpected but harmless shape" posture).
DEFAULT_REACTIVE_POOL_KEY = "quick_hits_item"

DIALOGUE_SPEAKER_A = "A"
DIALOGUE_SPEAKER_B = "B"


def pick_reactive_turn(segment_type: str, index: int) -> str:
    """Pure, deterministic selection from REACTIVE_TEMPLATES — no network,
    no randomness. A fixed (segment_type, index) always yields the same
    phrase, which keeps audio_synth.py's text-addressed cache correct and
    keeps tests non-flaky. index rotates through the pool (index % len)
    so an episode's several stories of the same segment_type don't all
    get the identical interjection back to back."""
    pool = REACTIVE_TEMPLATES.get(segment_type, REACTIVE_TEMPLATES[DEFAULT_REACTIVE_POOL_KEY])
    return pool[index % len(pool)]

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


def narrate_segment(segment: dict, api_key: str, narrate_fn=None, host_style: str = "single", reactive_index: int = 0) -> dict:
    """Attempts to narrate ONE story segment (script_gen.py's shape:
    {"segment_type", "text", "canonical_id", "claim_id", "source_id"}) in
    isolation — narrate_fn sees only this segment's own already-vetted
    "text", never another segment's, per this module's per-story-isolation
    design. narrate_fn defaults to generate_narration (below); tests
    inject a fake so this and narrate_script() can be proven without any
    network call, same convention as every other network-boundary
    function in this pipeline.

    Any exception from narrate_fn (including everything gemini_retry.py's
    retries in generate_narration() couldn't recover from) is treated
    exactly like a failed grounding check, not a special case — either
    way, this one story falls back to its original mechanical text.

    host_style="dialogue" (default "single") additionally sets "turns" on
    a SUCCESSFULLY narrated segment: [{"speaker": "A", "kind": "claim",
    "text": <the same grounded narration>}, {"speaker": "B",
    "kind": "reactive", "text": <pick_reactive_turn(...)>}]. "text" itself
    is always still set exactly as it was before this parameter existed —
    every existing consumer (qa_gate.py's checks, distribute.py,
    audio_synth.py's single-voice path) keeps working unchanged whether or
    not "turns" is present. A fallback segment (grounding failed, or
    host_style="single") never gets "turns" set at all — there is no
    partial-dialogue state; a story is either full two-voice dialogue or
    exactly today's single mechanical/narrated text, never a mix.

    Returns {"segment": <a segment dict — "text" replaced, and "turns"
    added, only if narration succeeded and passed
    check_narration_grounded()>, "narrated": bool, "reasons": [...]}."""
    if narrate_fn is None:
        narrate_fn = generate_narration

    try:
        result = narrate_fn(segment["text"], api_key)
    except Exception as e:
        return {"segment": segment, "narrated": False, "reasons": [f"{type(e).__name__}: {e}"]}

    check = check_narration_grounded(result.get("narration", ""), result.get("supporting_spans", []), segment["text"])
    if not check["passed"]:
        return {"segment": segment, "narrated": False, "reasons": check["reasons"]}

    new_segment = {**segment, "text": result["narration"]}
    if host_style == "dialogue":
        new_segment["turns"] = [
            {"speaker": DIALOGUE_SPEAKER_A, "kind": "claim", "text": result["narration"]},
            {"speaker": DIALOGUE_SPEAKER_B, "kind": "reactive", "text": pick_reactive_turn(segment["segment_type"], reactive_index)},
        ]
    return {"segment": new_segment, "narrated": True, "reasons": []}


def narrate_script(
    script: dict, api_key: str, narrate_fn=None, success_threshold: float = DEFAULT_SUCCESS_THRESHOLD, host_style: str = "single",
) -> dict:
    """Attempts narration for every STORY segment in script["segments"]
    (identified by claim_id is not None — script_gen.py's own convention
    for "this segment traces to a pinned claim", which is exactly the set
    this module's per-story-only design targets; connective segments
    including the disclosure are never touched, see this module's
    docstring).

    host_style ("single" default, or "dialogue"): passed straight through
    to narrate_segment() for every attempted segment — see its docstring
    for exactly what "dialogue" adds ("turns") and what stays identical
    either way ("text"). reactive_index uses each segment's own position
    in script["segments"] (i below), so pick_reactive_turn()'s rotation is
    deterministic across a whole episode, not just within one story.

    Two-tier fallback — see this module's docstring for the full
    reasoning, not repeated here:
      - Per-segment: a story whose narration fails its own grounding
        check keeps its original mechanical text; this alone does not
        block any other story.
      - Episode-level: if the fraction of ATTEMPTED narrations that
        passed grounding falls below success_threshold, every attempted
        segment reverts to its original mechanical text — even ones that
        individually passed — rather than shipping a script with wildly
        inconsistent narration quality within one episode. An episode
        with zero story segments to attempt (e.g. every selected story
        was excluded_no_evidence) trivially never triggers this — there
        is nothing to have failed.

    Returns:
      {
        "script": <script, with "segments" replaced by the narrated/
                   fallback mix decided above; every other key unchanged>,
        "narration_attempted": int,
        "narration_succeeded": int,
        "narration_success_rate": float,   # 1.0 when narration_attempted == 0
        "episode_level_fallback": bool,
        "narration_failures": [{"canonical_id", "reasons"}, ...],
      }"""
    if narrate_fn is None:
        narrate_fn = generate_narration

    segments = script["segments"]
    attempts = {
        i: narrate_segment(s, api_key, narrate_fn, host_style=host_style, reactive_index=i)
        for i, s in enumerate(segments) if s["claim_id"] is not None
    }

    attempted = len(attempts)
    succeeded = sum(1 for a in attempts.values() if a["narrated"])
    success_rate = (succeeded / attempted) if attempted else 1.0
    episode_level_fallback = attempted > 0 and success_rate < success_threshold

    new_segments = list(segments)
    if not episode_level_fallback:
        for i, a in attempts.items():
            new_segments[i] = a["segment"]

    return {
        "script": {**script, "segments": new_segments},
        "narration_attempted": attempted,
        "narration_succeeded": succeeded,
        "narration_success_rate": success_rate,
        "episode_level_fallback": episode_level_fallback,
        "narration_failures": [
            {"canonical_id": segments[i]["canonical_id"], "reasons": a["reasons"]}
            for i, a in attempts.items() if not a["narrated"]
        ],
    }


# ── Network wrapper (not used by anything above) ────────────────────────

def generate_narration(
    source_text: str,
    api_key: str,
    model: str = DEFAULT_TEXT_MODEL,
    timeout: float = 30.0,
    max_attempts: int = 3,
    backoff_base_seconds: float = 5.0,
) -> dict:
    """Calls Gemini's generateContent with structured JSON output
    (responseMimeType: "application/json" + a responseSchema requesting
    {"narration": STRING, "supporting_spans": ARRAY of STRING}) for
    exactly one story's own source_text — never a whole script, per this
    module's per-story-isolation design. The one function in this module
    that touches the network, kept separate so everything above can be
    unit-tested without it, same split as dedup_store.embed_text() and
    audio_synth.synthesize_text().

    Prompt and response shape confirmed live via _diagnose_narration.py's
    reconnaissance (deleted before this PR lands) — see this module's
    docstring for why gemini-3.5-flash specifically, not a newer model
    that looked more current on paper but had no live evidence behind it
    yet. Requires outbound access to generativelanguage.googleapis.com.

    Retries up to max_attempts times via gemini_retry.py — same policy
    audio_synth.synthesize_text() uses (honoring the server's own
    Retry-After header when present, conservative exponential backoff
    otherwise), same reason: this pipeline already learned live that
    blind, aggressive retries against a rate-limited endpoint make things
    WORSE, not better.

    Returns {"narration": str, "supporting_spans": [str, ...]}. Raises if
    the call ultimately fails or the response can't be parsed into that
    shape — narrate_segment() above is the one that decides what a
    failure here means for the episode, not this function."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    prompt = (
        "Rewrite the following healthcare AI news item as 1-2 sentences of natural, "
        "conversational spoken narration for a podcast host introducing this story. "
        "Use ONLY the facts stated in the source text below — do not add any information, "
        "names, numbers, or claims not present in it. Preserve any hedging or attribution "
        "language (e.g. 'may', 'could', 'according to') if present — do not state a hedged "
        "claim as certain fact.\n\n"
        f"Source text: {source_text}"
    )
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "narration": {"type": "STRING"},
                    "supporting_spans": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["narration", "supporting_spans"],
            },
        },
    }).encode("utf-8")

    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            return {"narration": parsed["narration"], "supporting_spans": parsed["supporting_spans"]}
        except Exception as e:
            if attempt == max_attempts - 1 or not gemini_retry.is_retryable(e):
                raise
            time.sleep(gemini_retry.retry_delay_seconds(e, attempt, backoff_base_seconds))
