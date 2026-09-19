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
        regs = [{"id": "a", "baseline_score": 1.0, "current_score": 0.0}]
        failures = score_eval.check_gates(self._summary(1.0), regs, None, True)
        self.assertEqual(len(failures), 1)

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
        sig_reg = {"point_a": 0.5, "point_b": 0.9, "diff": -0.4, "p_value": 0.01, "significant_at_0.05": True, "n": 20}
        failures = score_eval.check_gates(self._summary(0.5), [], None, False, significant_regression=sig_reg)
        self.assertEqual(len(failures), 1)
        self.assertIn("significant", failures[0])

    def test_not_significant_does_not_trigger(self):
        not_sig = {"point_a": 0.8, "point_b": 0.9, "diff": -0.1, "p_value": 0.42, "significant_at_0.05": False, "n": 20}
        self.assertEqual(score_eval.check_gates(self._summary(0.8), [], None, False, significant_regression=not_sig), [])

    def test_significant_improvement_does_not_trigger(self):
        # diff > 0 means current beat baseline -- significant in the GOOD
        # direction should never fail a build.
        improved = {"point_a": 0.95, "point_b": 0.6, "diff": 0.35, "p_value": 0.01, "significant_at_0.05": True, "n": 20}
        self.assertEqual(score_eval.check_gates(self._summary(0.95), [], None, False, significant_regression=improved), [])

    def test_none_significant_regression_does_not_trigger(self):
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, significant_regression=None), [])

    def test_significant_score_regression_triggers(self):
        sig_reg = {"point_a": 0.71, "point_b": 1.0, "diff": -0.29, "p_value": 0.0, "significant_at_0.05": True, "n": 15}
        failures = score_eval.check_gates(self._summary(1.0), [], None, False, significant_score_regression=sig_reg)
        self.assertEqual(len(failures), 1)
        self.assertIn("mean score", failures[0])

    def test_not_significant_score_regression_does_not_trigger(self):
        not_sig = {"point_a": 0.84, "point_b": 0.89, "diff": -0.05, "p_value": 0.25, "significant_at_0.05": False, "n": 20}
        self.assertEqual(score_eval.check_gates(self._summary(0.8), [], None, False, significant_score_regression=not_sig), [])

    def test_significant_score_improvement_does_not_trigger(self):
        improved = {"point_a": 0.95, "point_b": 0.6, "diff": 0.35, "p_value": 0.01, "significant_at_0.05": True, "n": 20}
        self.assertEqual(score_eval.check_gates(self._summary(0.95), [], None, False, significant_score_regression=improved), [])

    def test_none_significant_score_regression_does_not_trigger(self):
        self.assertEqual(score_eval.check_gates(self._summary(1.0), [], None, False, significant_score_regression=None), [])

    def test_both_significant_pass_rate_and_score_regression_reported_separately(self):
        # A case that trips both signals should surface both failure
        # messages, not collapse into one -- each is independently
        # actionable evidence.
        pass_rate_reg = {"point_a": 0.5, "point_b": 0.9, "diff": -0.4, "p_value": 0.01, "significant_at_0.05": True, "n": 20}
        score_reg = {"point_a": 0.4, "point_b": 0.85, "diff": -0.45, "p_value": 0.0, "significant_at_0.05": True, "n": 20}
        failures = score_eval.check_gates(
            self._summary(0.5), [], None, False,
            significant_regression=pass_rate_reg, significant_score_regression=score_reg,
        )
        self.assertEqual(len(failures), 2)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
