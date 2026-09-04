#!/usr/bin/env python3
"""Tests for orchestrate.py.

Three layers, matching the module's own split:
  - _fetch_for_source: dispatch-table correctness, verified with
    unittest.mock so no real ingest.py fetcher is ever actually called
    (still no network).
  - ingest_all / embed_items: pure orchestration/batch-resilience logic,
    driven with fake fetch_fn/embed_fn callables — no network.
  - RunEpisodeWiring: the big one. Drives run_episode() — the first
    place in this whole pipeline that exercises every stage in one
    call — entirely with fakes (a fake evidence client implementing
    EvidencePinningClient's real method shapes, a fake fetch_fn, a fake
    embed_fn, a fake synth_fn) against the REAL config/sources.json via
    source_registry.load_registry(). This is a genuine proof that every
    stage's real output shape feeds the next stage's real expected input
    shape — rank.py's output really does feed evidence.py, evidence.py's
    really does feed script_gen.py, script_gen.py's really does feed
    qa_gate.py and audio_synth.py — without requiring network, a spawned
    node process, or a live API key to check it.

Run: python test_orchestrate.py"""

import array
import hashlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
import wave
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "..", "config", "sources.json")


def load(name):
    # sys.modules registration matters here for the same reason it does
    # in test_evidence.py: orchestrate.py does its own plain
    # `import audio_synth` (etc.) internally, and RunEpisodeWiring's
    # fakes need to be indistinguishable from what orchestrate.py itself
    # sees when it does isinstance-free duck-typed calls into them.
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


source_registry = load("source_registry")
orchestrate = load("orchestrate")


def make_item(source_key, canonical_url, title, summary="A real summary.", published_date="2026-09-01"):
    return {
        "source_key": source_key,
        "title": title,
        "url": canonical_url,
        "id_hint": None,
        "published_date": published_date,
        "summary": summary,
    }


class FakeEvidenceClient:
    """Implements exactly the method surface run_episode()/qa_gate.gate()
    actually call on a real EvidencePinningClient: register_source,
    pin_claim, verify_claim. Deliberately not a subclass of the real
    client — proving the wiring only needs the real client's *shape*,
    not its identity, is part of the point."""

    def __init__(self, flag_claims=False, fail_pin_for_titles=()):
        self._sources = {}
        self._claims = {}
        self._next_id = 0
        self.flag_claims = flag_claims
        self.fail_pin_for_titles = set(fail_pin_for_titles)

    def register_source(self, url, title, id_hint=None):
        source_id = id_hint or f"url:{url}"
        is_new = source_id not in self._sources
        self._sources[source_id] = {"url": url, "title": title}
        return {"source_id": source_id, "id_type": "url", "is_new": is_new}

    def pin_claim(self, run_id, text, source_ids, excerpt):
        if text in self.fail_pin_for_titles:
            raise orchestrate.evidence.EvidencePinningError(f"forced failure for '{text}'")
        claim_id = f"claim-{self._next_id}"
        self._next_id += 1
        self._claims[claim_id] = {"status": "flagged" if self.flag_claims else "pinned"}
        return {"claim_id": claim_id, "is_new": True, "status": self._claims[claim_id]["status"]}

    def verify_claim(self, claim_id):
        if claim_id not in self._claims:
            raise orchestrate.evidence.EvidencePinningError(f"unknown claim_id {claim_id}")
        return {"status": self._claims[claim_id]["status"]}


class FetchForSourceDispatch(unittest.TestCase):
    def test_unknown_source_with_no_feed_url_raises(self):
        with self.assertRaises(ValueError):
            orchestrate._fetch_for_source({"key": "totally_unknown"}, 10)

    def test_feed_url_source_dispatches_to_fetch_rss(self):
        with mock.patch.object(orchestrate.ingest, "fetch_rss", return_value=[]) as m:
            orchestrate._fetch_for_source({"key": "stat_news", "feed_url": "https://example.com/feed"}, 10)
        m.assert_called_once_with("https://example.com/feed", "stat_news")

    def test_pubmed_dispatches_with_its_configured_query(self):
        with mock.patch.object(orchestrate.ingest, "fetch_pubmed", return_value=[]) as m:
            orchestrate._fetch_for_source({"key": "pubmed", "query": "FHIR AND clinical decision support"}, 7)
        m.assert_called_once_with("FHIR AND clinical decision support", max_results=7)

    def test_arxiv_dispatches_with_its_configured_query(self):
        with mock.patch.object(orchestrate.ingest, "fetch_arxiv", return_value=[]) as m:
            orchestrate._fetch_for_source({"key": "arxiv", "query": "cat:cs.AI AND abs:clinical"}, 7)
        m.assert_called_once_with("cat:cs.AI AND abs:clinical", max_results=7)

    def test_regulations_gov_dispatches_with_its_configured_query_and_no_env_key(self):
        with mock.patch.object(orchestrate.ingest, "fetch_regulations_gov", return_value=[]) as m, \
             mock.patch.dict(os.environ, {}, clear=True):
            orchestrate._fetch_for_source({"key": "regulations_gov", "query": "clinical decision support software"}, 7)
        m.assert_called_once_with("clinical decision support software", max_results=7, api_key=None)

    def test_regulations_gov_passes_through_a_real_api_key_from_the_environment(self):
        with mock.patch.object(orchestrate.ingest, "fetch_regulations_gov", return_value=[]) as m, \
             mock.patch.dict(os.environ, {"REGULATIONS_GOV_API_KEY": "a-real-key"}, clear=True):
            orchestrate._fetch_for_source({"key": "regulations_gov", "query": "clinical decision support software"}, 7)
        m.assert_called_once_with("clinical decision support software", max_results=7, api_key="a-real-key")

    def test_fda_maude_dispatches_with_its_configured_query(self):
        with mock.patch.object(orchestrate.ingest, "fetch_fda_maude", return_value=[]) as m:
            orchestrate._fetch_for_source({"key": "fda_maude", "query": 'device.generic_name:(software OR algorithm)'}, 7)
        m.assert_called_once_with('device.generic_name:(software OR algorithm)', max_results=7)

    def test_medrxiv_dispatches_with_no_query(self):
        with mock.patch.object(orchestrate.ingest, "fetch_medrxiv", return_value=[]) as m:
            orchestrate._fetch_for_source({"key": "medrxiv"}, 7)
        m.assert_called_once_with(max_results=7)

    def test_fda_guidance_dispatches_with_no_query(self):
        with mock.patch.object(orchestrate.ingest, "fetch_fda_guidance", return_value=[]) as m:
            orchestrate._fetch_for_source({"key": "fda_guidance"}, 7)
        m.assert_called_once_with(max_results=7)


class IngestAllTests(unittest.TestCase):
    def test_aggregates_items_across_all_sources(self):
        registry = {"sources": [{"key": "a"}, {"key": "b"}]}
        fetch_fn = lambda source, n: [make_item(source["key"], f"https://x/{source['key']}", "T")]  # noqa: E731
        result = orchestrate.ingest_all(registry, fetch_fn=fetch_fn)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["failed"], [])

    def test_one_source_failing_does_not_abort_the_rest(self):
        registry = {"sources": [{"key": "good"}, {"key": "bad"}]}

        def fetch_fn(source, n):
            if source["key"] == "bad":
                raise RuntimeError("boom")
            return [make_item("good", "https://x/1", "T")]

        result = orchestrate.ingest_all(registry, fetch_fn=fetch_fn)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["source_key"], "bad")
        self.assertIn("boom", result["failed"][0]["error"])

    def test_all_sources_failing_returns_empty_items_not_a_raise(self):
        registry = {"sources": [{"key": "a"}, {"key": "b"}]}
        fetch_fn = lambda source, n: (_ for _ in ()).throw(RuntimeError("nope"))  # noqa: E731
        result = orchestrate.ingest_all(registry, fetch_fn=fetch_fn)
        self.assertEqual(result["items"], [])
        self.assertEqual(len(result["failed"]), 2)


class EmbedItemsTests(unittest.TestCase):
    def test_embeds_every_item_in_order(self):
        items = [make_item("a", "https://x/1", "T1"), make_item("a", "https://x/2", "T2")]
        embed_fn = lambda text, api_key: [len(text) * 1.0]  # noqa: E731
        result = orchestrate.embed_items(items, "fake-key", embed_fn=embed_fn)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(len(result["embeddings"]), 2)
        self.assertEqual(result["failed"], [])

    def test_one_item_failing_is_dropped_and_recorded_not_fatal(self):
        items = [make_item("a", "https://x/1", "Good"), make_item("a", "https://x/2", "Bad")]

        def embed_fn(text, api_key):
            if "Bad" in text:
                raise RuntimeError("embedding service down")
            return [1.0]

        result = orchestrate.embed_items(items, "fake-key", embed_fn=embed_fn)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(len(result["embeddings"]), 1)
        self.assertEqual(result["items"][0]["title"], "Good")
        self.assertEqual(len(result["failed"]), 1)
        self.assertIn("embedding service down", result["failed"][0]["error"])

    def test_items_and_embeddings_stay_aligned_after_a_drop(self):
        items = [make_item("a", f"https://x/{i}", f"T{i}") for i in range(4)]

        def embed_fn(text, api_key):
            if "T2" in text:
                raise RuntimeError("skip this one")
            return [float(text[-1])]

        result = orchestrate.embed_items(items, "fake-key", embed_fn=embed_fn)
        self.assertEqual(len(result["items"]), len(result["embeddings"]))
        for item, emb in zip(result["items"], result["embeddings"]):
            self.assertEqual(emb, [float(item["title"][-1])])


class RunEpisodeWiring(unittest.TestCase):
    """Drives run_episode() fully end to end with fakes standing in for
    every network/subprocess boundary, against the real, loaded
    config/sources.json — proving every stage's real output shape feeds
    the next stage's real expected input shape."""

    def setUp(self):
        self.registry = source_registry.load_registry(CONFIG_PATH)
        self.store = {"entries": []}

    def _fake_fetch_fn(self, source, max_results):
        # One realistic, throughline-scoped item from a throughline
        # source and one from a broad_industry-scoped source, so both
        # top_three and quick_hits get real candidates.
        if source["key"] == "pubmed":
            return [make_item("pubmed", "https://doi.org/10.1000/abc1", "FHIR interoperability advances clinical decision support")]
        if source["key"] == "stat_news":
            return [make_item("stat_news", "https://statnews.com/x", "Hospital adopts new scheduling software")]
        return []

    def _fake_embed_fn(self, text, api_key):
        # Deterministic (no PYTHONHASHSEED dependency, unlike Python's
        # built-in hash()) but genuinely non-collinear across different
        # texts — three independent bytes from an md5 digest, not one
        # varying component tacked onto fixed [0.1, 0.2] tail. That
        # earlier version made ANY two different texts' embeddings
        # cosine-similar to ~0.997+ regardless of content (a small
        # varying first component barely changes the vector's direction
        # against a much larger fixed tail), silently risking spurious
        # same_day_duplicate classification in any test that fed it two
        # genuinely different stories — exactly the kind of bug this
        # pipeline's own tests are supposed to catch, caught here by
        # ReportJson's test actually asserting on dedup/selection
        # outcomes instead of just aggregate counts.
        digest = hashlib.md5(text.encode("utf-8")).digest()
        return [float(digest[0]) - 128.0, float(digest[1]) - 128.0, float(digest[2]) - 128.0]

    def _fake_synth_fn(self, text, api_key):
        pcm = b"\x01\x02" * 100
        return orchestrate.audio_synth._pcm_to_wav(pcm)

    def _fake_narrate_fn(self, text, api_key):
        # A generic fake generate_narration() that produces a genuinely
        # grounded result for WHATEVER text it's given (unlike a single
        # canned constant, which would only ground against one specific
        # source string) — script_gen.py's real story text is
        # "{title}. {summary} Source: {name}.", so the text before the
        # first period (the title) is always a safe, real substring to
        # cite as the supporting_span.
        first_clause = text.split(".")[0]
        return {"narration": f"{first_clause}, as reported.", "supporting_spans": [first_clause]}

    def test_full_wiring_produces_a_passing_gate_and_an_episode(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
        )
        self.assertEqual(result["ingest_failed"], [])
        self.assertEqual(result["embed_failed"], [])
        self.assertGreater(len(result["rank_result"]["top_three"]) + len(result["rank_result"]["quick_hits"]), 0)
        self.assertEqual(len(result["pinned"]["failed"]), 0)
        self.assertTrue(result["qa_result"]["passed"], result["qa_result"]["checks"])
        self.assertIsNotNone(result["episode_audio"])
        self.assertEqual(len(result["episode_audio"]["segments"]), len(result["script"]["segments"]))

    def test_store_is_updated_and_returned_for_the_caller_to_persist(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
        )
        self.assertGreater(len(result["store"]["entries"]), 0)

    def test_flagged_claims_fail_the_qa_gate_and_withhold_the_episode(self):
        client = FakeEvidenceClient(flag_claims=True)
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
        )
        self.assertFalse(result["qa_result"]["passed"])
        self.assertIsNone(result["episode_audio"])

    def test_a_failing_synth_segment_withholds_the_whole_episode(self):
        client = FakeEvidenceClient()
        calls = {"n": 0}

        def flaky_synth(text, api_key):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("TTS quota exceeded")
            return self._fake_synth_fn(text, api_key)

        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=flaky_synth,
        )
        self.assertTrue(result["qa_result"]["passed"])
        self.assertIsNone(result["episode_audio"])
        self.assertEqual(len(result["synth_failed"]), 1)
        self.assertIn("TTS quota exceeded", result["synth_failed"][0]["error"])

    def test_a_source_ingest_failure_still_produces_an_episode_from_the_rest(self):
        client = FakeEvidenceClient()

        def partly_broken_fetch(source, max_results):
            if source["key"] == "arxiv":
                raise RuntimeError("arxiv down")
            return self._fake_fetch_fn(source, max_results)

        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=partly_broken_fetch, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
        )
        self.assertEqual(len(result["ingest_failed"]), 1)
        self.assertEqual(result["ingest_failed"][0]["source_key"], "arxiv")
        self.assertIsNotNone(result["episode_audio"])

    def test_default_synth_delay_is_zero_no_sleep_between_calls(self):
        # run_episode()'s own default is 0.0 — the CLI is the one that
        # opts into a nonzero delay (see DEFAULT_SYNTH_DELAY_SECONDS and
        # main()'s argparse default) — so callers that don't ask for a
        # delay, tests included, never pay for one.
        client = FakeEvidenceClient()
        with mock.patch.object(orchestrate.time, "sleep") as mock_sleep:
            orchestrate.run_episode(
                "2026-09-02", self.registry, self.store, client, "fake-api-key",
                fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
            )
        mock_sleep.assert_not_called()

    def test_nonzero_synth_delay_sleeps_between_but_not_before_the_first_segment(self):
        client = FakeEvidenceClient()
        with mock.patch.object(orchestrate.time, "sleep") as mock_sleep:
            result = orchestrate.run_episode(
                "2026-09-02", self.registry, self.store, client, "fake-api-key",
                fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
                synth_delay_seconds=3.0,
            )
        segment_count = len(result["script"]["segments"])
        self.assertEqual(mock_sleep.call_count, segment_count - 1)
        mock_sleep.assert_called_with(3.0)

    # ── narration wiring — enable_narration/narrate_fn's own effect on
    # run_episode(), on top of the full-pipeline proof above. ──────────

    def test_narration_replaces_story_text_and_is_reported(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
        )
        self.assertIsNotNone(result["narration_result"])
        self.assertGreater(result["narration_result"]["narration_attempted"], 0)
        self.assertEqual(result["narration_result"]["narration_succeeded"], result["narration_result"]["narration_attempted"])
        self.assertFalse(result["narration_result"]["episode_level_fallback"])
        story_segments = [s for s in result["script"]["segments"] if s["claim_id"] is not None]
        self.assertTrue(all(s["text"].endswith(", as reported.") for s in story_segments))
        # QA gate and episode production are unaffected by narration having run —
        # qa_gate.py never compares segment text against the pinned claim verbatim.
        self.assertTrue(result["qa_result"]["passed"], result["qa_result"]["checks"])
        self.assertIsNotNone(result["episode_audio"])

    def test_enable_narration_false_skips_narrate_fn_and_keeps_mechanical_text(self):
        client = FakeEvidenceClient()
        narrate_fn = mock.Mock(side_effect=AssertionError("narrate_fn should never be called when enable_narration=False"))
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=narrate_fn, synth_fn=self._fake_synth_fn,
            enable_narration=False,
        )
        narrate_fn.assert_not_called()
        self.assertIsNone(result["narration_result"])
        story_segments = [s for s in result["script"]["segments"] if s["claim_id"] is not None]
        self.assertTrue(all(not s["text"].endswith(", as reported.") for s in story_segments))
        self.assertTrue(result["qa_result"]["passed"], result["qa_result"]["checks"])
        self.assertIsNotNone(result["episode_audio"])

    def test_narration_failure_falls_back_gracefully_and_still_produces_an_episode(self):
        # generate_narration() failing outright (e.g. the Gemini text
        # endpoint being down) should degrade prose quality only — the
        # rest of the pipeline (grounding, QA gate, synthesis) must be
        # completely unaffected, per narrate.py's own fallback design.
        client = FakeEvidenceClient()

        def broken_narrate(text, api_key):
            raise TimeoutError("narration endpoint unreachable")

        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=broken_narrate, synth_fn=self._fake_synth_fn,
        )
        self.assertEqual(result["narration_result"]["narration_succeeded"], 0)
        self.assertTrue(result["narration_result"]["episode_level_fallback"])
        self.assertTrue(result["qa_result"]["passed"], result["qa_result"]["checks"])
        self.assertIsNotNone(result["episode_audio"])

    def test_custom_narration_success_threshold_is_passed_through(self):
        client = FakeEvidenceClient()
        calls = {"n": 0}

        def half_failing_narrate(text, api_key):
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                raise TimeoutError("simulated failure")
            return self._fake_narrate_fn(text, api_key)

        # A permissive threshold (0.0) should NOT trigger the episode-level
        # fallback even though half the narration attempts fail.
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=half_failing_narrate, synth_fn=self._fake_synth_fn,
            narration_success_threshold=0.0,
        )
        self.assertFalse(result["narration_result"]["episode_level_fallback"])

    # ── audio quality wiring — normalize_audio/inter_segment_silence_ms's
    # own effect on run_episode(), on top of the full-pipeline proof above.
    # audio_synth.py's own test suite proves the underlying math; these
    # prove run_episode() actually wires its real, default-on values
    # through to assemble_episode_audio(), the same "prove the wiring,
    # not just the units" discipline every other stage here already gets. ─

    def _full_episode_peak(self, result):
        with wave.open(io.BytesIO(result["episode_audio"]["full_episode_wav"]), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        samples = array.array("h")
        samples.frombytes(frames)
        return max(abs(s) for s in samples)

    def test_defaults_normalize_and_silence_pad_the_assembled_episode(self):
        # _fake_synth_fn's clip is a quiet, constant b"\x01\x02" fill
        # (int16 peak 513) — well below the default normalization target,
        # so a real end-to-end run through run_episode()'s own defaults
        # should visibly raise it, and the assembled WAV should be longer
        # than the raw sum of segment clips once silence gaps are counted.
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
        )
        segment_count = len(result["script"]["segments"])
        raw_clip_frames = 100  # _fake_synth_fn always returns a 100-frame clip
        gap_frames = round(24000 * orchestrate.audio_synth.DEFAULT_INTER_SEGMENT_SILENCE_MS / 1000.0)
        expected_frames = segment_count * raw_clip_frames + (segment_count - 1) * gap_frames

        with wave.open(io.BytesIO(result["episode_audio"]["full_episode_wav"]), "rb") as wf:
            self.assertEqual(wf.getnframes(), expected_frames)

        expected_peak = int(32767 * orchestrate.audio_synth.DEFAULT_NORMALIZE_TARGET_PEAK_RATIO)
        self.assertEqual(self._full_episode_peak(result), expected_peak)

    def test_disabling_both_reproduces_raw_concatenation(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
            normalize_audio=False, inter_segment_silence_ms=0.0,
        )
        segment_count = len(result["script"]["segments"])
        with wave.open(io.BytesIO(result["episode_audio"]["full_episode_wav"]), "rb") as wf:
            self.assertEqual(wf.getnframes(), segment_count * 100)  # no silence gaps
        self.assertEqual(self._full_episode_peak(result), 513)  # unnormalized — _fake_synth_fn's own real peak

    def test_custom_inter_segment_silence_ms_is_used(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
            inter_segment_silence_ms=50.0,
        )
        segment_count = len(result["script"]["segments"])
        gap_frames = round(24000 * 50.0 / 1000.0)
        expected_frames = segment_count * 100 + (segment_count - 1) * gap_frames
        with wave.open(io.BytesIO(result["episode_audio"]["full_episode_wav"]), "rb") as wf:
            self.assertEqual(wf.getnframes(), expected_frames)

    # ── dry_run — the budget-guardrail estimate mode. Fake embed_fn/
    # narrate_fn/synth_fn all raise if called, so any of these tests
    # failing to raise would itself prove dry_run leaked past ingest and
    # spent real (fake, but supposedly-unreachable) Gemini quota. ────────

    def _forbidden_fn(self, *args, **kwargs):
        raise AssertionError("dry_run must never call this — it would mean Gemini quota was spent")

    def test_dry_run_reports_exact_embed_estimate_and_capped_narration_synth_estimates(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._forbidden_fn, narrate_fn=self._forbidden_fn, synth_fn=self._forbidden_fn,
            dry_run=True,
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["items_ingested"], 2)  # _fake_fetch_fn: 1 pubmed + 1 stat_news
        self.assertEqual(result["embed_calls_estimate"], 2)  # exact, not capped — only 2 items exist
        self.assertEqual(result["narration_calls_estimate_max"], 2)
        self.assertEqual(result["synth_calls_estimate_max"], 2 + 4)

    def test_dry_run_caps_narration_and_synth_estimates_at_the_real_selection_ceiling(self):
        def many_items_fetch(source, max_results):
            if source["key"] == "pubmed":
                return [make_item("pubmed", f"https://doi.org/10.1000/x{i}", f"Story {i}") for i in range(15)]
            return []

        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=many_items_fetch, embed_fn=self._forbidden_fn, narrate_fn=self._forbidden_fn, synth_fn=self._forbidden_fn,
            dry_run=True,
        )
        ceiling = orchestrate.rank.DEFAULT_TOP_THREE_COUNT + orchestrate.rank.DEFAULT_QUICK_HITS_COUNT
        self.assertEqual(result["items_ingested"], 15)
        self.assertEqual(result["embed_calls_estimate"], 15)  # exact, uncapped — this IS how many embed calls a real run makes
        self.assertEqual(result["narration_calls_estimate_max"], ceiling)  # capped — real selection can never exceed this
        self.assertEqual(result["synth_calls_estimate_max"], ceiling + 4)

    def test_dry_run_still_reports_real_ingest_failures(self):
        def partly_broken_fetch(source, max_results):
            if source["key"] == "arxiv":
                raise RuntimeError("arxiv down")
            return self._fake_fetch_fn(source, max_results)

        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=partly_broken_fetch, embed_fn=self._forbidden_fn, narrate_fn=self._forbidden_fn, synth_fn=self._forbidden_fn,
            dry_run=True,
        )
        self.assertEqual(len(result["ingest_failed"]), 1)
        self.assertEqual(result["ingest_failed"][0]["source_key"], "arxiv")

    def test_dry_run_with_zero_ingested_items_estimates_zero_story_calls(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=lambda source, max_results: [], embed_fn=self._forbidden_fn, narrate_fn=self._forbidden_fn, synth_fn=self._forbidden_fn,
            dry_run=True,
        )
        self.assertEqual(result["items_ingested"], 0)
        self.assertEqual(result["embed_calls_estimate"], 0)
        self.assertEqual(result["narration_calls_estimate_max"], 0)
        self.assertEqual(result["synth_calls_estimate_max"], 4)  # intro/disclosure/(no transition)/outro — still a real script every time


class ReportJson(RunEpisodeWiring):
    """_report_json() itself had zero direct test coverage before this —
    only exercised indirectly by whatever a real run happened to produce.
    Reuses RunEpisodeWiring's exact setUp/fakes (one pubmed/throughline
    item, one stat_news/broad_industry item) so this is checking the
    real _report_json() output against a real run_episode() result, not
    a hand-built fake shaped to match what the function expects."""

    def test_source_utilization_reflects_the_real_run(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
        )
        report = orchestrate._report_json(result)
        util = report["source_utilization"]
        self.assertIn("pubmed", util)
        self.assertIn("stat_news", util)
        self.assertEqual(util["pubmed"]["selected_top_three"], 1)
        self.assertEqual(util["stat_news"]["selected_quick_hits"], 1)
        # A source that never appeared in this run's fake_fetch_fn output
        # (e.g. arxiv) has no entry at all — see summarize_source_utilization's
        # own docstring for why this isn't faked as a zero-candidates row.
        self.assertNotIn("arxiv", util)

    def test_source_utilization_matches_rank_results_directly(self):
        client = FakeEvidenceClient()
        result = orchestrate.run_episode(
            "2026-09-02", self.registry, self.store, client, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
        )
        report = orchestrate._report_json(result)
        self.assertEqual(report["source_utilization"], orchestrate.rank.summarize_source_utilization(result["rank_result"]))


class SynthCacheWiring(RunEpisodeWiring):
    """synth_cache_dir's real point: a retry after a partial TTS failure
    should only pay for what's still actually missing, not redo
    everything — see audio_synth.py's cache section for the live
    incident (11 of 14 segments failing to a rate limit, twice) that
    motivated this. Each test here uses a synth_fn that actively proves
    what it claims, not just an assertion after the fact — either a
    call-counting fake, or one that raises outright if called for a
    segment that should have been served from cache."""

    def test_second_run_with_same_cache_dir_makes_zero_real_synth_calls(self):
        def _forbidden_synth_fn(text, api_key):
            raise AssertionError(f"synth_fn should never be called for a cached segment: {text!r}")

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, "audio_cache")
            client1 = FakeEvidenceClient()
            result1 = orchestrate.run_episode(
                "2026-09-02", self.registry, {"entries": []}, client1, "fake-api-key",
                fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
                synth_cache_dir=cache_dir,
            )
            self.assertIsNotNone(result1["episode_audio"])

            client2 = FakeEvidenceClient()
            result2 = orchestrate.run_episode(
                "2026-09-02", self.registry, {"entries": []}, client2, "fake-api-key",
                fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=_forbidden_synth_fn,
                synth_cache_dir=cache_dir,
            )
            self.assertIsNotNone(result2["episode_audio"])
            self.assertEqual(len(result2["script"]["segments"]), len(result1["script"]["segments"]))

    def test_retry_after_partial_failure_only_synthesizes_the_missing_segment(self):
        calls_attempt1 = {"n": 0}

        def flaky_synth(text, api_key):
            calls_attempt1["n"] += 1
            if calls_attempt1["n"] == 2:
                raise RuntimeError("TTS quota exceeded")
            return self._fake_synth_fn(text, api_key)

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, "audio_cache")
            client1 = FakeEvidenceClient()
            result1 = orchestrate.run_episode(
                "2026-09-02", self.registry, {"entries": []}, client1, "fake-api-key",
                fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=flaky_synth,
                synth_cache_dir=cache_dir,
            )
            self.assertIsNone(result1["episode_audio"])
            self.assertEqual(len(result1["synth_failed"]), 1)
            total_segments = len(result1["script"]["segments"])

            calls_attempt2 = {"n": 0}

            def counting_synth(text, api_key):
                calls_attempt2["n"] += 1
                return self._fake_synth_fn(text, api_key)

            client2 = FakeEvidenceClient()
            result2 = orchestrate.run_episode(
                "2026-09-02", self.registry, {"entries": []}, client2, "fake-api-key",
                fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=counting_synth,
                synth_cache_dir=cache_dir,
            )
            self.assertIsNotNone(result2["episode_audio"])
            # Only the one segment that failed last attempt needed a real
            # call this time — the whole point of this feature.
            self.assertEqual(calls_attempt2["n"], 1)
            self.assertLess(calls_attempt2["n"], total_segments)

    def test_pacing_sleep_is_skipped_entirely_on_a_fully_cached_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, "audio_cache")
            client1 = FakeEvidenceClient()
            orchestrate.run_episode(
                "2026-09-02", self.registry, {"entries": []}, client1, "fake-api-key",
                fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
                synth_cache_dir=cache_dir,
            )

            client2 = FakeEvidenceClient()
            with mock.patch.object(orchestrate.time, "sleep") as fake_sleep:
                orchestrate.run_episode(
                    "2026-09-02", self.registry, {"entries": []}, client2, "fake-api-key",
                    fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=self._fake_synth_fn,
                    synth_delay_seconds=6.0, synth_cache_dir=cache_dir,
                )
            fake_sleep.assert_not_called()

    def test_without_a_cache_dir_behavior_is_unchanged_synth_fn_always_called(self):
        # Regression guard: synth_cache_dir defaults to None, and passing
        # it explicitly as None must behave exactly like every
        # pre-existing test that never mentions this parameter at all.
        calls = {"n": 0}

        def counting_synth(text, api_key):
            calls["n"] += 1
            return self._fake_synth_fn(text, api_key)

        client1 = FakeEvidenceClient()
        result1 = orchestrate.run_episode(
            "2026-09-02", self.registry, {"entries": []}, client1, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=counting_synth,
            synth_cache_dir=None,
        )
        first_run_calls = calls["n"]
        self.assertGreater(first_run_calls, 0)

        client2 = FakeEvidenceClient()
        orchestrate.run_episode(
            "2026-09-02", self.registry, {"entries": []}, client2, "fake-api-key",
            fetch_fn=self._fake_fetch_fn, embed_fn=self._fake_embed_fn, narrate_fn=self._fake_narrate_fn, synth_fn=counting_synth,
            synth_cache_dir=None,
        )
        # No caching at all -> the second run repeats every real call the
        # first one made, none skipped.
        self.assertEqual(calls["n"], first_run_calls * 2)


class MainApiKeyGate(unittest.TestCase):
    """main()'s own GEMINI_API_KEY gate — the CLI wiring layer, distinct
    from run_episode()'s dry_run branch (already proven above, via the
    ForbiddenFn tests, to never touch api_key at all). This class exists
    because that distinction is exactly where the bug was: main() used to
    require GEMINI_API_KEY unconditionally, before ever checking
    args.dry_run, even though dry_run's whole point is answering a cost
    question for someone who may not have a key yet. run_episode() is
    patched out so this never spawns a real subprocess or hits the
    network — main() otherwise always uses the real fetch_fn default,
    unlike RunEpisodeWiring's tests above."""

    def _run_main(self, argv, gemini_api_key):
        env = {} if gemini_api_key is None else {"GEMINI_API_KEY": gemini_api_key}
        fake_result = {
            "run_date": "2026-09-02", "dry_run": True, "ingest_failed": [],
            "items_ingested": 0, "embed_calls_estimate": 0,
            "narration_calls_estimate_max": 0, "synth_calls_estimate_max": 4,
        }
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", ["orchestrate.py"] + argv), \
             mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(orchestrate, "run_episode", return_value=fake_result) as fake_run_episode, \
             mock.patch.object(sys, "stderr", stderr), \
             mock.patch.object(sys, "stdout", io.StringIO()):
            exit_code = orchestrate.main()
        return exit_code, stderr.getvalue(), fake_run_episode

    def test_dry_run_proceeds_without_gemini_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            exit_code, stderr, fake_run_episode = self._run_main(["--data-dir", tmp, "--dry-run"], gemini_api_key=None)
        self.assertEqual(exit_code, 0)
        self.assertNotIn("GEMINI_API_KEY", stderr)
        fake_run_episode.assert_called_once()
        self.assertTrue(fake_run_episode.call_args.kwargs["dry_run"])

    def test_real_run_still_requires_gemini_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            exit_code, stderr, fake_run_episode = self._run_main(["--data-dir", tmp], gemini_api_key=None)
        self.assertEqual(exit_code, 2)
        self.assertIn("GEMINI_API_KEY", stderr)
        fake_run_episode.assert_not_called()

    def test_dry_run_also_works_fine_when_a_key_is_actually_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            exit_code, _, fake_run_episode = self._run_main(["--data-dir", tmp, "--dry-run"], gemini_api_key="fake-api-key")
        self.assertEqual(exit_code, 0)
        fake_run_episode.assert_called_once()


if __name__ == "__main__":
    unittest.main()
