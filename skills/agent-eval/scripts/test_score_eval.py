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


if __name__ == "__main__":
    unittest.main(verbosity=2)
