#!/usr/bin/env python3
"""Live network smoke test for broadcast's ingest adapters,
dedup_store.embed_text(), narrate.generate_narration(), and
audio_synth.synthesize_text().

NOT part of the regular test suite (test_ingest.py, test_dedup_store.py,
etc. stay hermetic and network-free by design) — this is a separate,
manually-triggered check that exercises the real external APIs. It exists
to close specific verification gaps: fetch_pubmed/fetch_arxiv/fetch_rss
had only ever been confirmed to correctly SURFACE a blocked-network error
(this dev sandbox's egress policy), never to return a real successful
response; embed_text(), generate_narration(), and synthesize_text() had
never been executed against the live Gemini API at all. Passing here is
the actual close of those gaps — not an inference by analogy from a
different code path succeeding elsewhere.

The narration check is a real end-to-end proof, not just "didn't throw":
it feeds generate_narration() a real source sentence containing a hedge
marker, then runs the actual result through check_narration_grounded()
(the same grounding check narrate_segment() uses in a real episode) —
confirming the live model's output actually passes this pipeline's own
grounding bar, not merely that the API call succeeded. Before this check
existed, generate_narration() was only ever exercised live as a side
effect of a full orchestrate.py episode run — there was no cheap,
dedicated way to confirm it works without also paying for a full TTS
run.

Also doubles as the real verification for the three RSS feed_url values,
which config/sources.json marks feed_url_verified: false — a wrong URL
here surfaces as a real, actionable failure, not a silent "trust me."

Run manually via the "broadcast live smoke test" GitHub Actions workflow
(workflow_dispatch), or locally from an environment with real network
egress: python live_smoke_test.py

Exits non-zero if any check that should be reachable fails. The Gemini
embeddings/narration/TTS checks are SKIPPED (not failed) if GEMINI_API_KEY
isn't set in the environment, since that secret is configured separately
and its
absence isn't itself a failure of this script."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest  # noqa: E402
import dedup_store  # noqa: E402
import source_registry  # noqa: E402
import narrate  # noqa: E402
import audio_synth  # noqa: E402

passed = 0
failed = 0
skipped = 0


def check(label, fn):
    global passed, failed
    try:
        detail = fn()
        print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))
        passed += 1
    except Exception as e:
        print(f"  FAIL  {label} — {type(e).__name__}: {e}")
        failed += 1


def skip(label, reason):
    global skipped
    print(f"  SKIP  {label} — {reason}")
    skipped += 1


registry = source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))

print("── PubMed ──────────────────────────────────────────────────")
pubmed_source = source_registry.get_source(registry, "pubmed")


def _pubmed():
    items = ingest.fetch_pubmed(pubmed_source["query"], max_results=3)
    assert len(items) > 0, "query returned zero items"
    assert items[0]["title"], "first item has no title"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_pubmed returns real results", _pubmed)

print("\n── arXiv ────────────────────────────────────────────────────")
arxiv_source = source_registry.get_source(registry, "arxiv")


def _arxiv():
    items = ingest.fetch_arxiv(arxiv_source["query"], max_results=3)
    assert len(items) > 0, "query returned zero items"
    assert items[0]["title"], "first item has no title"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_arxiv returns real results", _arxiv)

print("\n── medRxiv ──────────────────────────────────────────────────")


def _medrxiv():
    items = ingest.fetch_medrxiv(days=14, max_results=5)
    assert len(items) > 0, "no postings in the last 14 days (unlikely — check the API response shape)"
    assert items[0]["title"], "first item has no title"
    assert items[0]["id_hint"].startswith("doi:"), "first item has no doi id_hint"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_medrxiv returns real results", _medrxiv)

print("\n── FDA guidance documents ───────────────────────────────────")


def _fda_guidance():
    items = ingest.fetch_fda_guidance(max_results=5)
    assert len(items) > 0, "the static datatables JSON returned zero usable records"
    assert items[0]["title"], "first item has no title"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_fda_guidance returns real results", _fda_guidance)

print("\n── regulations.gov ──────────────────────────────────────────")
regulations_gov_source = source_registry.get_source(registry, "regulations_gov")


def _regulations_gov():
    regulations_gov_api_key = os.environ.get("REGULATIONS_GOV_API_KEY")
    items = ingest.fetch_regulations_gov(
        regulations_gov_source["query"], days=365, max_results=5, api_key=regulations_gov_api_key,
    )
    assert len(items) > 0, "query returned zero documents (DEMO_KEY rate-limited? see the module docstring — set REGULATIONS_GOV_API_KEY for a real key)"
    assert items[0]["title"], "first item has no title"
    key_label = "a real REGULATIONS_GOV_API_KEY" if regulations_gov_api_key else "the shared DEMO_KEY (set REGULATIONS_GOV_API_KEY for a real one)"
    return f"{len(items)} items via {key_label}, e.g. '{items[0]['title'][:60]}'"


check("fetch_regulations_gov returns real results", _regulations_gov)

print("\n── ONC/ASTP blog ────────────────────────────────────────────")
onc_astp_source = source_registry.get_source(registry, "onc_astp")


def _onc_astp():
    items = ingest.fetch_rss(onc_astp_source["feed_url"], "onc_astp")
    assert len(items) > 0, "feed returned zero items"
    assert items[0]["title"], "first item has no title"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_rss(onc_astp) returns real results", _onc_astp)

print("\n── CMS newsroom ──────────────────────────────────────────────")
cms_source = source_registry.get_source(registry, "cms")


def _cms():
    items = ingest.fetch_rss(cms_source["feed_url"], "cms")
    assert len(items) > 0, "feed returned zero items"
    assert items[0]["title"], "first item has no title"
    assert not items[0]["url"].startswith("https://www.cms.gov/%3C"), "link recovery from the corrupted <link> field failed"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_rss(cms) returns real results", _cms)

print("\n── Industry RSS feeds ──────────────────────────────────────")
for key in ("stat_news", "fierce_healthcare", "hit_consultant"):
    source = source_registry.get_source(registry, key)

    def _rss(feed_url=source["feed_url"], source_key=key):
        items = ingest.fetch_rss(feed_url, source_key)
        assert len(items) > 0, "feed returned zero items"
        assert items[0]["title"], "first item has no title"
        return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"

    check(f"fetch_rss({key}) returns real results — feed_url_verified was false, this is the verification", _rss)

print("\n── Gemini embeddings ────────────────────────────────────────")
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    skip("embed_text returns a real embedding vector", "GEMINI_API_KEY not set in environment")
else:
    def _embed():
        vec = dedup_store.embed_text("Test sentence for embedding verification.", api_key)
        assert isinstance(vec, list) and len(vec) > 0, "embedding vector is empty or not a list"
        assert all(isinstance(x, (int, float)) for x in vec), "embedding contains non-numeric values"
        return f"{len(vec)}-dim vector, first values: {[round(v, 4) for v in vec[:3]]}"

    check("embed_text returns a real embedding vector", _embed)

print("\n── Gemini narration ─────────────────────────────────────────")
if not api_key:
    skip("generate_narration returns grounded narration", "GEMINI_API_KEY not set in environment")
else:
    def _narrate():
        source_text = (
            "A new FDA guidance document suggests that AI-based clinical decision "
            "support tools may require additional premarket review starting next year."
        )
        result = narrate.generate_narration(source_text, api_key)
        assert result.get("narration"), "narration is empty"
        assert isinstance(result.get("supporting_spans"), list), "supporting_spans missing or not a list"
        check_result = narrate.check_narration_grounded(result["narration"], result["supporting_spans"], source_text)
        assert check_result["passed"], f"live narration failed this pipeline's own grounding check: {check_result['reasons']}"
        return f"'{result['narration'][:80]}...' — {len(result['supporting_spans'])} supporting span(s), grounding check passed"

    check("generate_narration returns grounded narration", _narrate)

print("\n── Gemini TTS ───────────────────────────────────────────────")
if not api_key:
    skip("synthesize_text returns a real WAV clip", "GEMINI_API_KEY not set in environment")
else:
    def _tts():
        import wave
        import io
        wav_bytes = audio_synth.synthesize_text("Say cheerfully: Have a wonderful day!", api_key)
        assert wav_bytes[:4] == b"RIFF", "output does not start with a RIFF/WAV header"
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getnframes() > 0, "WAV clip has zero frames"
            return f"{wf.getnframes()} frames, {wf.getnchannels()}ch, {wf.getframerate()}Hz, {len(wav_bytes)} bytes"

    check("synthesize_text returns a real WAV clip", _tts)

print(f"\n{'─' * 62}\n  {passed} passed  —  {failed} failed  —  {skipped} skipped")
if failed > 0:
    sys.exit(1)
