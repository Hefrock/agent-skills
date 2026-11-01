#!/usr/bin/env python3
"""Tests for law10_overlap.py.

Run with: python skills/wiki-librarian/scripts/test_law10_overlap.py

The controls mirror the 2026-10-31 trial on real warehoused notes: pasted
source text must fail even when it is short (the case a character cap
misses), and a long note that copies nothing must pass (the case a character
cap wrongly flags).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from law10_overlap import check_note, classify, load_source_text, measure, split_quotes  # noqa: E402

FIXTURE_WAREHOUSE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "warehouse")


def prose(prefix, n):
    """n distinct-ish words that share no 8-word run with prose() of another prefix."""
    return " ".join(f"{prefix}{i}" for i in range(n))


SOURCE = prose("src", 12000)  # ~100k chars, a realistic paper, so the large-source share rule is not what is tested


class OverlapTests(unittest.TestCase):
    def test_short_paste_fails(self):
        body = " ".join(f"src{i}" for i in range(400))  # ~2.5k chars, well under 4000
        self.assertLess(len(body), 4000)
        result = check_note(body, SOURCE)
        self.assertEqual(result["status"], "fail")
        self.assertGreater(result["copied_outside_quotes_pct"], 90)

    def test_paste_padded_with_commentary_still_fails(self):
        body = "My notes on this paper.\n\n" + " ".join(f"src{i}" for i in range(400)) + "\n\nWorth a follow-up."
        self.assertEqual(check_note(body, SOURCE)["status"], "fail")

    def test_long_original_passes(self):
        body = prose("mine", 1500)  # ~10k chars, no shared 8-word run
        self.assertGreater(len(body), 8000)
        result = check_note(body, SOURCE)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["copied_outside_quotes_pct"], 0.0)

    def test_blockquote_is_a_budget_not_copied_wording(self):
        quoted = "> " + " ".join(f"src{i}" for i in range(30))
        body = prose("mine", 200) + "\n" + quoted
        m = measure(body, SOURCE)
        self.assertEqual(m["copied_outside_quotes_pct"], 0.0)
        self.assertGreater(m["quote_chars"], 0)
        self.assertGreaterEqual(m["longest_run_words"], 30)  # runs still count quotes

    def test_inline_quote_span_is_excluded_from_copied_pct(self):
        span = " ".join(f"src{i}" for i in range(12))
        outside, quote_chars = split_quotes(f'Intro words here. "{span}" Outro words here.')
        self.assertNotIn("src3", outside)
        self.assertGreater(quote_chars, 20)

    def test_long_verbatim_run_fails_even_inside_quotes(self):
        body = prose("mine", 400) + "\n> " + " ".join(f"src{i}" for i in range(130))
        result = check_note(body, SOURCE)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("verbatim run" in r for r in result["reasons"]))

    def test_no_source_text_is_unchecked_never_pass(self):
        result = check_note("short note", None)
        self.assertEqual(result["status"], "unchecked")
        self.assertNotEqual(result["status"], "pass")

    def test_classify_boundaries(self):
        base = {"body_chars": 100, "source_chars": 1000, "outside_words": 100, "quote_chars": 0}
        self.assertEqual(classify({**base, "copied_outside_quotes_pct": 20.0, "longest_run_words": 0})["status"], "pass")
        self.assertEqual(classify({**base, "copied_outside_quotes_pct": 20.1, "longest_run_words": 0})["status"], "warn")
        self.assertEqual(classify({**base, "copied_outside_quotes_pct": 35.1, "longest_run_words": 0})["status"], "fail")
        self.assertEqual(classify({**base, "copied_outside_quotes_pct": 0, "longest_run_words": 80})["status"], "warn")
        self.assertEqual(classify({**base, "copied_outside_quotes_pct": 0, "longest_run_words": 120})["status"], "fail")


class ManifestLookupTests(unittest.TestCase):
    def test_prefix_doc_id_resolves(self):
        text = load_source_text(FIXTURE_WAREHOUSE, "sha256:bbbb2222")
        self.assertIn("simulates a full paragraph", text)

    def test_unknown_doc_id_is_none(self):
        self.assertIsNone(load_source_text(FIXTURE_WAREHOUSE, "sha256:ffff9999"))

    def test_missing_warehouse_is_none(self):
        self.assertIsNone(load_source_text("/nonexistent/warehouse", "sha256:bbbb2222"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
