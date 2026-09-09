#!/usr/bin/env python3
"""Tests for qa_gate_history.py — real temp directories for the
filesystem-facing functions (same discipline as test_source_health_report.py:
this module's whole job is reading real files, so faking that would test
less than a real, disposable tmp dir does), pure-dict fixtures for the
flattening logic itself.

Run: python test_qa_gate_history.py"""

import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qa_gate_history = load("qa_gate_history")
qa_gate = load("qa_gate")


def make_report(data_dir, iso_date, qa_checks=None):
    d = os.path.join(data_dir, "episodes", iso_date)
    os.makedirs(d, exist_ok=True)
    report = {"run_date": iso_date}
    if qa_checks is not None:
        report["qa_checks"] = qa_checks
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f)


class LoadQaChecks(unittest.TestCase):
    def test_missing_report_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(qa_gate_history.load_qa_checks(tmp, "2026-09-02"))

    def test_report_without_qa_checks_returns_none(self):
        # An older episode run from before this field existed.
        with tempfile.TemporaryDirectory() as tmp:
            make_report(tmp, "2026-09-02", qa_checks=None)
            self.assertIsNone(qa_gate_history.load_qa_checks(tmp, "2026-09-02"))

    def test_malformed_json_returns_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "episodes", "2026-09-02")
            os.makedirs(d)
            with open(os.path.join(d, "report.json"), "w") as f:
                f.write("{not valid json")
            self.assertIsNone(qa_gate_history.load_qa_checks(tmp, "2026-09-02"))

    def test_real_qa_checks_returned(self):
        checks = [{"check": "has_intro", "passed": True, "detail": ""}]
        with tempfile.TemporaryDirectory() as tmp:
            make_report(tmp, "2026-09-02", qa_checks=checks)
            self.assertEqual(qa_gate_history.load_qa_checks(tmp, "2026-09-02"), checks)


class FlattenQaChecks(unittest.TestCase):
    def test_passing_check_scores_one(self):
        checks = [{"check": "has_intro", "passed": True, "detail": ""}]
        rows = qa_gate_history.flatten_qa_checks("2026-09-02", checks)
        self.assertEqual(rows, [{"id": "2026-09-02__has_intro", "score": 1.0, "category": "has_intro", "rationale": ""}])

    def test_failing_check_scores_zero_with_detail(self):
        checks = [{"check": "no_empty_text", "passed": False, "detail": "empty narration text in segment(s): ['outro']"}]
        rows = qa_gate_history.flatten_qa_checks("2026-09-02", checks)
        self.assertEqual(rows[0]["score"], 0.0)
        self.assertEqual(rows[0]["rationale"], "empty narration text in segment(s): ['outro']")

    def test_multiple_checks_produce_multiple_rows_with_unique_ids(self):
        checks = [
            {"check": "has_intro", "passed": True, "detail": ""},
            {"check": "has_outro", "passed": True, "detail": ""},
        ]
        rows = qa_gate_history.flatten_qa_checks("2026-09-02", checks)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["id"] for r in rows}, {"2026-09-02__has_intro", "2026-09-02__has_outro"})

    def test_empty_checks_returns_empty_list(self):
        self.assertEqual(qa_gate_history.flatten_qa_checks("2026-09-02", []), [])

    def test_against_a_real_qa_gate_output(self):
        # Real integration point: flatten_qa_checks() against qa_gate.py's
        # own actual return shape, not a hand-typed fixture that could
        # silently drift from what gate() really produces.
        script = {
            "segments": [
                {"segment_type": "intro", "text": "hello", "canonical_id": None, "claim_id": None, "source_id": None},
                {"segment_type": "outro", "text": "bye", "canonical_id": None, "claim_id": None, "source_id": None},
            ],
            "excluded_no_evidence": [],
        }
        result = qa_gate.gate(script)
        rows = qa_gate_history.flatten_qa_checks("2026-09-02", result["checks"])
        self.assertEqual(len(rows), len(result["checks"]))
        self.assertTrue(all(r["score"] in (0.0, 1.0) for r in rows))


class MainCli(unittest.TestCase):
    """Real end-to-end: writes real report.json files, runs the real
    main(), only mocking sys.argv."""

    def test_writes_flattened_rows_across_episodes(self):
        import sys
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            make_report(tmp, "2026-09-01", qa_checks=[{"check": "has_intro", "passed": True, "detail": ""}])
            make_report(tmp, "2026-09-02", qa_checks=[{"check": "has_intro", "passed": False, "detail": "missing"}])
            out_path = os.path.join(tmp, "out.jsonl")

            argv = ["qa_gate_history.py", "--data-dir", tmp, "--date", "2026-09-02", "--days", "7", "--out", out_path]
            with mock.patch.object(sys, "argv", argv):
                exit_code = qa_gate_history.main()

            self.assertEqual(exit_code, 0)
            with open(out_path) as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["id"] for r in rows}, {"2026-09-01__has_intro", "2026-09-02__has_intro"})

    def test_window_filtering_excludes_reports_outside_the_days_argument(self):
        import sys
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            make_report(tmp, "2026-08-01", qa_checks=[{"check": "has_intro", "passed": True, "detail": ""}])  # outside a 7-day window from 2026-09-02
            make_report(tmp, "2026-09-01", qa_checks=[{"check": "has_intro", "passed": True, "detail": ""}])  # inside
            out_path = os.path.join(tmp, "out.jsonl")

            argv = ["qa_gate_history.py", "--data-dir", tmp, "--date", "2026-09-02", "--days", "7", "--out", out_path]
            with mock.patch.object(sys, "argv", argv):
                qa_gate_history.main()

            with open(out_path) as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual([r["id"] for r in rows], ["2026-09-01__has_intro"])

    def test_no_data_writes_empty_file_and_reports_plainly(self):
        import io
        import sys
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "out.jsonl")
            argv = ["qa_gate_history.py", "--data-dir", tmp, "--date", "2026-09-02", "--days", "30", "--out", out_path]
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stdout", stdout):
                exit_code = qa_gate_history.main()

            self.assertEqual(exit_code, 0)
            self.assertIn("No episodes", stdout.getvalue())
            self.assertTrue(os.path.isfile(out_path))
            with open(out_path) as f:
                self.assertEqual(f.read(), "")

    def test_output_is_valid_score_eval_schema_and_score_eval_accepts_it(self):
        # Real cross-tool check: feed the written file into agent-eval's
        # actual score_eval.py and confirm it loads without warnings.
        # This is a test-time-only check of the documented file-format
        # handoff between two independent skills (see qa_gate_history.py's
        # own docstring on why it's a file format, not a cross-skill
        # import) — skipped, not failed, if agent-eval isn't present
        # alongside broadcast, since a skill in this repo may be copied
        # out and used standalone elsewhere.
        import subprocess
        import sys as sys_mod
        from unittest import mock

        score_eval_path = os.path.join(HERE, "..", "..", "agent-eval", "scripts", "score_eval.py")
        if not os.path.isfile(score_eval_path):
            self.skipTest("agent-eval not present alongside broadcast in this checkout")

        with tempfile.TemporaryDirectory() as tmp:
            make_report(tmp, "2026-09-01", qa_checks=[
                {"check": "has_intro", "passed": True, "detail": ""},
                {"check": "has_outro", "passed": False, "detail": "missing outro"},
            ])
            out_path = os.path.join(tmp, "out.jsonl")
            argv = ["qa_gate_history.py", "--data-dir", tmp, "--date", "2026-09-01", "--days", "1", "--out", out_path]
            with mock.patch.object(sys_mod, "argv", argv):
                qa_gate_history.main()

            score_eval_path = os.path.join(HERE, "..", "..", "agent-eval", "scripts", "score_eval.py")
            proc = subprocess.run([sys_mod.executable, score_eval_path, out_path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Eval Report", proc.stdout)
            self.assertIn("Cases: 2", proc.stdout)


if __name__ == "__main__":
    unittest.main()
