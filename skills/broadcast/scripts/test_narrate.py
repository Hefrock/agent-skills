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


if __name__ == "__main__":
    unittest.main()
