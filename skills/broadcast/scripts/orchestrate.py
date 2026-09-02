#!/usr/bin/env python3
"""Orchestration driver (Phase 8a) — the first place in this pipeline that
actually wires ingest -> rank -> evidence pinning -> script generation ->
QA gate -> audio synthesis together into one real episode. Every stage
before this PR has only ever been unit-tested in isolation; this is the
integration point that proves those stages' output/input shapes actually
compose, the same way every ingest adapter's live smoke test proved its
network call actually worked rather than just looking right on paper.

Same discipline as the rest of this pipeline: the parts that are pure
orchestration logic (aggregating per-source results, deciding which
fetcher a source needs, pairing failures with the item/source that
caused them) are separated from the parts that touch the network or a
spawned subprocess, and every network/subprocess-touching function is
injectable so run_episode()'s full wiring can be proven with fakes, with
no network call and no spawned process — see test_orchestrate.py's
FakeWiring tests, which are exactly that proof.

run_episode() itself never starts or closes the EvidencePinningClient it's
given — same "caller owns the resource" convention evidence.py and
qa_gate.py already use — and never persists anything to disk itself
(store, episode audio); that's the __main__ CLI block's job below,
matching dedup_store's own load_store()/save_store() split between pure
logic and explicit, caller-controlled I/O.

Query strings for the three sources that need one (pubmed, arxiv,
regulations_gov) live in config/sources.json's "query" fields, not
hardcoded here — see that file's query_note fields for why: they're
carried over as-is from live_smoke_test.py's already-confirmed-working
queries, a documented judgment call, not a validated one, same status as
every other hand-tuned rubric value in this pipeline.

stdlib only for the orchestration logic itself; the CLI block additionally
depends on this repo's own ingest.py/dedup_store.py/source_registry.py/
rank.py/evidence.py/evidence_pinning_client.py/script_gen.py/qa_gate.py/
audio_synth.py, all already stdlib-only themselves."""

import argparse
import json
import os
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest  # noqa: E402
import dedup_store  # noqa: E402
import source_registry  # noqa: E402
import rank  # noqa: E402
import evidence  # noqa: E402
import evidence_pinning_client  # noqa: E402
import script_gen  # noqa: E402
import narrate  # noqa: E402
import qa_gate  # noqa: E402
import audio_synth  # noqa: E402

DEFAULT_MAX_RESULTS_PER_SOURCE = 10
# 6s (~10 calls/minute) is deliberately conservative, not a guess: a 2s
# pace (~30/min) was tried live first and made a real rate-limit
# situation WORSE on the very next run (more segments failed, not
# fewer) — every one of audio_synth.synthesize_text()'s own retries is
# itself another request competing for the same tight quota, so pacing
# has to assume that quota is lower than it looks from a single
# successful call, not higher.
DEFAULT_SYNTH_DELAY_SECONDS = 6.0


def _fetch_for_source(source: dict, max_results: int) -> list[dict]:
    """Dispatches one config/sources.json source entry to the right
    ingest.py fetcher. Every RSS-backed source (identified by a
    "feed_url" field, not by name — this is how the five industry/agency
    RSS sources already share one generic parser) goes through
    ingest.fetch_rss, which has no max_results param of its own (a feed
    returns whatever it returns; rank.py's own selection does the
    down-selecting). The three query-based sources read their query from
    the source's own "query" field. medRxiv and FDA guidance need
    neither a feed_url nor a query — they're each a single fixed
    endpoint."""
    key = source["key"]
    if "feed_url" in source:
        return ingest.fetch_rss(source["feed_url"], key)
    if key == "pubmed":
        return ingest.fetch_pubmed(source["query"], max_results=max_results)
    if key == "arxiv":
        return ingest.fetch_arxiv(source["query"], max_results=max_results)
    if key == "medrxiv":
        return ingest.fetch_medrxiv(max_results=max_results)
    if key == "fda_guidance":
        return ingest.fetch_fda_guidance(max_results=max_results)
    if key == "regulations_gov":
        return ingest.fetch_regulations_gov(source["query"], max_results=max_results)
    raise ValueError(f"no fetch dispatch registered for source '{key}' (no feed_url, and not one of the known query-based/fixed sources)")


def ingest_all(registry: dict, max_results_per_source: int = DEFAULT_MAX_RESULTS_PER_SOURCE, fetch_fn=_fetch_for_source) -> dict:
    """Fetches every source in registry["sources"] via fetch_fn (defaults
    to the real dispatcher above; tests inject a fake to avoid network).
    One source's failure doesn't abort the whole ingest pass — the same
    "skip the bad one, keep going, report it explicitly" policy every
    other batch stage in this pipeline already uses (rank.py's dropped
    duplicates, evidence.py's per-item catch, qa_gate's run-every-check).
    This is also where healthcare_it_news's documented, permanent block
    (config/sources.json's feed_url_verified: false) surfaces at runtime:
    as one entry in "failed", not a crash.

    Returns {"items": [...], "failed": [{"source_key", "error"}, ...]}."""
    items = []
    failed = []
    for source in registry["sources"]:
        try:
            items.extend(fetch_fn(source, max_results_per_source))
        except Exception as e:
            failed.append({"source_key": source["key"], "error": f"{type(e).__name__}: {e}"})
    return {"items": items, "failed": failed}


def embed_items(items: list[dict], api_key: str, embed_fn=dedup_store.embed_text) -> dict:
    """Computes one embedding per item via embed_fn (defaults to the real
    dedup_store.embed_text; tests inject a fake). Same batch-resilience
    policy as ingest_all: one item's embedding failure drops that item
    from both the returned items and embeddings lists — kept aligned,
    since rank_stories() requires items and embeddings to be the exact
    same length and order — and records it into failed instead of
    aborting the whole batch.

    Returns {"items": [...], "embeddings": [...], "failed": [{"item", "error"}, ...]}."""
    ok_items = []
    embeddings = []
    failed = []
    for item in items:
        try:
            embeddings.append(embed_fn(f"{item['title']} {item['summary']}", api_key))
            ok_items.append(item)
        except Exception as e:
            failed.append({"item": item, "error": f"{type(e).__name__}: {e}"})
    return {"items": ok_items, "embeddings": embeddings, "failed": failed}


def run_episode(
    run_date: str,
    registry: dict,
    store: dict,
    evidence_client,
    api_key: str,
    max_results_per_source: int = DEFAULT_MAX_RESULTS_PER_SOURCE,
    fetch_fn=_fetch_for_source,
    embed_fn=dedup_store.embed_text,
    narrate_fn=narrate.generate_narration,
    synth_fn=audio_synth.synthesize_text,
    show_name: str = "Healthcare AI Briefing",
    synth_delay_seconds: float = 0.0,
    enable_narration: bool = True,
    narration_success_threshold: float = narrate.DEFAULT_SUCCESS_THRESHOLD,
) -> dict:
    """The full pipeline for one day's episode, wired end to end: ingest
    -> embed -> rank -> pin evidence -> generate script -> narrate (best-
    effort, see below) -> QA gate -> (only if the gate passes) synthesize
    audio -> assemble one episode WAV. This is deliberately the first
    place in this pipeline that exercises every stage in one call; every
    stage before this PR was only ever unit-tested in isolation.

    evidence_client must already be a started EvidencePinningClient (or,
    in tests, a fake exposing the same register_source/pin_claim/
    verify_claim method shapes) — this function never starts or closes
    one itself, same "caller owns the resource" convention evidence.py
    and qa_gate.py already use.

    fetch_fn/embed_fn/narrate_fn/synth_fn default to the real network-
    touching functions; tests inject fakes so the full wiring — every
    stage's real output shape actually feeding the next stage's expected
    input shape — can be proven without any network call or spawned
    process. narrate_fn is narrate.py's PER-STORY generate_narration()
    (the same "inject the network call, not the orchestration around it"
    level fetch_fn/embed_fn/synth_fn already use) — narrate.narrate_script()
    itself, the pure orchestration wrapping it, is always called for
    real, same as audio_synth.assemble_episode_audio() never being
    injected either.

    enable_narration (default True — this pipeline's whole point,
    decided in conversation, is genuinely AI-generated narration, not a
    template pretending to be one) runs narrate.narrate_script() on the
    script generate_script() just produced, BEFORE the QA gate — qa_gate.
    gate() never compares a segment's text against the pinned claim's
    stored text verbatim (only claim_id/source_id/canonical_id presence
    and a live re-verification that the claim is still pinned, see
    qa_gate.py's own docstring), so a narrated paraphrase passes the same
    structural checks the original mechanical text would. Narration's own
    two-tier fallback (see narrate.py's docstring) means a bad or
    unreachable narration call degrades this episode's prose quality, not
    its grounding — every story segment qa_gate then checks still traces
    to a real pinned claim regardless of whether narration succeeded,
    partially succeeded, or was skipped entirely. Set False to reproduce
    the pre-narration, purely mechanical script (e.g. for a cost-
    conscious or narration-API-down run) without touching any other stage.

    Never raises on a per-item failure anywhere in the pipeline (every
    stage already has its own explicit failure bucket, all threaded
    through into the return value below). If any segment fails to
    synthesize, the whole episode's audio is withheld (None) rather than
    assembled with a missing segment silently skipped — assemble_episode_
    audio() requires exact 1:1 alignment between script segments and
    synthesized clips, so a partial batch can't be safely assembled at
    all, unlike every other stage's "drop the one bad item" policy.

    synth_delay_seconds (default 0.0 here — see DEFAULT_SYNTH_DELAY_SECONDS
    and the __main__ CLI below, which actually opts into a nonzero one)
    is a real, live-confirmed necessity, not a defensive guess:
    orchestrate.py's first real end-to-end GitHub Actions run synthesized
    13 segments in a tight sequential loop with no delay and hit Gemini
    TTS's real rate limit (3 HTTP 429s, 4 more read timeouts almost
    certainly from the same throttling). A small delay between calls
    here is the first line of defense; synth_fn's own retry-with-backoff
    (see audio_synth.synthesize_text's _is_retryable()) is the second,
    for whatever the delay alone doesn't prevent. Defaulting to 0.0 here
    keeps run_episode() itself fast and predictable for callers that
    don't need it (tests included) — the delay is an orchestration-time
    policy choice, not something inherent to a single synthesis call.

    Returns:
      {
        "run_date", "ingest_failed", "embed_failed",
        "rank_result", "pinned", "script", "narration_result", "qa_result", "synth_failed",
        "episode_audio",   # None if the QA gate failed OR any segment failed to synthesize
        "store",           # caller must dedup_store.save_store() this
      }
      narration_result is None when enable_narration is False; otherwise
      narrate.narrate_script()'s own return value (narration_attempted/
      succeeded/success_rate, episode_level_fallback, narration_failures)
      — "script" above is already the POST-narration script (or the
      unmodified one, if enable_narration is False), same "one source of
      truth, not the pre- and post- versions both floating around" policy
      the rest of this pipeline already follows."""
    ingest_result = ingest_all(registry, max_results_per_source, fetch_fn)
    embed_result = embed_items(ingest_result["items"], api_key, embed_fn)

    rank_result = rank.rank_stories(embed_result["items"], embed_result["embeddings"], registry, store, current_date=run_date)

    selected = rank_result["top_three"] + rank_result["quick_hits"]
    pinned = evidence.pin_evidence_for_stories(evidence_client, selected, run_id=run_date)

    script = script_gen.generate_script(rank_result, pinned, registry, run_date, show_name=show_name)

    narration_result = None
    if enable_narration:
        narration_result = narrate.narrate_script(script, api_key, narrate_fn=narrate_fn, success_threshold=narration_success_threshold)
        script = narration_result["script"]

    qa_result = qa_gate.gate(script, client=evidence_client)

    episode_audio = None
    synth_failed = []
    if qa_result["passed"]:
        segment_audio = []
        for i, segment in enumerate(script["segments"]):
            if i > 0 and synth_delay_seconds > 0:
                time.sleep(synth_delay_seconds)
            try:
                segment_audio.append(synth_fn(segment["text"], api_key))
            except Exception as e:
                synth_failed.append({"segment_type": segment["segment_type"], "canonical_id": segment["canonical_id"], "error": f"{type(e).__name__}: {e}"})
        if not synth_failed:
            episode_audio = audio_synth.assemble_episode_audio(script, segment_audio)

    return {
        "run_date": run_date,
        "ingest_failed": ingest_result["failed"],
        "embed_failed": embed_result["failed"],
        "rank_result": rank_result,
        "pinned": pinned,
        "script": script,
        "narration_result": narration_result,
        "qa_result": qa_result,
        "synth_failed": synth_failed,
        "episode_audio": episode_audio,
        "store": rank_result["store"],
    }


# ── CLI driver — the one place in this module that touches disk ─────────

def _report_json(result: dict) -> dict:
    """A JSON-serializable summary of run_episode()'s result: everything
    except the raw audio bytes (episode_audio's WAV bytes, if present,
    are written to their own .wav file instead — see main() below) and
    the full ranked/store internals (already large, already implicit in
    the counts below). Kept separate from run_episode()'s own return
    value so that dict can stay the single source of truth for a caller
    that wants the real data, not a lossy summary."""
    qa = result["qa_result"]
    narration = result["narration_result"]
    return {
        "run_date": result["run_date"],
        "ingest_failed": result["ingest_failed"],
        "embed_failed": result["embed_failed"],
        "top_three_count": len(result["rank_result"]["top_three"]),
        "quick_hits_count": len(result["rank_result"]["quick_hits"]),
        "dropped_duplicates_count": len(result["rank_result"]["dropped_duplicates"]),
        "pinned_count": len(result["pinned"]["pinned"]),
        "pinned_skipped_no_summary_count": len(result["pinned"]["skipped_no_summary"]),
        "pinned_failed_count": len(result["pinned"]["failed"]),
        "excluded_no_evidence_count": len(result["script"]["excluded_no_evidence"]),
        "segment_count": len(result["script"]["segments"]),
        # narration_attempted/succeeded/success_rate/episode_level_fallback/
        # narration_failures are all None when narration was disabled for
        # this run (--no-narration) — distinguishable from a real 0/0 run
        # (a narrated episode with zero story segments), which reports 0s.
        "narration_attempted": narration["narration_attempted"] if narration else None,
        "narration_succeeded": narration["narration_succeeded"] if narration else None,
        "narration_success_rate": narration["narration_success_rate"] if narration else None,
        "narration_episode_level_fallback": narration["episode_level_fallback"] if narration else None,
        "narration_failures": narration["narration_failures"] if narration else None,
        "qa_passed": qa["passed"],
        "qa_checks": qa["checks"],
        "synth_failed": result["synth_failed"],
        "episode_produced": result["episode_audio"] is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, help="Directory for persistent state (dedup store, evidence store) and this run's output. Not inside the repo — this is runtime state, not source.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Run date, ISO format (default: today).")
    parser.add_argument("--max-results-per-source", type=int, default=DEFAULT_MAX_RESULTS_PER_SOURCE)
    parser.add_argument(
        "--synth-delay-seconds", type=float, default=DEFAULT_SYNTH_DELAY_SECONDS,
        help="Pause between synthesize_text() calls, to stay under Gemini TTS's real rate limit (confirmed live: a tight sequential loop with no delay hit HTTP 429s and read timeouts).",
    )
    parser.add_argument(
        "--no-narration", dest="enable_narration", action="store_false",
        help="Skip narrate.py entirely and ship the mechanical script_gen.py text as-is (e.g. for a cost-conscious run, or if the narration API is known to be down). Default: narration is enabled.",
    )
    parser.add_argument(
        "--narration-success-threshold", type=float, default=narrate.DEFAULT_SUCCESS_THRESHOLD,
        help="Fraction of attempted per-story narrations that must pass grounding for the episode to keep any of them — see narrate.py's docstring for why this is an episode-level, not just per-segment, threshold.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY must be set — embeddings, narration, and audio synthesis all require it.", file=sys.stderr)
        return 2

    os.makedirs(args.data_dir, exist_ok=True)
    registry_path = os.path.join(HERE, "..", "config", "sources.json")
    store_path = os.path.join(args.data_dir, "dedup_store.json")
    evidence_store_path = os.path.join(args.data_dir, "evidence_store")
    episode_dir = os.path.join(args.data_dir, "episodes", args.date)

    registry = source_registry.load_registry(registry_path)
    store = dedup_store.load_store(store_path)  # already returns a fresh {"entries": []} store if store_path doesn't exist yet

    with evidence_pinning_client.EvidencePinningClient(store_path=evidence_store_path) as client:
        result = run_episode(
            args.date, registry, store, client, api_key,
            max_results_per_source=args.max_results_per_source, synth_delay_seconds=args.synth_delay_seconds,
            enable_narration=args.enable_narration, narration_success_threshold=args.narration_success_threshold,
        )

    dedup_store.save_store(result["store"], store_path)

    os.makedirs(episode_dir, exist_ok=True)
    report = _report_json(result)
    with open(os.path.join(episode_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # The full script (unlike report.json's counts-only summary) is what
    # distribute.py (Phase 8b) needs to build a feed description and vault
    # note from real story content — persisted here since script.json's
    # segments are plain JSON-safe dicts (no raw audio bytes), the same
    # "pure data out, caller decides what to persist" boundary every
    # other stage already draws.
    with open(os.path.join(episode_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(result["script"], f, indent=2)

    if result["episode_audio"] is not None:
        with open(os.path.join(episode_dir, "episode.wav"), "wb") as f:
            f.write(result["episode_audio"]["full_episode_wav"])

    print(json.dumps(report, indent=2))
    return 0 if report["episode_produced"] else 1


if __name__ == "__main__":
    sys.exit(main())
