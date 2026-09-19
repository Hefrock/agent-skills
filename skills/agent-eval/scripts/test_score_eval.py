#!/usr/bin/env python3
"""
Unit tests for score_eval.py — an eval tool should itself be tested.

Covers loading (coercion, skipping malformed/incomplete lines), aggregation
(pass rate, per-category stats, cost/latency, threshold boundary), regression
detection, and the CI gate (--fail-under / --fail-on-regression exit codes).

Stdlib only (unittest). Run: python test_score_eval.py
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "score_eval.py")

spec = importlib.util.spec_from_file_location("score_eval", SCRIPT)
score_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score_eval)


def write_jsonl(rows):
    """Write rows (dicts or raw strings) to a temp .jsonl, return its path."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return path


class LoadResults(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def load(self, rows):
        path = write_jsonl(rows)
        self._paths.append(path)
        with contextlib.redirect_stderr(io.StringIO()):  # silence skip warnings
            return score_eval.load_results(path)

    def test_valid_lines_parse(self):
        r = self.load([{"id": "a", "score": 1.0}, {"id": "b", "score": 0.0}])
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["id"], "a")

    def test_blank_lines_skipped(self):
        r = self.load([{"id": "a", "score": 1.0}, "", "   ", {"id": "b", "score": 0.5}])
        self.assertEqual(len(r), 2)

    def test_malformed_json_skipped(self):
        r = self.load([{"id": "a", "score": 1.0}, "{not valid json", {"id": "b", "score": 0.5}])
        self.assertEqual(len(r), 2)
        self.assertEqual([x["id"] for x in r], ["a", "b"])

    def test_missing_id_skipped(self):
        r = self.load([{"score": 1.0}, {"id": "b", "score": 0.5}])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], "b")

    def test_missing_score_skipped(self):
        r = self.load([{"id": "a"}, {"id": "b", "score": 0.5}])
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], "b")

    def test_bool_score_coerced(self):
        r = self.load([{"id": "a", "score": True}, {"id": "b", "score": False}])
        self.assertEqual(r[0]["score"], 1.0)
        self.assertEqual(r[1]["score"], 0.0)
        self.assertIsInstance(r[0]["score"], float)

    def test_int_score_coerced_to_float(self):
        r = self.load([{"id": "a", "score": 1}])
        self.assertIsInstance(r[0]["score"], float)
        self.assertEqual(r[0]["score"], 1.0)


class NormalizeCategory(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(score_eval.normalize_category("Accuracy"), "accuracy")

    def test_strips_whitespace(self):
        self.assertEqual(score_eval.normalize_category("  accuracy  "), "accuracy")

    def test_combined_case_and_whitespace(self):
        self.assertEqual(score_eval.normalize_category(" ACCURACY "), "accuracy")

    def test_already_normalized_unchanged(self):
        self.assertEqual(score_eval.normalize_category("accuracy"), "accuracy")

    def test_non_string_coerced(self):
        self.assertEqual(score_eval.normalize_category(1), "1")


class FindLikelyTypoCategories(unittest.TestCase):
    def test_identical_categories_not_flagged(self):
        self.assertEqual(score_eval.find_likely_typo_categories(["accuracy", "accuracy"]), [])

    def test_clearly_different_categories_not_flagged(self):
        self.assertEqual(score_eval.find_likely_typo_categories(["accuracy", "format"]), [])

    def test_near_duplicate_flagged(self):
        pairs = score_eval.find_likely_typo_categories(["accuracy", "accuraccy"])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(set(pairs[0]), {"accuracy", "accuraccy"})

    def test_no_categories_returns_empty(self):
        self.assertEqual(score_eval.find_likely_typo_categories([]), [])

    def test_below_threshold_not_flagged(self):
        pairs = score_eval.find_likely_typo_categories(["accuracy", "format"], similarity_threshold=0.82)
        self.assertEqual(pairs, [])

    def test_custom_threshold_widens_what_is_flagged(self):
        pairs = score_eval.find_likely_typo_categories(["accuracy", "adequacy"], similarity_threshold=0.5)
        self.assertEqual(pairs, [("accuracy", "adequacy")])


class Summarize(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(score_eval.summarize([], 0.7))

    def test_basic_counts_and_rates(self):
        results = [
            {"id": "a", "score": 1.0, "category": "x"},
            {"id": "b", "score": 0.0, "category": "x"},
            {"id": "c", "score": 0.8, "category": "y"},
            {"id": "d", "score": 0.6, "category": "y"},
        ]
        s = score_eval.summarize(results, 0.7)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["pass_count"], 2)          # 1.0 and 0.8
        self.assertAlmostEqual(s["pass_rate"], 0.5)
        self.assertAlmostEqual(s["mean_score"], (1.0 + 0.0 + 0.8 + 0.6) / 4)

    def test_threshold_boundary_is_inclusive(self):
        # score exactly == threshold must count as a pass (>=)
        s = score_eval.summarize([{"id": "a", "score": 0.7}], 0.7)
        self.assertEqual(s["pass_count"], 1)

    def test_per_category_breakdown(self):
        results = [
            {"id": "a", "score": 1.0, "category": "x"},
            {"id": "b", "score": 0.0, "category": "x"},
            {"id": "c", "score": 1.0, "category": "y"},
        ]
        s = score_eval.summarize(results, 0.7)
        self.assertEqual(s["by_category"]["x"]["count"], 2)
        self.assertAlmostEqual(s["by_category"]["x"]["pass_rate"], 0.5)
        self.assertAlmostEqual(s["by_category"]["y"]["mean_score"], 1.0)

    def test_missing_category_is_uncategorized(self):
        s = score_eval.summarize([{"id": "a", "score": 1.0}], 0.7)
        self.assertIn("uncategorized", s["by_category"])

    def test_cost_latency_absent_when_no_fields(self):
        s = score_eval.summarize([{"id": "a", "score": 1.0}], 0.7)
        self.assertNotIn("mean_cost_usd", s)
        self.assertNotIn("mean_latency_ms", s)

    def test_cost_latency_present_and_averaged(self):
        results = [
            {"id": "a", "score": 1.0, "cost_usd": 0.002, "latency_ms": 800},
            {"id": "b", "score": 0.0, "cost_usd": 0.004, "latency_ms": 1200},
        ]
        s = score_eval.summarize(results, 0.7)
        self.assertAlmostEqual(s["mean_cost_usd"], 0.003)
        self.assertAlmostEqual(s["mean_latency_ms"], 1000)

    def test_partial_cost_averages_only_present(self):
        # Only some rows carry cost — mean is over present values, not all rows.
        results = [
            {"id": "a", "score": 1.0, "cost_usd": 0.002},
            {"id": "b", "score": 1.0},
        ]
        s = score_eval.summarize(results, 0.7)
        self.assertAlmostEqual(s["mean_cost_usd"], 0.002)

    def test_category_case_and_whitespace_variants_merge_into_one_bucket(self):
        results = [
            {"id": "a", "score": 1.0, "category": "Accuracy"},
            {"id": "b", "score": 0.0, "category": "accuracy"},
            {"id": "c", "score": 1.0, "category": " ACCURACY "},
        ]
        s = score_eval.summarize(results, 0.7)
        self.assertEqual(len(s["by_category"]), 1)
        cat = next(iter(s["by_category"]))
        self.assertEqual(s["by_category"][cat]["count"], 3)

    def test_category_display_label_is_first_seen_spelling(self):
        results = [
            {"id": "a", "score": 1.0, "category": "Accuracy"},
            {"id": "b", "score": 0.0, "category": "accuracy"},
        ]
        s = score_eval.summarize(results, 0.7)
        self.assertIn("Accuracy", s["by_category"])
        self.assertNotIn("accuracy", s["by_category"])

    def test_similar_but_distinct_categories_stay_separate(self):
        # normalize_category only handles case/whitespace — a real
        # spelling difference (typo or not) must never be auto-merged.
        results = [
            {"id": "a", "score": 1.0, "category": "accuracy"},
            {"id": "b", "score": 1.0, "category": "accuraccy"},
        ]
        s = score_eval.summarize(results, 0.7)
        self.assertEqual(len(s["by_category"]), 2)


class LowestScoring(unittest.TestCase):
    def test_returns_n_lowest_ascending(self):
        results = [
            {"id": "a", "score": 0.9},
            {"id": "b", "score": 0.1},
            {"id": "c", "score": 0.5},
        ]
        low = score_eval.lowest_scoring(results, n=2)
        self.assertEqual([r["id"] for r in low], ["b", "c"])

    def test_n_larger_than_list_returns_all(self):
        results = [{"id": "a", "score": 0.5}]
        self.assertEqual(len(score_eval.lowest_scoring(results, n=5)), 1)


class FindRegressions(unittest.TestCase):
    def test_pass_to_fail_flagged(self):
        base = [{"id": "a", "score": 1.0}]
        cur = [{"id": "a", "score": 0.0}]
        regs = score_eval.find_regressions(cur, base, 0.7)
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["id"], "a")
        self.assertEqual(regs[0]["kind"], "threshold_crossing")

    def test_fail_to_fail_not_flagged(self):
        base = [{"id": "a", "score": 0.2}]
        cur = [{"id": "a", "score": 0.1}]
        self.assertEqual(score_eval.find_regressions(cur, base, 0.7), [])

    def test_pass_to_pass_not_flagged(self):
        base = [{"id": "a", "score": 1.0}]
        cur = [{"id": "a", "score": 0.8}]
        self.assertEqual(score_eval.find_regressions(cur, base, 0.7), [])

    def test_new_id_absent_from_baseline_ignored(self):
        base = [{"id": "a", "score": 1.0}]
        cur = [{"id": "b", "score": 0.0}]
        self.assertEqual(score_eval.find_regressions(cur, base, 0.7), [])

    def test_magnitude_drop_ignored_without_min_drop(self):
        # 1.00 -> 0.71 against threshold 0.7: still passing, real quality
        # decay, but min_drop isn't set (the old, default behavior) so
        # this must NOT be flagged.
        base = [{"id": "a", "score": 1.0}]
        cur = [{"id": "a", "score": 0.71}]
        self.assertEqual(score_eval.find_regressions(cur, base, 0.7), [])

    def test_magnitude_drop_flagged_when_exceeding_min_drop(self):
        base = [{"id": "a", "score": 1.0}]
        cur = [{"id": "a", "score": 0.71}]  # drop of 0.29, still passing
        regs = score_eval.find_regressions(cur, base, 0.7, min_drop=0.2)
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["kind"], "magnitude_drop")
        self.assertEqual(regs[0]["baseline_score"], 1.0)
        self.assertEqual(regs[0]["current_score"], 0.71)

    def test_magnitude_drop_not_flagged_when_within_min_drop(self):
        base = [{"id": "a", "score": 1.0}]
        cur = [{"id": "a", "score": 0.85}]  # drop of 0.15, under the 0.2 bar
        self.assertEqual(score_eval.find_regressions(cur, base, 0.7, min_drop=0.2), [])

    def test_threshold_crossing_takes_priority_over_magnitude_drop(self):
        # A case that both crosses the threshold AND exceeds min_drop must
        # be reported once, as threshold_crossing (the more severe kind) —
        # not double-counted under both kinds.
        base = [{"id": "a", "score": 1.0}]
        cur = [{"id": "a", "score": 0.3}]  # crosses AND drops > 0.2
        regs = score_eval.find_regressions(cur, base, 0.7, min_drop=0.2)
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]["kind"], "threshold_crossing")

    def test_score_improvement_never_flagged_even_with_min_drop_set(self):
        base = [{"id": "a", "score": 0.5}]
        cur = [{"id": "a", "score": 0.9}]
        self.assertEqual(score_eval.find_regressions(cur, base, 0.7, min_drop=0.2), [])


class MatchedScores(unittest.TestCase):
    def test_matches_by_id_not_position(self):
        cur = [{"id": "b", "score": 0.5}, {"id": "a", "score": 1.0}]
        base = [{"id": "a", "score": 0.9}, {"id": "b", "score": 0.4}]
        current, baseline = score_eval.matched_scores(cur, base)
        # Order follows `results` (cur): b then a.
        self.assertEqual(current, [0.5, 1.0])
        self.assertEqual(baseline, [0.4, 0.9])

    def test_only_shared_ids_included(self):
        cur = [{"id": "a", "score": 1.0}, {"id": "new", "score": 0.5}]
        base = [{"id": "a", "score": 0.9}, {"id": "gone", "score": 0.2}]
        current, baseline = score_eval.matched_scores(cur, base)
        self.assertEqual(current, [1.0])
        self.assertEqual(baseline, [0.9])

    def test_no_overlap_returns_empty(self):
        cur = [{"id": "a", "score": 1.0}]
        base = [{"id": "b", "score": 1.0}]
        self.assertEqual(score_eval.matched_scores(cur, base), ([], []))


class ComputeConfidence(unittest.TestCase):
    def test_always_includes_pass_rate_and_mean_score_ci(self):
        results = [{"id": "a", "score": 1.0}, {"id": "b", "score": 0.5}, {"id": "c", "score": 0.0}]
        confidence = score_eval.compute_confidence(results, threshold=0.7)
        self.assertIn("pass_rate_ci", confidence)
        self.assertIn("mean_score_ci", confidence)
        self.assertAlmostEqual(confidence["pass_rate_ci"]["point"], 1 / 3, places=4)  # only "a" passes at 0.7
        self.assertNotIn("paired_pass_rate_diff", confidence)  # no baseline given

    def test_no_baseline_omits_paired_diff(self):
        results = [{"id": "a", "score": 1.0}]
        confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=None)
        self.assertNotIn("paired_pass_rate_diff", confidence)

    def test_baseline_with_no_overlap_omits_paired_diff(self):
        results = [{"id": "a", "score": 1.0}]
        baseline = [{"id": "different", "score": 1.0}]
        confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=baseline)
        self.assertNotIn("paired_pass_rate_diff", confidence)

    def test_baseline_with_overlap_includes_paired_diff(self):
        # Unique ids, as a real case set would have -- score_eval.py itself
        # doesn't enforce uniqueness (that preflight concern lives in
        # run_judge.py), but matched_scores() assumes ids identify cases,
        # not just lookup keys, so tests should too.
        results = [{"id": f"c{i}", "score": 0.0} for i in range(10)]
        baseline = [{"id": f"c{i}", "score": 1.0} for i in range(10)]
        confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=baseline)
        diff = confidence["paired_pass_rate_diff"]
        self.assertEqual(diff["point_a"], 0.0)
        self.assertEqual(diff["point_b"], 1.0)
        self.assertTrue(diff["significant_at_0.05"])

    def test_baseline_with_overlap_also_includes_paired_mean_score_diff(self):
        results = [{"id": f"c{i}", "score": 0.0} for i in range(10)]
        baseline = [{"id": f"c{i}", "score": 1.0} for i in range(10)]
        confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=baseline)
        diff = confidence["paired_mean_score_diff"]
        self.assertEqual(diff["point_a"], 0.0)
        self.assertEqual(diff["point_b"], 1.0)
        self.assertTrue(diff["significant_at_0.05"])

    def test_mean_score_diff_catches_a_regression_pass_rate_diff_misses(self):
        # Every case drops by 0.29 but stays above the 0.7 pass threshold --
        # pass rate is unchanged (100% -> 100%), so the pass-rate paired
        # diff has zero variance to detect anything, but the raw scores
        # dropped by a large, consistent amount the mean-score diff should
        # catch. This is the exact gap this function exists to close.
        results = [{"id": f"c{i}", "score": 0.71} for i in range(15)]
        baseline = [{"id": f"c{i}", "score": 1.0} for i in range(15)]
        confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=baseline)
        pass_rate_diff = confidence["paired_pass_rate_diff"]
        score_diff = confidence["paired_mean_score_diff"]
        self.assertFalse(pass_rate_diff["significant_at_0.05"])  # unchanged pass rate: nothing to detect
        self.assertTrue(score_diff["significant_at_0.05"])  # but the score drop is real and detected

    def test_baseline_with_no_overlap_omits_paired_mean_score_diff(self):
        results = [{"id": "a", "score": 1.0}]
        baseline = [{"id": "different", "score": 1.0}]
        confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=baseline)
        self.assertNotIn("paired_mean_score_diff", confidence)

    def test_custom_n_boot_and_seed_are_passed_through(self):
        results = [{"id": "a", "score": 1.0}, {"id": "b", "score": 0.0}]
        confidence = score_eval.compute_confidence(results, threshold=0.7, n_boot=250, boot_seed=7)
        self.assertEqual(confidence["pass_rate_ci"]["n_boot"], 250)

    def test_ci_width_reliable_false_below_threshold(self):
        results = [{"id": f"c{i}", "score": 1.0} for i in range(5)]  # n=5 < CI_LOW_RELIABILITY_N=20
        confidence = score_eval.compute_confidence(results, threshold=0.7)
        self.assertFalse(confidence["pass_rate_ci"]["ci_width_reliable"])
        self.assertFalse(confidence["mean_score_ci"]["ci_width_reliable"])

    def test_ci_width_reliable_true_at_threshold(self):
        results = [{"id": f"c{i}", "score": 1.0} for i in range(20)]  # n=20 == CI_LOW_RELIABILITY_N
        confidence = score_eval.compute_confidence(results, threshold=0.7)
        self.assertTrue(confidence["pass_rate_ci"]["ci_width_reliable"])
        self.assertTrue(confidence["mean_score_ci"]["ci_width_reliable"])

    def test_paired_diffs_also_carry_ci_width_reliable(self):
        results = [{"id": f"c{i}", "score": 0.0} for i in range(5)]
        baseline = [{"id": f"c{i}", "score": 1.0} for i in range(5)]
        confidence = score_eval.compute_confidence(results, threshold=0.7, baseline_results=baseline)
        self.assertFalse(confidence["paired_pass_rate_diff"]["ci_width_reliable"])
        self.assertFalse(confidence["paired_mean_score_diff"]["ci_width_reliable"])


def _rows(category, current_scores, baseline_scores, prefix):
    """Builds matched (current, baseline) row lists for one category, ids
    shared between both so compute_per_category_confidence() pairs them."""
    current = [{"id": f"{prefix}{i}", "score": s, "category": category} for i, s in enumerate(current_scores)]
    baseline = [{"id": f"{prefix}{i}", "score": s, "category": category} for i, s in enumerate(baseline_scores)]
    return current, baseline


class ComputePerCategoryConfidence(unittest.TestCase):
    def test_no_baseline_returns_empty(self):
        results = [{"id": "a", "score": 1.0, "category": "x"}]
        self.assertEqual(score_eval.compute_per_category_confidence(results, 0.7, None), {})

    def test_empty_baseline_returns_empty(self):
        results = [{"id": "a", "score": 1.0, "category": "x"}]
        self.assertEqual(score_eval.compute_per_category_confidence(results, 0.7, []), {})

    def test_groups_by_category_case_and_whitespace_insensitively(self):
        current = [
            {"id": "a", "score": 1.0, "category": "Accuracy"},
            {"id": "b", "score": 1.0, "category": " accuracy "},
            {"id": "c", "score": 1.0, "category": "ACCURACY"},
        ]
        baseline = [{"id": f"{c}", "score": 1.0, "category": "accuracy"} for c in "abc"]
        result = score_eval.compute_per_category_confidence(current, 0.7, baseline, min_n=1)
        self.assertEqual(len(result), 1)
        self.assertIn("Accuracy", result)  # first-seen spelling, same convention as summarize()

    def test_category_below_min_n_is_skipped_with_reason(self):
        current, baseline = _rows("tiny", [1.0, 0.0], [1.0, 1.0], "t")
        result = score_eval.compute_per_category_confidence(current, 0.7, baseline, min_n=3)
        self.assertIn("skipped_reason", result["tiny"])
        self.assertEqual(result["tiny"]["n"], 2)
        self.assertNotIn("paired_pass_rate_diff", result["tiny"])

    def test_category_at_or_above_min_n_gets_both_diffs(self):
        # A second category (even a tiny, skipped one) keeps "cat" from
        # being the sole category, so it's actually computed rather than
        # treated as a duplicate of the run-wide result -- see
        # test_sole_category_is_skipped_as_duplicate_of_run_wide below for
        # that other case.
        current, baseline = _rows("cat", [0.0] * 5, [1.0] * 5, "c")
        other_current, other_baseline = _rows("other", [1.0], [1.0], "o")
        result = score_eval.compute_per_category_confidence(current + other_current, 0.7, baseline + other_baseline, min_n=3)
        entry = result["cat"]
        self.assertNotIn("skipped_reason", entry)
        self.assertIn("paired_pass_rate_diff", entry)
        self.assertIn("paired_mean_score_diff", entry)
        self.assertTrue(entry["paired_pass_rate_diff"]["significant_at_0.05"])

    def test_category_diffs_carry_ci_width_reliable(self):
        # This category's n=5 is a legitimate, computed result (not skipped
        # -- min_n=3 is satisfied) but still well below CI_LOW_RELIABILITY_N=20,
        # exactly the regime the ci_width_reliable flag exists to mark: the
        # significance verdict here is trustworthy, the CI's width isn't.
        current, baseline = _rows("cat", [0.0] * 5, [1.0] * 5, "c")
        other_current, other_baseline = _rows("other", [1.0], [1.0], "o")
        result = score_eval.compute_per_category_confidence(current + other_current, 0.7, baseline + other_baseline, min_n=3)
        entry = result["cat"]
        self.assertFalse(entry["paired_pass_rate_diff"]["ci_width_reliable"])
        self.assertFalse(entry["paired_mean_score_diff"]["ci_width_reliable"])

    def test_sole_category_is_skipped_as_duplicate_of_run_wide(self):
        # A single category spanning every matched case is, by
        # construction, testing the identical data compute_confidence()'s
        # run-wide diffs already test -- computing it again wouldn't just
        # be wasted work, it would silently double the multiple-comparisons
        # correction's test count for zero new information (a real bug
        # this repo's own critique process caught: n_tests=4 instead of 2
        # on data with no category field at all).
        current, baseline = _rows("cat", [0.0] * 5, [1.0] * 5, "c")
        result = score_eval.compute_per_category_confidence(current, 0.7, baseline, min_n=3)
        self.assertEqual(len(result), 1)
        self.assertIn("skipped_reason", result["cat"])
        self.assertEqual(result["cat"]["n"], 5)

    def test_no_category_field_is_also_skipped_as_duplicate(self):
        # No "category" key at all -> everything falls into "uncategorized",
        # which is still a single category spanning every matched case.
        current = [{"id": f"c{i}", "score": 0.0} for i in range(5)]
        baseline = [{"id": f"c{i}", "score": 1.0} for i in range(5)]
        result = score_eval.compute_per_category_confidence(current, 0.7, baseline, min_n=3)
        self.assertEqual(list(result.keys()), ["uncategorized"])
        self.assertIn("skipped_reason", result["uncategorized"])

    def test_catches_regression_diluted_away_in_aggregate(self):
        # The confirmed real-world case, loaded directly from this skill's
        # own worked example rather than hand-tuned synthetic data: a
        # regression concentrated in the `accuracy` category, diluted by
        # unaffected cases from three other categories, invisible to the
        # run-wide aggregate but not to the per-category test. Hand-tuning
        # synthetic data to land in this exact "diluted below significance
        # yet real per-category" statistical regime turned out to be
        # surprisingly fiddly (a too-clean synthetic regression stays
        # aggregate-significant even at high dilution ratios) -- the real
        # example already sits there, confirmed via examples/README.md's
        # own worked numbers, so this test uses it directly.
        examples_dir = os.path.join(HERE, "..", "examples")
        current = score_eval.load_results(os.path.join(examples_dir, "results_regressed.jsonl"))
        baseline = score_eval.load_results(os.path.join(examples_dir, "results_baseline.jsonl"))

        # Aggregate test sees the dilution.
        overall = score_eval.compute_confidence(current, 0.7, baseline_results=baseline)
        self.assertFalse(overall["paired_pass_rate_diff"]["significant_at_0.05"])

        # Per-category test isolates the real regression.
        per_category = score_eval.compute_per_category_confidence(current, 0.7, baseline, min_n=3)
        self.assertTrue(per_category["accuracy"]["paired_pass_rate_diff"]["significant_at_0.05"])
        self.assertLess(per_category["accuracy"]["paired_pass_rate_diff"]["diff"], 0)
        self.assertFalse(per_category["format"]["paired_pass_rate_diff"]["significant_at_0.05"])

    def test_uses_current_rows_category_not_baseline_rows(self):
        # Grouping follows the *current* run's category assignment, same
        # convention summarize()'s by_category uses -- a case relabeled
        # between runs is grouped under its current label.
        current = [{"id": f"c{i}", "score": 1.0, "category": "new_label"} for i in range(3)]
        baseline = [{"id": f"c{i}", "score": 1.0, "category": "old_label"} for i in range(3)]
        result = score_eval.compute_per_category_confidence(current, 0.7, baseline, min_n=3)
        self.assertIn("new_label", result)
        self.assertNotIn("old_label", result)

    def test_only_matched_ids_contribute(self):
        current = [{"id": "a", "score": 1.0, "category": "x"}, {"id": "unmatched", "score": 0.0, "category": "x"}]
        baseline = [{"id": "a", "score": 1.0, "category": "x"}]
        result = score_eval.compute_per_category_confidence(current, 0.7, baseline, min_n=1)
        self.assertEqual(result["x"]["n"], 1)


class BonferroniAlpha(unittest.TestCase):
    def test_single_test_returns_family_alpha_unchanged(self):
        self.assertEqual(score_eval.bonferroni_alpha(1, family_alpha=0.05), 0.05)

    def test_divides_by_test_count(self):
        self.assertAlmostEqual(score_eval.bonferroni_alpha(10, family_alpha=0.05), 0.005)

    def test_zero_tests_returns_family_alpha_unchanged(self):
        self.assertEqual(score_eval.bonferroni_alpha(0, family_alpha=0.05), 0.05)

    def test_negative_tests_returns_family_alpha_unchanged(self):
        # Defensive only -- n_tests is always a len() in real callers, never
        # negative, but a bogus caller shouldn't get a negative alpha.
        self.assertEqual(score_eval.bonferroni_alpha(-1, family_alpha=0.05), 0.05)

    def test_custom_family_alpha_respected(self):
        self.assertAlmostEqual(score_eval.bonferroni_alpha(4, family_alpha=0.1), 0.025)


class BenjaminiHochbergSignificance(unittest.TestCase):
    def test_empty_list_is_a_noop(self):
        diffs = []
        score_eval.benjamini_hochberg_significance(diffs)  # must not raise
        self.assertEqual(diffs, [])

    def test_single_diff_uses_plain_family_alpha(self):
        diffs = [{"p_value": 0.03}]
        score_eval.benjamini_hochberg_significance(diffs, family_alpha=0.05)
        self.assertTrue(diffs[0]["significant_after_correction"])

    def test_step_up_procedure_matches_hand_computed_example(self):
        # p-values [0.01, 0.03, 0.4] at family_alpha=0.05, m=3.
        # BH thresholds: rank1=0.05/3=0.0167, rank2=2*0.05/3=0.0333, rank3=0.05.
        # 0.01 <= 0.0167 (rank1 passes), 0.03 <= 0.0333 (rank2 passes),
        # 0.4 > 0.05 (rank3 fails) -> largest passing rank is 2, so ranks
        # 1 and 2 are both significant, rank 3 is not. Notably: 0.03 would
        # NOT survive a Bonferroni correction at the same m (0.05/3=0.0167),
        # but does survive BH -- the concrete difference between the two
        # methods this function exists to capture.
        diffs = [{"p_value": 0.4}, {"p_value": 0.01}, {"p_value": 0.03}]
        score_eval.benjamini_hochberg_significance(diffs, family_alpha=0.05)
        self.assertFalse(diffs[0]["significant_after_correction"])  # p=0.4
        self.assertTrue(diffs[1]["significant_after_correction"])   # p=0.01
        self.assertTrue(diffs[2]["significant_after_correction"])   # p=0.03

    def test_all_large_p_values_none_significant(self):
        diffs = [{"p_value": 0.9}, {"p_value": 0.8}, {"p_value": 1.0}]
        score_eval.benjamini_hochberg_significance(diffs)
        self.assertTrue(all(not d["significant_after_correction"] for d in diffs))

    def test_all_tiny_p_values_all_significant(self):
        diffs = [{"p_value": 0.001}, {"p_value": 0.002}, {"p_value": 0.003}]
        score_eval.benjamini_hochberg_significance(diffs)
        self.assertTrue(all(d["significant_after_correction"] for d in diffs))


class ApplyMultipleComparisonsCorrection(unittest.TestCase):
    def test_counts_both_run_wide_diffs(self):
        confidence = {
            "paired_pass_rate_diff": {"p_value": 0.01, "diff": -0.1},
            "paired_mean_score_diff": {"p_value": 0.02, "diff": -0.1},
        }
        correction = score_eval.apply_multiple_comparisons_correction(confidence, None)
        self.assertEqual(correction["n_tests"], 2)
        self.assertEqual(correction["method"], "benjamini_hochberg")
        self.assertEqual(correction["family_alpha"], 0.05)

    def test_adds_significant_after_correction_key_to_each_diff(self):
        confidence = {"paired_pass_rate_diff": {"p_value": 0.01, "diff": -0.1}}
        score_eval.apply_multiple_comparisons_correction(confidence, None)
        self.assertIn("significant_after_correction", confidence["paired_pass_rate_diff"])

    def test_counts_only_non_skipped_categories(self):
        confidence = {}
        per_category = {
            "a": {"n": 8, "paired_pass_rate_diff": {"p_value": 0.5, "diff": 0.0}, "paired_mean_score_diff": {"p_value": 0.5, "diff": 0.0}},
            "tiny": {"n": 2, "skipped_reason": "too few"},
        }
        correction = score_eval.apply_multiple_comparisons_correction(confidence, per_category)
        self.assertEqual(correction["n_tests"], 2)  # only category "a"'s two diffs; "tiny" is skipped
        self.assertNotIn("significant_after_correction", per_category["tiny"])

    def test_no_tests_at_all(self):
        correction = score_eval.apply_multiple_comparisons_correction(None, None)
        self.assertEqual(correction["n_tests"], 0)
        self.assertEqual(correction["family_alpha"], score_eval.DEFAULT_FAMILY_ALPHA)

    def test_matches_documented_real_example_outcome(self):
        # The real, confirmed consequence documented in examples/README.md:
        # at 10 tests together (2 run-wide + 4 categories x 2), the
        # accuracy category's uncorrected p=0.043 -- significant at the old
        # fixed 0.05 -- does not survive Benjamini-Hochberg correction
        # against the other 9 tests examined in the same pass.
        examples_dir = os.path.join(HERE, "..", "examples")
        results = score_eval.load_results(os.path.join(examples_dir, "results_regressed.jsonl"))
        baseline = score_eval.load_results(os.path.join(examples_dir, "results_baseline.jsonl"))
        confidence = score_eval.compute_confidence(results, 0.7, baseline_results=baseline)
        per_category = score_eval.compute_per_category_confidence(results, 0.7, baseline)
        correction = score_eval.apply_multiple_comparisons_correction(confidence, per_category)
        self.assertEqual(correction["n_tests"], 10)
        self.assertFalse(per_category["accuracy"]["paired_pass_rate_diff"]["significant_after_correction"])

    def test_more_robust_to_unrelated_categories_than_bonferroni(self):
        # The exact finding that motivated switching correction methods:
        # a fixed, unchanged regression (p=0.005) stops clearing a
        # Bonferroni-corrected bar once enough unrelated, entirely stable
        # categories exist alongside it (3+ here), but keeps clearing the
        # Benjamini-Hochberg bar at the same test count.
        acc_cur = [1.0, 0.4, 0.4, 0.8, 0.9, 0.3, 1.0, 0.5]
        acc_base = [1.0, 0.9, 1.0, 0.8, 0.9, 0.9, 1.0, 0.8]
        current, baseline = [], []
        for i, (c, b) in enumerate(zip(acc_cur, acc_base)):
            current.append({"id": f"acc{i}", "score": c, "category": "accuracy"})
            baseline.append({"id": f"acc{i}", "score": b, "category": "accuracy"})
        for cat_idx in range(7):  # well past the 3-category point where Bonferroni fails
            for i in range(4):
                current.append({"id": f"cat{cat_idx}_{i}", "score": 0.9, "category": f"stable{cat_idx}"})
                baseline.append({"id": f"cat{cat_idx}_{i}", "score": 0.9, "category": f"stable{cat_idx}"})

        confidence = score_eval.compute_confidence(current, 0.7, baseline_results=baseline)
        per_category = score_eval.compute_per_category_confidence(current, 0.7, baseline)
        accuracy_diff = per_category["accuracy"]["paired_pass_rate_diff"]
        self.assertEqual(accuracy_diff["p_value"], 0.005)  # same fixed signal throughout

        n_tests = 2 + sum(2 for e in per_category.values() if "skipped_reason" not in e)
        would_survive_bonferroni = accuracy_diff["p_value"] < score_eval.bonferroni_alpha(n_tests)
        self.assertFalse(would_survive_bonferroni)  # Bonferroni: no longer detected at this test count

        score_eval.apply_multiple_comparisons_correction(confidence, per_category)
        self.assertTrue(per_category["accuracy"]["paired_pass_rate_diff"]["significant_after_correction"])  # BH: still detected

    def test_reports_regression_and_non_regression_candidate_counts(self):
        confidence = {
            "paired_pass_rate_diff": {"p_value": 0.01, "diff": -0.1},
            "paired_mean_score_diff": {"p_value": 0.01, "diff": 0.2},  # improvement
        }
        correction = score_eval.apply_multiple_comparisons_correction(confidence, None)
        self.assertEqual(correction["n_regression_candidates"], 1)
        self.assertEqual(correction["n_non_regression_candidates"], 1)
        self.assertEqual(correction["n_tests"], 2)

    def test_zero_diff_counted_as_non_regression(self):
        confidence = {"paired_pass_rate_diff": {"p_value": 0.5, "diff": 0.0}}
        correction = score_eval.apply_multiple_comparisons_correction(confidence, None)
        self.assertEqual(correction["n_regression_candidates"], 0)
        self.assertEqual(correction["n_non_regression_candidates"], 1)

    def test_unrelated_improvements_never_affect_a_fixed_regressions_significance(self):
        # The finding that motivated the direction split: pooling
        # regression and improvement p-values into one BH ranking let an
        # unrelated, strongly significant improvement elsewhere in the
        # same run lower the effective bar for a borderline regression to
        # pass, since BH's rank-dependent threshold grows with rank and
        # low-p-value improvements occupy the earliest ranks ahead of it.
        # Direct, confound-free check (no realistic eval-set pooling to
        # muddy it): a fixed borderline regression's significance must not
        # change as more unrelated improvement diffs are added.
        def make_confidence():
            return {
                "paired_pass_rate_diff": {"p_value": 0.043, "diff": -0.1},
                "paired_mean_score_diff": {"p_value": 0.043, "diff": -0.1},
            }

        results = []
        for n_improvements in (0, 1, 2, 4, 8):
            confidence = make_confidence()
            per_category = {
                f"imp{i}": {
                    "n": 10,
                    "paired_pass_rate_diff": {"p_value": 0.001, "diff": 0.3},
                    "paired_mean_score_diff": {"p_value": 0.001, "diff": 0.3},
                }
                for i in range(n_improvements)
            }
            score_eval.apply_multiple_comparisons_correction(confidence, per_category)
            results.append(confidence["paired_pass_rate_diff"]["significant_after_correction"])

        self.assertEqual(len(set(results)), 1)  # identical verdict regardless of n_improvements

    def test_unrelated_stable_non_regressions_never_affect_a_fixed_regressions_significance(self):
        # Same property, checked for diff=0 ("stable," not an improvement)
        # unrelated tests instead of diff>0 ones -- both belong in the
        # non-regression family and must be equally inert to the
        # regression family's outcome.
        def make_confidence():
            return {
                "paired_pass_rate_diff": {"p_value": 0.043, "diff": -0.1},
                "paired_mean_score_diff": {"p_value": 0.043, "diff": -0.1},
            }

        results = []
        for n_stable in (0, 1, 2, 4, 8):
            confidence = make_confidence()
            per_category = {
                f"stable{i}": {
                    "n": 10,
                    "paired_pass_rate_diff": {"p_value": 1.0, "diff": 0.0},
                    "paired_mean_score_diff": {"p_value": 1.0, "diff": 0.0},
                }
                for i in range(n_stable)
            }
            score_eval.apply_multiple_comparisons_correction(confidence, per_category)
            results.append(confidence["paired_pass_rate_diff"]["significant_after_correction"])

        self.assertEqual(len(set(results)), 1)  # identical verdict regardless of n_stable

    def test_improvements_still_get_their_own_bh_corrected_significance(self):
        # The split doesn't mean improvements go uncorrected -- they get
        # their own BH family, so "SIGNIFICANT IMPROVEMENT" in the report
        # stays a real, corrected claim, just decoupled from the
        # regression family's outcome.
        confidence = {
            "paired_pass_rate_diff": {"p_value": 0.4, "diff": 0.1},   # weak improvement
            "paired_mean_score_diff": {"p_value": 0.001, "diff": 0.3},  # strong improvement
        }
        score_eval.apply_multiple_comparisons_correction(confidence, None)
        self.assertTrue(confidence["paired_mean_score_diff"]["significant_after_correction"])
        self.assertFalse(confidence["paired_pass_rate_diff"]["significant_after_correction"])


class FindMetricRegression(unittest.TestCase):
    def test_increase_beyond_tolerance_flagged(self):
        summary = {"mean_cost_usd": 0.008}
        baseline = {"mean_cost_usd": 0.005}
        reg = score_eval.find_metric_regression(summary, baseline, "mean_cost_usd", 0.2)
        self.assertIsNotNone(reg)
        self.assertEqual(reg["metric"], "mean_cost_usd")
        self.assertEqual(reg["baseline"], 0.005)
        self.assertEqual(reg["current"], 0.008)

    def test_increase_within_tolerance_not_flagged(self):
        # 0.0055 is a 10% increase over 0.005 — within a 20% tolerance.
        summary = {"mean_cost_usd": 0.0055}
        baseline = {"mean_cost_usd": 0.005}
        self.assertIsNone(score_eval.find_metric_regression(summary, baseline, "mean_cost_usd", 0.2))

    def test_exactly_at_tolerance_boundary_not_flagged(self):
        # Strictly greater-than, same convention as SummarizeCalibration's
        # boundary test and score_eval's own pass-rate threshold.
        summary = {"mean_latency_ms": 1200.0}
        baseline = {"mean_latency_ms": 1000.0}
        self.assertIsNone(score_eval.find_metric_regression(summary, baseline, "mean_latency_ms", 0.2))

    def test_decrease_not_flagged(self):
        summary = {"mean_cost_usd": 0.003}
        baseline = {"mean_cost_usd": 0.005}
        self.assertIsNone(score_eval.find_metric_regression(summary, baseline, "mean_cost_usd", 0.2))

    def test_missing_metric_in_either_summary_returns_none(self):
        self.assertIsNone(score_eval.find_metric_regression({"mean_cost_usd": 0.01}, {}, "mean_cost_usd", 0.2))
        self.assertIsNone(score_eval.find_metric_regression({}, {"mean_cost_usd": 0.01}, "mean_cost_usd", 0.2))

    def test_none_summaries_return_none(self):
        self.assertIsNone(score_eval.find_metric_regression(None, {"mean_cost_usd": 0.01}, "mean_cost_usd", 0.2))
        self.assertIsNone(score_eval.find_metric_regression({"mean_cost_usd": 0.01}, None, "mean_cost_usd", 0.2))


class CheckGates(unittest.TestCase):
    def _summary(self, pass_rate, **extra):
        return {"pass_rate": pass_rate, "total": 10, **extra}

    def test_no_gates_configured_passes(self):
        self.assertEqual(score_eval.check_gates(self._summary(0.1), [], None, False), [])

    def test_fail_under_triggers_below(self):
        failures = score_eval.check_gates(self._summary(0.5), [], 0.8, False)
        self.assertEqual(len(failures), 1)

    def test_fail_under_passes_at_or_above(self):
        self.assertEqual(score_eval.check_gates(self._summary(0.8), [], 0.8, False), [])

    def test_fail_under_with_no_results(self):
        failures = score_eval.check_gates(None, [], 0.8, False)
        self.assertEqual(len(failures), 1)

    def test_fail_on_regression_triggers(self):
        regs = [{"id": "a", "baseline_score": 1.0, "current_score": 0.0, "kind": "threshold_crossing"}]
        failures = score_eval.check_gates(self._summary(1.0), regs, None, True)
        self.assertEqual(len(failures), 1)

    def test_fail_on_regression_message_breaks_down_by_kind(self):
        regs = [
            {"id": "a", "baseline_score": 1.0, "current_score": 0.0, "kind": "threshold_crossing"},
            {"id": "b", "baseline_score": 1.0, "current_score": 0.75, "kind": "magnitude_drop"},
        ]
        failures = score_eval.check_gates(self._summary(1.0), regs, None, True)
        self.assertEqual(len(failures), 1)
        self.assertIn("2 regression(s)", failures[0])
        self.assertIn("1 crossed pass/fail", failures[0])
        self.assertIn("1 dropped sharply", failures[0])

    def test_fail_on_regression_no_regs_passes(self):
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, True), [])

    def test_cost_regression_triggers(self):
        cost_reg = {"metric": "mean_cost_usd", "baseline": 0.005, "current": 0.01, "tolerance": 0.2}
        failures = score_eval.check_gates(self._summary(1.0), [], None, False, cost_regression=cost_reg)
        self.assertEqual(len(failures), 1)
        self.assertIn("cost", failures[0])

    def test_no_cost_regression_passes(self):
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, cost_regression=None), [])

    def test_latency_regression_triggers(self):
        lat_reg = {"metric": "mean_latency_ms", "baseline": 1000.0, "current": 2000.0, "tolerance": 0.2}
        failures = score_eval.check_gates(self._summary(1.0), [], None, False, latency_regression=lat_reg)
        self.assertEqual(len(failures), 1)
        self.assertIn("latency", failures[0])

    def test_fail_if_mean_cost_above_triggers(self):
        summary = self._summary(1.0, mean_cost_usd=0.02)
        failures = score_eval.check_gates(summary, [], None, False, fail_if_mean_cost_above=0.01)
        self.assertEqual(len(failures), 1)

    def test_fail_if_mean_cost_above_passes_when_under(self):
        summary = self._summary(1.0, mean_cost_usd=0.005)
        self.assertEqual(score_eval.check_gates(summary, [], None, False, fail_if_mean_cost_above=0.01), [])

    def test_fail_if_mean_latency_above_triggers(self):
        summary = self._summary(1.0, mean_latency_ms=3000.0)
        failures = score_eval.check_gates(summary, [], None, False, fail_if_mean_latency_above=2000.0)
        self.assertEqual(len(failures), 1)

    def test_fail_if_mean_cost_above_with_no_cost_data_does_not_crash(self):
        # A results file with no cost_usd anywhere — summary lacks the key
        # entirely, this gate just has nothing to check, not an error.
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, fail_if_mean_cost_above=0.01), [])

    def test_multiple_gate_failures_all_reported(self):
        summary = self._summary(0.1, mean_cost_usd=0.02)
        failures = score_eval.check_gates(summary, [], 0.8, False, fail_if_mean_cost_above=0.01)
        self.assertEqual(len(failures), 2)

    def test_significant_regression_triggers(self):
        sig_reg = {"point_a": 0.5, "point_b": 0.9, "diff": -0.4, "p_value": 0.01, "significant_after_correction": True, "n": 20}
        failures = score_eval.check_gates(self._summary(0.5), [], None, False, significant_regression=sig_reg)
        self.assertEqual(len(failures), 1)
        self.assertIn("significant", failures[0])

    def test_not_significant_does_not_trigger(self):
        not_sig = {"point_a": 0.8, "point_b": 0.9, "diff": -0.1, "p_value": 0.42, "significant_after_correction": False, "n": 20}
        self.assertEqual(score_eval.check_gates(self._summary(0.8), [], None, False, significant_regression=not_sig), [])

    def test_significant_improvement_does_not_trigger(self):
        # diff > 0 means current beat baseline -- significant in the GOOD
        # direction should never fail a build.
        improved = {"point_a": 0.95, "point_b": 0.6, "diff": 0.35, "p_value": 0.01, "significant_after_correction": True, "n": 20}
        self.assertEqual(score_eval.check_gates(self._summary(0.95), [], None, False, significant_regression=improved), [])

    def test_none_significant_regression_does_not_trigger(self):
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, significant_regression=None), [])

    def test_significant_score_regression_triggers(self):
        sig_reg = {"point_a": 0.71, "point_b": 1.0, "diff": -0.29, "p_value": 0.0, "significant_after_correction": True, "n": 15}
        failures = score_eval.check_gates(self._summary(1.0), [], None, False, significant_score_regression=sig_reg)
        self.assertEqual(len(failures), 1)
        self.assertIn("mean score", failures[0])

    def test_not_significant_score_regression_does_not_trigger(self):
        not_sig = {"point_a": 0.84, "point_b": 0.89, "diff": -0.05, "p_value": 0.25, "significant_after_correction": False, "n": 20}
        self.assertEqual(score_eval.check_gates(self._summary(0.8), [], None, False, significant_score_regression=not_sig), [])

    def test_significant_score_improvement_does_not_trigger(self):
        improved = {"point_a": 0.95, "point_b": 0.6, "diff": 0.35, "p_value": 0.01, "significant_after_correction": True, "n": 20}
        self.assertEqual(score_eval.check_gates(self._summary(0.95), [], None, False, significant_score_regression=improved), [])

    def test_none_significant_score_regression_does_not_trigger(self):
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, significant_score_regression=None), [])

    def test_both_significant_pass_rate_and_score_regression_reported_separately(self):
        # A case that trips both signals should surface both failure
        # messages, not collapse into one -- each is independently
        # actionable evidence.
        pass_rate_reg = {"point_a": 0.5, "point_b": 0.9, "diff": -0.4, "p_value": 0.01, "significant_after_correction": True, "n": 20}
        score_reg = {"point_a": 0.4, "point_b": 0.85, "diff": -0.45, "p_value": 0.0, "significant_after_correction": True, "n": 20}
        failures = score_eval.check_gates(
            self._summary(0.5), [], None, False,
            significant_regression=pass_rate_reg, significant_score_regression=score_reg,
        )
        self.assertEqual(len(failures), 2)

    def test_per_category_significant_regression_triggers(self):
        per_category = {
            "accuracy": {
                "n": 8,
                "paired_pass_rate_diff": {"point_a": 0.6, "point_b": 1.0, "diff": -0.4, "p_value": 0.04, "significant_after_correction": True, "n": 8},
                "paired_mean_score_diff": {"point_a": 0.6, "point_b": 0.9, "diff": -0.3, "p_value": 0.04, "significant_after_correction": True, "n": 8},
            },
        }
        failures = score_eval.check_gates(self._summary(0.8), [], None, False, per_category_confidence=per_category)
        self.assertEqual(len(failures), 2)  # both metrics tripped for this one category
        self.assertTrue(any("accuracy" in f for f in failures))

    def test_per_category_significant_improvement_does_not_trigger(self):
        per_category = {
            "format": {
                "n": 5,
                "paired_pass_rate_diff": {"point_a": 1.0, "point_b": 0.8, "diff": 0.2, "p_value": 0.03, "significant_after_correction": True, "n": 5},
                "paired_mean_score_diff": {"point_a": 0.97, "point_b": 0.88, "diff": 0.09, "p_value": 0.03, "significant_after_correction": True, "n": 5},
            },
        }
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, per_category_confidence=per_category), [])

    def test_per_category_skipped_entry_does_not_trigger(self):
        per_category = {"tiny": {"n": 2, "skipped_reason": "only 2 matched case(s) — need at least 3"}}
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, per_category_confidence=per_category), [])

    def test_per_category_not_significant_does_not_trigger(self):
        per_category = {
            "grounding": {
                "n": 4,
                "paired_pass_rate_diff": {"point_a": 0.9, "point_b": 1.0, "diff": -0.1, "p_value": 0.7, "significant_after_correction": False, "n": 4},
                "paired_mean_score_diff": {"point_a": 0.9, "point_b": 0.95, "diff": -0.05, "p_value": 0.7, "significant_after_correction": False, "n": 4},
            },
        }
        self.assertEqual(score_eval.check_gates(self._summary(0.9), [], None, False, per_category_confidence=per_category), [])

    def test_none_per_category_confidence_does_not_trigger(self):
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, per_category_confidence=None), [])

    def test_multiple_categories_each_report_their_own_failure(self):
        per_category = {
            "accuracy": {
                "n": 8,
                "paired_pass_rate_diff": {"point_a": 0.6, "point_b": 1.0, "diff": -0.4, "p_value": 0.04, "significant_after_correction": True, "n": 8},
                "paired_mean_score_diff": {"point_a": 0.6, "point_b": 0.9, "diff": -0.3, "p_value": 0.7, "significant_after_correction": False, "n": 8},
            },
            "tool_use": {
                "n": 5,
                "paired_pass_rate_diff": {"point_a": 0.4, "point_b": 1.0, "diff": -0.6, "p_value": 0.02, "significant_after_correction": True, "n": 5},
                "paired_mean_score_diff": {"point_a": 0.4, "point_b": 0.9, "diff": -0.5, "p_value": 0.02, "significant_after_correction": True, "n": 5},
            },
        }
        failures = score_eval.check_gates(self._summary(0.5), [], None, False, per_category_confidence=per_category)
        self.assertEqual(len(failures), 3)  # accuracy pass-rate + tool_use pass-rate + tool_use score


class Cli(unittest.TestCase):
    """End-to-end: invoke the script as a subprocess and check exit codes."""

    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def make(self, rows):
        path = write_jsonl(rows)
        self._paths.append(path)
        return path

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            capture_output=True, text=True,
        )

    def test_basic_run_exits_zero(self):
        path = self.make([{"id": "a", "score": 1.0}, {"id": "b", "score": 0.9}])
        proc = self.run_script(path)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Eval Report", proc.stdout)

    def test_fail_under_gate_exits_nonzero(self):
        path = self.make([{"id": "a", "score": 0.0}, {"id": "b", "score": 0.0}])
        proc = self.run_script(path, "--fail-under", "0.8")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("GATE FAILED", proc.stderr)

    def test_fail_under_gate_passes_when_met(self):
        path = self.make([{"id": "a", "score": 1.0}, {"id": "b", "score": 1.0}])
        proc = self.run_script(path, "--fail-under", "0.8")
        self.assertEqual(proc.returncode, 0)

    def test_fail_on_regression_gate_exits_nonzero(self):
        base = self.make([{"id": "a", "score": 1.0}])
        cur = self.make([{"id": "a", "score": 0.0}])
        proc = self.run_script(cur, "--baseline", base, "--fail-on-regression")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("GATE FAILED", proc.stderr)

    def test_json_out_writes_valid_summary(self):
        path = self.make([{"id": "a", "score": 1.0, "category": "x"}])
        fd, out = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self._paths.append(out)
        proc = self.run_script(path, "--json-out", out)
        self.assertEqual(proc.returncode, 0)
        with open(out) as f:
            data = json.load(f)
        self.assertEqual(data["total"], 1)
        self.assertIn("by_category", data)

    def test_regression_min_drop_without_baseline_is_a_usage_error(self):
        path = self.make([{"id": "a", "score": 1.0}])
        proc = self.run_script(path, "--regression-min-drop", "0.2")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("requires --baseline", proc.stderr)

    def test_regression_min_drop_catches_still_passing_case_that_fail_on_regression_alone_misses(self):
        base = self.make([{"id": "a", "score": 1.0}])
        cur = self.make([{"id": "a", "score": 0.71}])  # stays >= default 0.7 threshold

        # Without --regression-min-drop, --fail-on-regression alone must NOT catch this.
        proc_without = self.run_script(cur, "--baseline", base, "--fail-on-regression")
        self.assertEqual(proc_without.returncode, 0)

        # With it, the same still-passing drop must be caught.
        proc_with = self.run_script(cur, "--baseline", base, "--fail-on-regression", "--regression-min-drop", "0.2")
        self.assertEqual(proc_with.returncode, 1)
        self.assertIn("GATE FAILED", proc_with.stderr)
        self.assertIn("dropped sharply", proc_with.stderr)

    def test_regression_min_drop_report_labels_kind_separately(self):
        base = self.make([{"id": "a", "score": 1.0}, {"id": "b", "score": 1.0}])
        cur = self.make([{"id": "a", "score": 0.0}, {"id": "b", "score": 0.71}])
        proc = self.run_script(cur, "--baseline", base, "--regression-min-drop", "0.2")
        self.assertEqual(proc.returncode, 0)  # no --fail-on-regression, so just a report
        self.assertIn("Passed before, failing now", proc.stdout)
        self.assertIn("Still passing, but dropped sharply", proc.stdout)

    def test_fail_on_cost_regression_without_baseline_is_a_usage_error(self):
        path = self.make([{"id": "a", "score": 1.0, "cost_usd": 0.01}])
        proc = self.run_script(path, "--fail-on-cost-regression")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("require --baseline", proc.stderr)

    def test_fail_on_cost_regression_gate_exits_nonzero(self):
        base = self.make([{"id": "a", "score": 1.0, "cost_usd": 0.005}])
        cur = self.make([{"id": "a", "score": 1.0, "cost_usd": 0.02}])  # +300%, well past default 20%
        proc = self.run_script(cur, "--baseline", base, "--fail-on-cost-regression")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("GATE FAILED", proc.stderr)
        self.assertIn("cost regression", proc.stdout.lower())

    def test_fail_on_cost_regression_passes_within_tolerance(self):
        base = self.make([{"id": "a", "score": 1.0, "cost_usd": 0.005}])
        cur = self.make([{"id": "a", "score": 1.0, "cost_usd": 0.0055}])  # +10%
        proc = self.run_script(cur, "--baseline", base, "--fail-on-cost-regression")
        self.assertEqual(proc.returncode, 0)

    def test_fail_on_latency_regression_gate_exits_nonzero(self):
        base = self.make([{"id": "a", "score": 1.0, "latency_ms": 1000}])
        cur = self.make([{"id": "a", "score": 1.0, "latency_ms": 5000}])
        proc = self.run_script(cur, "--baseline", base, "--fail-on-latency-regression")
        self.assertEqual(proc.returncode, 1)

    def test_custom_tolerance_widens_what_passes(self):
        base = self.make([{"id": "a", "score": 1.0, "cost_usd": 0.005}])
        cur = self.make([{"id": "a", "score": 1.0, "cost_usd": 0.008}])  # +60%
        # Default 20% tolerance would fail this; a wider explicit tolerance shouldn't.
        proc = self.run_script(cur, "--baseline", base, "--fail-on-cost-regression", "--cost-regression-tolerance", "0.7")
        self.assertEqual(proc.returncode, 0)

    def test_fail_if_mean_cost_above_gate_exits_nonzero_no_baseline_needed(self):
        path = self.make([{"id": "a", "score": 1.0, "cost_usd": 0.02}])
        proc = self.run_script(path, "--fail-if-mean-cost-above", "0.01")
        self.assertEqual(proc.returncode, 1)

    def test_fail_if_mean_latency_above_gate_passes_when_under(self):
        path = self.make([{"id": "a", "score": 1.0, "latency_ms": 500}])
        proc = self.run_script(path, "--fail-if-mean-latency-above", "2000")
        self.assertEqual(proc.returncode, 0)

    def test_likely_typo_categories_warned_on_stderr_without_failing(self):
        path = self.make([
            {"id": "a", "score": 1.0, "category": "accuracy"},
            {"id": "b", "score": 1.0, "category": "accuraccy"},
        ])
        proc = self.run_script(path)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("look similar", proc.stderr)
        self.assertIn("accuracy", proc.stderr)
        self.assertIn("accuraccy", proc.stderr)

    def test_no_typo_warning_for_clean_categories(self):
        path = self.make([
            {"id": "a", "score": 1.0, "category": "accuracy"},
            {"id": "b", "score": 1.0, "category": "format"},
        ])
        proc = self.run_script(path)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("look similar", proc.stderr)

    def test_typo_check_spans_baseline_categories_too(self):
        base = self.make([{"id": "a", "score": 1.0, "category": "accuraccy"}])
        cur = self.make([{"id": "a", "score": 1.0, "category": "accuracy"}])
        proc = self.run_script(cur, "--baseline", base)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("look similar", proc.stderr)

    def test_ci_flag_prints_confidence_interval(self):
        path = self.make([{"id": "a", "score": 1.0}, {"id": "b", "score": 0.0}, {"id": "c", "score": 1.0}])
        proc = self.run_script(path, "--ci")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("95% CI (bootstrap", proc.stdout)
        self.assertIn("Pass rate:", proc.stdout)
        self.assertIn("Mean score:", proc.stdout)

    def test_ci_flag_without_baseline_has_no_paired_diff_line(self):
        path = self.make([{"id": "a", "score": 1.0}])
        proc = self.run_script(path, "--ci")
        self.assertNotIn("vs baseline", proc.stdout)

    def test_ci_flag_with_baseline_prints_paired_diff(self):
        base = self.make([{"id": f"c{i}", "score": 1.0} for i in range(10)])
        cur = self.make([{"id": f"c{i}", "score": 0.0} for i in range(10)])
        proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertIn("Pass rate vs baseline:", proc.stdout)
        self.assertIn("SIGNIFICANT", proc.stdout)

    def test_without_ci_flag_no_confidence_section_printed(self):
        path = self.make([{"id": "a", "score": 1.0}])
        proc = self.run_script(path)
        self.assertNotIn("95% CI", proc.stdout)

    def test_fail_on_significant_regression_requires_baseline(self):
        path = self.make([{"id": "a", "score": 1.0}])
        proc = self.run_script(path, "--fail-on-significant-regression")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("require", proc.stderr.lower())

    def test_fail_on_significant_regression_fires_on_clear_drop(self):
        base = self.make([{"id": f"c{i}", "score": 1.0} for i in range(15)])
        cur = self.make([{"id": f"c{i}", "score": 0.0} for i in range(15)])
        proc = self.run_script(cur, "--baseline", base, "--fail-on-significant-regression")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--fail-on-significant-regression", proc.stderr)

    def test_fail_on_significant_regression_passes_on_noise_level_shift(self):
        # One case out of 15 flips -- exactly the kind of small, plausibly-
        # noise shift --fail-on-regression alone can't distinguish from a
        # real regression, but the paired bootstrap test can.
        base = self.make([{"id": f"c{i}", "score": 1.0} for i in range(15)])
        rows = [{"id": f"c{i}", "score": 1.0} for i in range(15)]
        rows[0]["score"] = 0.0
        cur = self.make(rows)
        proc = self.run_script(cur, "--baseline", base, "--fail-on-significant-regression")
        self.assertEqual(proc.returncode, 0)

    def test_fail_on_significant_regression_catches_score_drop_pass_rate_test_misses(self):
        # Every case drops from 1.0 to 0.71 -- still passes the 0.7
        # threshold (--fail-on-regression sees zero flips, pass rate
        # unchanged 100% -> 100%), but the magnitude drop is large and
        # consistent. The mean-score paired test should catch this even
        # though the pass-rate test alone has nothing to detect.
        base = self.make([{"id": f"c{i}", "score": 1.0} for i in range(15)])
        cur = self.make([{"id": f"c{i}", "score": 0.71} for i in range(15)])
        plain = self.run_script(cur, "--baseline", base, "--fail-on-regression")
        self.assertEqual(plain.returncode, 0)  # confirms the blind spot exists
        sig = self.run_script(cur, "--baseline", base, "--fail-on-significant-regression")
        self.assertEqual(sig.returncode, 1)
        self.assertIn("mean score", sig.stderr)

    def test_ci_report_includes_mean_score_vs_baseline_line(self):
        base = self.make([{"id": f"c{i}", "score": 1.0} for i in range(10)])
        cur = self.make([{"id": f"c{i}", "score": 0.5} for i in range(10)])
        proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertIn("Mean score vs baseline:", proc.stdout)

    def test_json_out_includes_confidence_when_ci_passed(self):
        path = self.make([{"id": "a", "score": 1.0}, {"id": "b", "score": 0.0}])
        out_fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)
        self._paths.append(out_path)
        proc = self.run_script(path, "--ci", "--json-out", out_path)
        self.assertEqual(proc.returncode, 0)
        with open(out_path) as f:
            summary = json.load(f)
        self.assertIn("confidence", summary)
        self.assertIn("pass_rate_ci", summary["confidence"])

    def test_json_out_omits_confidence_without_ci_flag(self):
        path = self.make([{"id": "a", "score": 1.0}])
        out_fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)
        self._paths.append(out_path)
        proc = self.run_script(path, "--json-out", out_path)
        self.assertEqual(proc.returncode, 0)
        with open(out_path) as f:
            summary = json.load(f)
        self.assertNotIn("confidence", summary)

    def test_ci_prints_per_category_significance_section(self):
        # Two categories -- "accuracy" alone would be the sole category and
        # get skipped as a duplicate of the run-wide result (see
        # ComputePerCategoryConfidence.test_sole_category_is_skipped_as_
        # duplicate_of_run_wide); a second, stable category is what makes
        # this exercise the real multi-category path.
        base = self.make(
            [{"id": f"c{i}", "score": 1.0, "category": "accuracy"} for i in range(5)]
            + [{"id": f"s{i}", "score": 0.9, "category": "stable"} for i in range(3)]
        )
        cur = self.make(
            [{"id": f"c{i}", "score": 0.0, "category": "accuracy"} for i in range(5)]
            + [{"id": f"s{i}", "score": 0.9, "category": "stable"} for i in range(3)]
        )
        proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertIn("By category vs baseline", proc.stdout)
        self.assertIn("accuracy (n=5)", proc.stdout)
        self.assertIn("SIGNIFICANT REGRESSION", proc.stdout)

    def test_ci_reports_multiple_comparisons_correction(self):
        base = self.make([{"id": f"c{i}", "score": 1.0, "category": "accuracy"} for i in range(5)])
        cur = self.make([{"id": f"c{i}", "score": 0.0, "category": "accuracy"} for i in range(5)])
        proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertIn("Multiple-comparisons correction:", proc.stdout)
        self.assertIn("Benjamini-Hochberg (FDR)", proc.stdout)
        self.assertIn("Benjamini-Hochberg-corrected", proc.stdout)  # per-category section header

    def test_ci_without_baseline_reports_no_correction(self):
        # No baseline -> nothing significant is ever computed, so there's
        # nothing to correct for; the correction line should not appear.
        path = self.make([{"id": "a", "score": 1.0, "category": "x"}])
        proc = self.run_script(path, "--ci")
        self.assertNotIn("Multiple-comparisons correction:", proc.stdout)

    def test_json_out_includes_correction_metadata(self):
        # 2 run-wide + 2 accuracy + 2 stable (n=3 clears min_n=3) = 6.
        base = self.make(
            [{"id": f"c{i}", "score": 1.0, "category": "accuracy"} for i in range(5)]
            + [{"id": f"s{i}", "score": 0.9, "category": "stable"} for i in range(3)]
        )
        cur = self.make(
            [{"id": f"c{i}", "score": 0.0, "category": "accuracy"} for i in range(5)]
            + [{"id": f"s{i}", "score": 0.9, "category": "stable"} for i in range(3)]
        )
        out_fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)
        self._paths.append(out_path)
        proc = self.run_script(cur, "--baseline", base, "--ci", "--json-out", out_path)
        self.assertEqual(proc.returncode, 0)
        with open(out_path) as f:
            summary = json.load(f)
        self.assertIn("multiple_comparisons_correction", summary)
        self.assertEqual(summary["multiple_comparisons_correction"]["n_tests"], 6)

    def test_json_out_correction_metadata_excludes_sole_duplicate_category(self):
        # The single-category case: n_tests should be 2 (run-wide only),
        # not 4 -- the sole "accuracy" category is skipped as a duplicate,
        # not silently double-counted in the correction.
        base = self.make([{"id": f"c{i}", "score": 1.0, "category": "accuracy"} for i in range(5)])
        cur = self.make([{"id": f"c{i}", "score": 0.0, "category": "accuracy"} for i in range(5)])
        out_fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)
        self._paths.append(out_path)
        proc = self.run_script(cur, "--baseline", base, "--ci", "--json-out", out_path)
        self.assertEqual(proc.returncode, 0)
        with open(out_path) as f:
            summary = json.load(f)
        self.assertEqual(summary["multiple_comparisons_correction"]["n_tests"], 2)
        self.assertIn("skipped_reason", summary["per_category_confidence"]["accuracy"])

    def test_ci_shows_skipped_reason_for_tiny_category(self):
        base = self.make([{"id": "c0", "score": 1.0, "category": "rare"}, {"id": "c1", "score": 1.0, "category": "rare"}])
        cur = self.make([{"id": "c0", "score": 0.0, "category": "rare"}, {"id": "c1", "score": 0.0, "category": "rare"}])
        proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertIn("rare: skipped", proc.stdout)

    def test_ci_marks_significant_improvement_distinctly_from_regression(self):
        # Two categories -- "format" alone would be skipped as a duplicate
        # of the run-wide result (see ComputePerCategoryConfidence.
        # test_sole_category_is_skipped_as_duplicate_of_run_wide).
        base = self.make(
            [{"id": f"c{i}", "score": 0.5, "category": "format"} for i in range(6)]
            + [{"id": f"s{i}", "score": 0.9, "category": "stable"} for i in range(3)]
        )
        cur = self.make(
            [{"id": f"c{i}", "score": 1.0, "category": "format"} for i in range(6)]
            + [{"id": f"s{i}", "score": 0.9, "category": "stable"} for i in range(3)]
        )
        proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertIn("SIGNIFICANT IMPROVEMENT", proc.stdout)
        self.assertNotIn("SIGNIFICANT REGRESSION", proc.stdout)

    def test_no_per_category_section_without_baseline(self):
        path = self.make([{"id": "a", "score": 1.0, "category": "x"}])
        proc = self.run_script(path, "--ci")
        self.assertNotIn("By category vs baseline", proc.stdout)

    def test_fail_on_significant_regression_on_real_example_now_reflects_correction(self):
        # The exact real-world scenario examples/README.md documents, run
        # end-to-end as a subprocess against the actual worked-example
        # files: a regression concentrated in the `accuracy` category
        # (p=0.043 uncorrected), diluted below significance in the run-wide
        # aggregate. Before this correction existed, the per-category
        # signal alone was enough to fail this build. Weighed against the
        # other 9 tests examined in the same pass (Benjamini-Hochberg,
        # not Bonferroni -- see apply_multiple_comparisons_correction()'s
        # docstring for why), p=0.043 doesn't survive -- honest, intended
        # behavior: this example's evidence was never strong enough. See
        # test_fail_on_significant_regression_catches_decisive_category_
        # regression below for a case strong enough to survive correction.
        examples_dir = os.path.join(HERE, "..", "examples")
        cur = os.path.join(examples_dir, "results_regressed.jsonl")
        base = os.path.join(examples_dir, "results_baseline.jsonl")
        proc = self.run_script(cur, "--baseline", base, "--fail-on-significant-regression")
        self.assertEqual(proc.returncode, 0)

    def test_fail_on_significant_regression_catches_decisive_category_regression(self):
        # A decisive category regression -- the exact same data as
        # examples/README.md's second worked example. 4 of 5 `accuracy`
        # cases drop decisively (0.9 -> 0.3); three stable categories of 3
        # cases each (the min_n=3 floor exactly) are the dilution.
        #
        # Since the direction split (regression candidates and
        # non-regression candidates corrected as separate BH families —
        # see apply_multiple_comparisons_correction()'s docstring), the
        # run-wide aggregate is no longer diluted by the three unrelated
        # p=1.0 stable-category tests sharing its family: with those
        # excluded, the run-wide regression-candidate family shrinks to
        # just itself + accuracy's two diffs, and p=0.021 clears that
        # smaller family's threshold too. Both signals now correctly fire
        # -- a strictly better outcome than the pre-split version of this
        # same example, not a regression in what this test demonstrates.
        rows_current, rows_baseline = [], []
        for i, (c, b) in enumerate(zip([0.3, 0.3, 0.3, 0.3, 0.9], [0.9] * 5)):
            rows_current.append({"id": f"acc{i}", "score": c, "category": "accuracy"})
            rows_baseline.append({"id": f"acc{i}", "score": b, "category": "accuracy"})
        for cat in ("format", "grounding", "tool_use"):
            for i in range(3):
                rows_current.append({"id": f"{cat}{i}", "score": 0.9, "category": cat})
                rows_baseline.append({"id": f"{cat}{i}", "score": 0.9, "category": cat})
        cur = self.make(rows_current)
        base = self.make(rows_baseline)

        ci_proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertIn("SIGNIFICANT (after correction)", ci_proc.stdout)  # run-wide: no longer diluted
        self.assertIn("SIGNIFICANT REGRESSION", ci_proc.stdout)  # per-category: independently significant

        proc = self.run_script(cur, "--baseline", base, "--fail-on-significant-regression")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("category 'accuracy'", proc.stderr)

    def test_json_out_includes_per_category_confidence(self):
        base = self.make([{"id": f"c{i}", "score": 1.0, "category": "accuracy"} for i in range(5)])
        cur = self.make([{"id": f"c{i}", "score": 0.0, "category": "accuracy"} for i in range(5)])
        out_fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)
        self._paths.append(out_path)
        proc = self.run_script(cur, "--baseline", base, "--ci", "--json-out", out_path)
        self.assertEqual(proc.returncode, 0)
        with open(out_path) as f:
            summary = json.load(f)
        self.assertIn("per_category_confidence", summary)
        self.assertIn("accuracy", summary["per_category_confidence"])

    def test_json_out_ci_width_reliable_flag_round_trips(self):
        # A machine reading --json-out (a dashboard, a script comparing
        # runs) has no access to CI_LOW_RELIABILITY_N or print_report()'s
        # stdout warnings -- this flag is the only way it can learn that a
        # small-n CI's width, unlike its p-value, isn't trustworthy.
        base = (
            [{"id": f"a{i}", "score": 1.0, "category": "accuracy"} for i in range(5)]
            + [{"id": f"b{i}", "score": 1.0, "category": "format"} for i in range(20)]
        )
        cur = (
            [{"id": f"a{i}", "score": 0.0, "category": "accuracy"} for i in range(5)]
            + [{"id": f"b{i}", "score": 1.0, "category": "format"} for i in range(20)]
        )
        base_path = self.make(base)
        cur_path = self.make(cur)
        out_fd, out_path = tempfile.mkstemp(suffix=".json")
        os.close(out_fd)
        self._paths.append(out_path)
        proc = self.run_script(cur_path, "--baseline", base_path, "--ci", "--json-out", out_path)
        self.assertEqual(proc.returncode, 0)
        with open(out_path) as f:
            summary = json.load(f)
        # Run-wide n=25 (>= 20): reliable.
        self.assertTrue(summary["confidence"]["pass_rate_ci"]["ci_width_reliable"])
        self.assertTrue(summary["confidence"]["paired_pass_rate_diff"]["ci_width_reliable"])
        # accuracy category n=5 (< 20): not reliable.
        self.assertFalse(summary["per_category_confidence"]["accuracy"]["paired_pass_rate_diff"]["ci_width_reliable"])

    def test_ci_flag_warns_when_n_below_reliability_threshold(self):
        path = self.make([{"id": f"c{i}", "score": 1.0} for i in range(5)])
        proc = self.run_script(path, "--ci")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bootstrap CI width is not a reliable", proc.stdout)

    def test_ci_flag_no_width_warning_when_n_meets_reliability_threshold(self):
        path = self.make([{"id": f"c{i}", "score": 1.0} for i in range(20)])
        proc = self.run_script(path, "--ci")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("bootstrap CI width is not a reliable", proc.stdout)

    def test_paired_diff_warns_when_matched_n_below_reliability_threshold(self):
        base = self.make([{"id": f"c{i}", "score": 1.0} for i in range(5)])
        cur = self.make([{"id": f"c{i}", "score": 0.0} for i in range(5)])
        proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("this CI's width is not reliable either", proc.stdout)

    def test_paired_diff_no_width_warning_when_matched_n_meets_reliability_threshold(self):
        base = self.make([{"id": f"c{i}", "score": 1.0} for i in range(20)])
        cur = self.make([{"id": f"c{i}", "score": 0.0} for i in range(20)])
        proc = self.run_script(cur, "--baseline", base, "--ci")
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("this CI's width is not reliable either", proc.stdout)

    def test_per_category_ci_marks_category_below_reliability_threshold(self):
        # 3 is the min_n floor for running the significance test at all, and
        # well below CI_LOW_RELIABILITY_N=20 — exactly the regime the finding
        # says was invisible: a category CI running at a legitimate, sanctioned
        # n that's still too small for its own width to mean anything.
        base = (
            [{"id": f"a{i}", "score": 1.0, "category": "accuracy"} for i in range(3)]
            + [{"id": f"b{i}", "score": 1.0, "category": "format"} for i in range(3)]
        )
        cur = (
            [{"id": f"a{i}", "score": 0.0, "category": "accuracy"} for i in range(3)]
            + [{"id": f"b{i}", "score": 1.0, "category": "format"} for i in range(3)]
        )
        base_path = self.make(base)
        cur_path = self.make(cur)
        proc = self.run_script(cur_path, "--baseline", base_path, "--ci")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("marks a category below n=20", proc.stdout)
        self.assertIn("accuracy (n=3) ⚠", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
