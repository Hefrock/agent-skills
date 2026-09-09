#!/usr/bin/env python3
"""
Unit tests for calibrate_judge.py — SKILL.md step 5's calibration step,
which references/llm-judge-prompt.md's own log shows has never actually
been run on a real eval before this script existed.

Covers score loading, judge-vs-human delta computation (including the
"only compare the intersection of IDs" behavior), threshold-based
summarization, and the calibration-log markdown update (both the first-
ever calibration, replacing the placeholder row, and a later one,
appending to real history) against a real temp file — same discipline as
test_prune_episodes.py/test_source_health_report.py: this module's job is
reading and rewriting a real file, so a real temp file tests more than a
mock would.

Stdlib only (unittest). Run: python test_calibrate_judge.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "calibrate_judge.py")

spec = importlib.util.spec_from_file_location("calibrate_judge", SCRIPT)
cal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cal)

REAL_LOG_TEMPLATE = """# LLM-as-Judge Prompt Template

Some preamble text.

## Calibration log

<!-- Update after each spot-check -->
| Date | Cases checked | Mean delta vs human | Action taken |
|---|---|---|---|
| — | — | — | not yet calibrated |

## Known biases to guard against

- Some bias.
"""


def write_jsonl(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


class LoadScoresById(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def load(self, rows):
        path = write_jsonl(rows)
        self._paths.append(path)
        return cal.load_scores_by_id(path)

    def test_loads_id_to_score(self):
        scores = self.load([{"id": "a", "score": 0.8}, {"id": "b", "score": 0.5}])
        self.assertEqual(scores, {"a": 0.8, "b": 0.5})

    def test_missing_score_skipped(self):
        scores = self.load([{"id": "a"}, {"id": "b", "score": 0.5}])
        self.assertEqual(scores, {"b": 0.5})

    def test_int_score_coerced_to_float(self):
        scores = self.load([{"id": "a", "score": 1}])
        self.assertIsInstance(scores["a"], float)


class ComputeDeltas(unittest.TestCase):
    def test_intersection_only(self):
        judge = {"a": 0.9, "b": 0.5, "c": 0.2}
        human = {"a": 1.0, "b": 0.5}  # no "c" — human didn't hand-score it
        deltas = cal.compute_deltas(judge, human)
        self.assertEqual({r["id"] for r in deltas}, {"a", "b"})

    def test_delta_is_absolute_value(self):
        deltas = cal.compute_deltas({"a": 0.2}, {"a": 0.9})
        self.assertAlmostEqual(deltas[0]["delta"], 0.7)

    def test_sorted_worst_agreement_first(self):
        judge = {"a": 0.9, "b": 0.5, "c": 1.0}
        human = {"a": 0.9, "b": 0.0, "c": 0.9}
        deltas = cal.compute_deltas(judge, human)
        self.assertEqual([r["id"] for r in deltas], ["b", "c", "a"])

    def test_no_overlap_returns_empty(self):
        self.assertEqual(cal.compute_deltas({"a": 1.0}, {"b": 1.0}), [])


class SummarizeCalibration(unittest.TestCase):
    def test_empty_returns_none_fields(self):
        s = cal.summarize_calibration([], 0.2)
        self.assertEqual(s["cases_checked"], 0)
        self.assertIsNone(s["mean_delta"])

    def test_mean_delta_computed(self):
        deltas = [{"delta": 0.1}, {"delta": 0.3}]
        s = cal.summarize_calibration(deltas, 0.2)
        self.assertAlmostEqual(s["mean_delta"], 0.2)

    def test_over_threshold_true_when_exceeded(self):
        s = cal.summarize_calibration([{"delta": 0.5}], 0.2)
        self.assertTrue(s["over_threshold"])

    def test_over_threshold_false_at_boundary(self):
        # Strictly greater-than, matching SKILL.md's "exceeds 0.2" wording.
        s = cal.summarize_calibration([{"delta": 0.2}], 0.2)
        self.assertFalse(s["over_threshold"])


class UpdateCalibrationLog(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write(REAL_LOG_TEMPLATE)

    def tearDown(self):
        os.unlink(self.path)

    def test_first_calibration_replaces_placeholder(self):
        cal.update_calibration_log(self.path, "| 2026-09-09 | 8 | 0.120 | none needed |")
        with open(self.path) as f:
            content = f.read()
        self.assertNotIn("not yet calibrated", content)
        self.assertIn("| 2026-09-09 | 8 | 0.120 | none needed |", content)
        # Preamble and the section after the table survive untouched.
        self.assertIn("Some preamble text.", content)
        self.assertIn("Known biases to guard against", content)

    def test_second_calibration_appends_after_first(self):
        cal.update_calibration_log(self.path, "| 2026-09-09 | 8 | 0.120 | none needed |")
        cal.update_calibration_log(self.path, "| 2026-10-01 | 6 | 0.250 | revised judge prompt |")
        with open(self.path) as f:
            content = f.read()
        lines = content.splitlines()
        idx_1 = lines.index("| 2026-09-09 | 8 | 0.120 | none needed |")
        idx_2 = lines.index("| 2026-10-01 | 6 | 0.250 | revised judge prompt |")
        self.assertEqual(idx_2, idx_1 + 1)  # both rows present, in order, contiguous

    def test_missing_table_header_raises_clear_error(self):
        fd, bad_path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write("# Some doc with no calibration table\n")
        try:
            with self.assertRaises(ValueError):
                cal.update_calibration_log(bad_path, "| x | x | x | x |")
        finally:
            os.unlink(bad_path)


class Cli(unittest.TestCase):
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
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_within_threshold_exits_zero(self):
        judge = self.make([{"id": "a", "score": 0.8}, {"id": "b", "score": 0.5}])
        human = self.make([{"id": "a", "score": 0.85}, {"id": "b", "score": 0.55}])
        proc = self.run_script(judge, human)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("OK:", proc.stdout)

    def test_over_threshold_exits_nonzero(self):
        judge = self.make([{"id": "a", "score": 1.0}])
        human = self.make([{"id": "a", "score": 0.0}])
        proc = self.run_script(judge, human)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("ACTION NEEDED", proc.stdout)

    def test_no_overlap_exits_nonzero_with_clear_message(self):
        judge = self.make([{"id": "a", "score": 1.0}])
        human = self.make([{"id": "b", "score": 1.0}])
        proc = self.run_script(judge, human)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("nothing to calibrate", proc.stderr)

    def test_update_log_end_to_end(self):
        judge = self.make([{"id": "a", "score": 0.8}])
        human = self.make([{"id": "a", "score": 0.8}])
        fd, log_path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write(REAL_LOG_TEMPLATE)
        self._paths.append(log_path)

        proc = self.run_script(judge, human, "--update-log", log_path)
        self.assertEqual(proc.returncode, 0)
        with open(log_path) as f:
            content = f.read()
        self.assertNotIn("not yet calibrated", content)
        self.assertIn("1 |", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
