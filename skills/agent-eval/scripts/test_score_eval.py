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


class CheckGates(unittest.TestCase):
    def _summary(self, pass_rate):
        return {"pass_rate": pass_rate, "total": 10}

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
