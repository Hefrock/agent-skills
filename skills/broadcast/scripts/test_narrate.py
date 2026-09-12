#!/usr/bin/env python3
"""Tests for narrate.py's pure logic (check_narration_grounded and its
helpers) — no network. generate_narration() itself is deliberately NOT
unit tested here, same treatment every other network-touching function
in this pipeline gets (dedup_store.embed_text, audio_synth.synthesize_text):
it's verified live via live_smoke_test.py instead.

narrate_script()'s orchestration (episode-level success threshold,
per-segment fallback) is tested with a fake narrate_fn, same pattern
orchestrate.py's test_orchestrate.py already established for injecting
fakes at network boundaries.

Run: python test_narrate.py"""

import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


narrate = load("narrate")


class HasHedgeMarker(unittest.TestCase):
    def test_detects_a_hedge_word(self):
        self.assertTrue(narrate._has_hedge_marker("This may cause issues."))

    def test_detects_attribution_language(self):
        self.assertTrue(narrate._has_hedge_marker("According to the agency, this is preliminary."))

    def test_case_insensitive(self):
        self.assertTrue(narrate._has_hedge_marker("This MAY cause issues."))

    def test_no_hedge_marker_present(self):
        self.assertFalse(narrate._has_hedge_marker("The agency approved the new rule today."))


class SpanIsGrounded(unittest.TestCase):
    def test_exact_substring_is_grounded(self):
        self.assertTrue(narrate._span_is_grounded("the new rule", "The agency approved the new rule today."))

    def test_case_and_whitespace_normalized(self):
        self.assertTrue(narrate._span_is_grounded("THE   NEW rule", "The agency approved the new rule today."))

    def test_span_not_present_is_not_grounded(self):
        self.assertFalse(narrate._span_is_grounded("a total ban", "The agency approved the new rule today."))


class CheckNarrationGrounded(unittest.TestCase):
    SOURCE = "The FDA approved a new diagnostic tool today. Source: FDA guidance."

    def test_well_grounded_narration_passes(self):
        result = narrate.check_narration_grounded(
            "The FDA gave the green light to a new diagnostic tool.",
            ["The FDA approved a new diagnostic tool"],
            self.SOURCE,
        )
        self.assertTrue(result["passed"], result["reasons"])
        self.assertEqual(result["reasons"], [])

    def test_empty_narration_fails(self):
        result = narrate.check_narration_grounded("", ["The FDA approved"], self.SOURCE)
        self.assertFalse(result["passed"])
        self.assertIn("narration is empty", result["reasons"])

    def test_no_supporting_spans_fails(self):
        result = narrate.check_narration_grounded("The FDA approved a new tool.", [], self.SOURCE)
        self.assertFalse(result["passed"])
        self.assertIn("no supporting_spans provided", result["reasons"])

    def test_a_fabricated_span_not_in_source_fails(self):
        result = narrate.check_narration_grounded(
            "The FDA approved a new tool and banned all competitors.",
            ["banned all competitors"],
            self.SOURCE,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(any("not found verbatim" in r for r in result["reasons"]))

    def test_one_ungrounded_span_among_grounded_ones_still_fails(self):
        result = narrate.check_narration_grounded(
            "narration text",
            ["The FDA approved a new diagnostic tool", "an invented phrase not in the source"],
            self.SOURCE,
        )
        self.assertFalse(result["passed"])

    def test_dropped_hedge_language_fails(self):
        source = "The agency may expand this rule to other devices."
        result = narrate.check_narration_grounded(
            "The agency will expand this rule to other devices.",
            ["expand this rule to other devices"],
            source,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(any("hedging" in r for r in result["reasons"]))

    def test_preserved_hedge_language_passes(self):
        source = "The agency may expand this rule to other devices."
        result = narrate.check_narration_grounded(
            "The agency could expand this rule to cover other devices too.",
            ["expand this rule to other devices"],
            source,
        )
        self.assertTrue(result["passed"], result["reasons"])

    def test_source_with_no_hedge_language_does_not_require_narration_to_have_any(self):
        result = narrate.check_narration_grounded(
            "The FDA gave the green light to a new diagnostic tool.",
            ["The FDA approved a new diagnostic tool"],
            self.SOURCE,
        )
        self.assertNotIn("source contains hedging/attribution language the narration dropped", result["reasons"])

    def test_wildly_expanded_narration_fails_length_ratio(self):
        long_narration = "The FDA approved a new diagnostic tool. " * 20
        result = narrate.check_narration_grounded(long_narration, [self.SOURCE[:20]], self.SOURCE)
        self.assertFalse(result["passed"])
        self.assertTrue(any("length ratio" in r for r in result["reasons"]))

    def test_wildly_truncated_narration_fails_length_ratio(self):
        result = narrate.check_narration_grounded("FDA.", ["FDA"], self.SOURCE)
        self.assertFalse(result["passed"])
        self.assertTrue(any("length ratio" in r for r in result["reasons"]))

    def test_reports_every_failing_check_not_just_the_first(self):
        result = narrate.check_narration_grounded("", [], self.SOURCE)
        self.assertFalse(result["passed"])
        self.assertGreaterEqual(len(result["reasons"]), 2)

    def test_custom_length_ratio_bounds_are_respected(self):
        narration = "The FDA approved a new diagnostic tool today for real."
        spans = ["The FDA approved a new diagnostic tool"]
        strict = narrate.check_narration_grounded(narration, spans, self.SOURCE, length_ratio_min=0.99, length_ratio_max=1.0)
        self.assertFalse(strict["passed"])
        lenient = narrate.check_narration_grounded(narration, spans, self.SOURCE, length_ratio_min=0.1, length_ratio_max=10.0)
        self.assertTrue(lenient["passed"], lenient["reasons"])


# ── narrate_segment / narrate_script — fake narrate_fn injection, no
# network, same pattern test_orchestrate.py's RunEpisodeWiring already
# established for this pipeline's network-boundary functions. ──────────

def make_story_segment(canonical_id, text="A device got a new clearance. Source: FDA guidance."):
    return {"segment_type": "top_three_item", "text": text, "canonical_id": canonical_id, "claim_id": f"claim-{canonical_id}", "source_id": f"source-{canonical_id}"}


def make_connective_segment(segment_type, text):
    return {"segment_type": segment_type, "text": text, "canonical_id": None, "claim_id": None, "source_id": None}


class FakeNarrateFn:
    """Fake narrate_fn, keyed by exact source text — each entry in
    behaviors is either a {"narration", "supporting_spans"} dict to
    return, or an Exception instance to raise. Records every text it was
    called with, so tests can assert per-story isolation (each call sees
    exactly one segment's own text, never another's)."""

    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.calls = []

    def __call__(self, text, api_key):
        self.calls.append(text)
        behavior = self.behaviors[text]
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


GROUNDED_RESULT = {"narration": "A device just got a new clearance, per FDA guidance.", "supporting_spans": ["A device got a new clearance"]}
UNGROUNDED_RESULT = {"narration": "A device got a new clearance and also cured cancer.", "supporting_spans": ["cured cancer"]}


def grounded_result_for(source_text):
    """Builds a {"narration", "supporting_spans"} result that actually
    passes check_narration_grounded() against the GIVEN source_text —
    needed whenever a test uses several segments with different source
    text, since a single fixed GROUNDED_RESULT (crafted for one specific
    source string) would fail the span-grounding check against any other
    segment's different text."""
    first_clause = source_text.split(".")[0]
    return {"narration": f"{first_clause}, reported today.", "supporting_spans": [first_clause]}


class NarrateSegment(unittest.TestCase):
    def test_successful_grounded_narration_replaces_text(self):
        segment = make_story_segment("a")
        narrate_fn = FakeNarrateFn({segment["text"]: GROUNDED_RESULT})
        result = narrate.narrate_segment(segment, "fake-key", narrate_fn)
        self.assertTrue(result["narrated"], result["reasons"])
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["segment"]["text"], GROUNDED_RESULT["narration"])
        # every other field is untouched
        self.assertEqual(result["segment"]["canonical_id"], "a")
        self.assertEqual(result["segment"]["claim_id"], "claim-a")
        self.assertEqual(result["segment"]["source_id"], "source-a")
        self.assertEqual(result["segment"]["segment_type"], "top_three_item")

    def test_ungrounded_narration_falls_back_to_original_text(self):
        segment = make_story_segment("a")
        narrate_fn = FakeNarrateFn({segment["text"]: UNGROUNDED_RESULT})
        result = narrate.narrate_segment(segment, "fake-key", narrate_fn)
        self.assertFalse(result["narrated"])
        self.assertTrue(result["reasons"])
        self.assertEqual(result["segment"], segment)

    def test_narrate_fn_exception_falls_back_to_original_text(self):
        segment = make_story_segment("a")
        narrate_fn = FakeNarrateFn({segment["text"]: TimeoutError("simulated network failure")})
        result = narrate.narrate_segment(segment, "fake-key", narrate_fn)
        self.assertFalse(result["narrated"])
        self.assertIn("simulated network failure", result["reasons"][0])
        self.assertEqual(result["segment"], segment)

    def test_defaults_to_real_generate_narration_when_no_fn_given(self):
        self.assertIs(narrate.narrate_segment.__defaults__[0], None)


class NarrateScript(unittest.TestCase):
    def _script(self, segments):
        return {"run_date": "2026-09-02", "show_name": "Test Show", "segments": segments, "excluded_no_evidence": []}

    def test_all_stories_narrated_successfully(self):
        seg_a, seg_b = make_story_segment("a"), make_story_segment("b", text="Another story entirely. Source: CMS.")
        script = self._script([make_connective_segment("intro", "Welcome."), seg_a, seg_b, make_connective_segment("outro", "Bye.")])
        narrate_fn = FakeNarrateFn({
            seg_a["text"]: GROUNDED_RESULT,
            seg_b["text"]: {"narration": "Another story, straight from CMS.", "supporting_spans": ["Another story entirely"]},
        })
        result = narrate.narrate_script(script, "fake-key", narrate_fn)
        self.assertEqual(result["narration_attempted"], 2)
        self.assertEqual(result["narration_succeeded"], 2)
        self.assertEqual(result["narration_success_rate"], 1.0)
        self.assertFalse(result["episode_level_fallback"])
        self.assertEqual(result["narration_failures"], [])
        self.assertEqual(result["script"]["segments"][1]["text"], GROUNDED_RESULT["narration"])
        self.assertEqual(result["script"]["segments"][0]["text"], "Welcome.")  # connective segment never touched
        self.assertEqual(result["script"]["segments"][3]["text"], "Bye.")

    def test_connective_segments_are_never_sent_to_narrate_fn(self):
        seg_a = make_story_segment("a")
        script = self._script([make_connective_segment("intro", "Welcome."), seg_a, make_connective_segment("disclosure", "This is AI.")])
        narrate_fn = FakeNarrateFn({seg_a["text"]: GROUNDED_RESULT})
        narrate.narrate_script(script, "fake-key", narrate_fn)
        self.assertEqual(narrate_fn.calls, [seg_a["text"]])

    def test_per_segment_fallback_above_threshold_keeps_successful_narrations(self):
        # 3 of 4 stories succeed (75%) — above the default 70% threshold,
        # so the episode is NOT reverted; only the one failing story
        # falls back individually.
        segs = [make_story_segment(str(i), text=f"Story number {i}. Source: Test.") for i in range(4)]
        behaviors = {s["text"]: grounded_result_for(s["text"]) for s in segs}
        behaviors[segs[3]["text"]] = UNGROUNDED_RESULT
        script = self._script(segs)
        result = narrate.narrate_script(script, "fake-key", FakeNarrateFn(behaviors))
        self.assertEqual(result["narration_attempted"], 4)
        self.assertEqual(result["narration_succeeded"], 3)
        self.assertAlmostEqual(result["narration_success_rate"], 0.75)
        self.assertFalse(result["episode_level_fallback"])
        for i in range(3):
            self.assertEqual(result["script"]["segments"][i]["text"], grounded_result_for(segs[i]["text"])["narration"])
        self.assertEqual(result["script"]["segments"][3]["text"], segs[3]["text"])  # the one failure, individually fell back
        self.assertEqual(len(result["narration_failures"]), 1)
        self.assertEqual(result["narration_failures"][0]["canonical_id"], "3")

    def test_episode_level_fallback_below_threshold_reverts_every_segment(self):
        # Only 1 of 4 stories succeeds (25%) — below the default 70%
        # threshold, so even the one story that DID succeed is reverted.
        segs = [make_story_segment(str(i), text=f"Story number {i}. Source: Test.") for i in range(4)]
        behaviors = {s["text"]: UNGROUNDED_RESULT for s in segs}
        behaviors[segs[0]["text"]] = grounded_result_for(segs[0]["text"])
        script = self._script(segs)
        result = narrate.narrate_script(script, "fake-key", FakeNarrateFn(behaviors))
        self.assertEqual(result["narration_attempted"], 4)
        self.assertEqual(result["narration_succeeded"], 1)
        self.assertTrue(result["episode_level_fallback"])
        for i in range(4):
            self.assertEqual(result["script"]["segments"][i]["text"], segs[i]["text"])

    def test_zero_story_segments_never_triggers_episode_level_fallback(self):
        script = self._script([make_connective_segment("intro", "Welcome."), make_connective_segment("outro", "Bye.")])
        result = narrate.narrate_script(script, "fake-key", FakeNarrateFn({}))
        self.assertEqual(result["narration_attempted"], 0)
        self.assertEqual(result["narration_success_rate"], 1.0)
        self.assertFalse(result["episode_level_fallback"])
        self.assertEqual(result["script"]["segments"][0]["text"], "Welcome.")

    def test_custom_success_threshold_is_respected(self):
        # Same 75% success rate as the per-segment-fallback test above,
        # but with a stricter 80% threshold this time it SHOULD trigger
        # the episode-level fallback.
        segs = [make_story_segment(str(i), text=f"Story number {i}. Source: Test.") for i in range(4)]
        behaviors = {s["text"]: grounded_result_for(s["text"]) for s in segs}
        behaviors[segs[3]["text"]] = UNGROUNDED_RESULT
        script = self._script(segs)
        result = narrate.narrate_script(script, "fake-key", FakeNarrateFn(behaviors), success_threshold=0.8)
        self.assertTrue(result["episode_level_fallback"])

    def test_non_segment_script_keys_are_preserved(self):
        script = self._script([make_connective_segment("intro", "Welcome.")])
        result = narrate.narrate_script(script, "fake-key", FakeNarrateFn({}))
        self.assertEqual(result["script"]["run_date"], "2026-09-02")
        self.assertEqual(result["script"]["show_name"], "Test Show")
        self.assertEqual(result["script"]["excluded_no_evidence"], [])


if __name__ == "__main__":
    unittest.main()
