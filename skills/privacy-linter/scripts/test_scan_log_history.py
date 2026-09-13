#!/usr/bin/env python3
"""Tests for scan_log_history.py — real temp directories for the
filesystem-facing functions (this module's whole job is reading real log
files, same discipline as broadcast/scripts/test_qa_gate_history.py), pure-
dict fixtures for the flattening logic itself.

Run: python test_scan_log_history.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "scan_log_history.py")

spec = importlib.util.spec_from_file_location("scan_log_history", SCRIPT)
scan_log_history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan_log_history)


def write_log(log_dir, timestamp, findings):
    os.makedirs(log_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".json", dir=log_dir)
    with os.fdopen(fd, "w") as f:
        json.dump({"timestamp": timestamp, "findings": findings}, f)
    return path


def finding(severity="high", leak_class="secret", label="x"):
    return {"severity": severity, "leak_class": leak_class, "finding": label, "reason": "matched", "location": "note.md:1"}


class LoadRunLogs(unittest.TestCase):
    def test_reads_a_run_within_the_window(self):
        with tempfile.TemporaryDirectory() as d:
            now = datetime.now(timezone.utc)
            write_log(d, now.isoformat(), [])
            runs = scan_log_history.load_run_logs(d, now - timedelta(days=1))
            self.assertEqual(len(runs), 1)

    def test_excludes_a_run_before_the_cutoff(self):
        with tempfile.TemporaryDirectory() as d:
            old = datetime.now(timezone.utc) - timedelta(days=60)
            write_log(d, old.isoformat(), [])
            runs = scan_log_history.load_run_logs(d, datetime.now(timezone.utc) - timedelta(days=30))
            self.assertEqual(runs, [])

    def test_malformed_log_file_skipped_not_crashed(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "bad.json"), "w") as f:
                f.write("{not valid json")
            now = datetime.now(timezone.utc)
            write_log(d, now.isoformat(), [])
            runs = scan_log_history.load_run_logs(d, now - timedelta(days=1))
            self.assertEqual(len(runs), 1)

    def test_missing_timestamp_field_skipped_not_crashed(self):
        with tempfile.TemporaryDirectory() as d:
            fd, path = tempfile.mkstemp(suffix=".json", dir=d)
            with os.fdopen(fd, "w") as f:
                json.dump({"findings": []}, f)
            runs = scan_log_history.load_run_logs(d, datetime.now(timezone.utc) - timedelta(days=1))
            self.assertEqual(runs, [])

    def test_empty_dir_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(scan_log_history.load_run_logs(d, datetime.now(timezone.utc)), [])


class FlattenRun(unittest.TestCase):
    def test_clean_run_all_checks_pass(self):
        record = {"timestamp": "2026-09-13T00:00:00+00:00", "findings": []}
        rows = scan_log_history.flatten_run(record)
        self.assertEqual(len(rows), len(scan_log_history.DERIVED_CHECKS))
        for row in rows:
            self.assertEqual(row["score"], 1.0)
            self.assertEqual(row["rationale"], "no matching findings")

    def test_secret_finding_fails_no_secrets_and_clean_only(self):
        record = {"timestamp": "2026-09-13T00:00:00+00:00", "findings": [finding(leak_class="secret", severity="high")]}
        rows = {row["category"]: row for row in scan_log_history.flatten_run(record)}
        self.assertEqual(rows["clean"]["score"], 0.0)
        self.assertEqual(rows["no_secrets"]["score"], 0.0)
        self.assertEqual(rows["no_high_severity"]["score"], 0.0)
        self.assertEqual(rows["no_direct_pii"]["score"], 1.0)  # unaffected — it's a secret, not PII

    def test_low_severity_pii_only_fails_clean_and_pii_not_secrets_or_high(self):
        record = {"timestamp": "2026-09-13T00:00:00+00:00", "findings": [finding(leak_class="direct_pii", severity="low")]}
        rows = {row["category"]: row for row in scan_log_history.flatten_run(record)}
        self.assertEqual(rows["clean"]["score"], 0.0)
        self.assertEqual(rows["no_direct_pii"]["score"], 0.0)
        self.assertEqual(rows["no_secrets"]["score"], 1.0)
        self.assertEqual(rows["no_high_severity"]["score"], 1.0)

    def test_id_includes_timestamp_and_check_name(self):
        record = {"timestamp": "2026-09-13T00:00:00+00:00", "findings": []}
        rows = scan_log_history.flatten_run(record)
        for row in rows:
            self.assertTrue(row["id"].startswith("2026-09-13T00:00:00+00:00__"))

    def test_rationale_lists_finding_labels_when_failing(self):
        record = {"timestamp": "2026-09-13T00:00:00+00:00", "findings": [finding(label="AWS access key ID: aws_access_key")]}
        rows = {row["category"]: row for row in scan_log_history.flatten_run(record)}
        self.assertIn("AWS access key ID", rows["clean"]["rationale"])

    def test_rationale_truncates_beyond_three_findings(self):
        findings = [finding(label=f"finding {i}") for i in range(5)]
        record = {"timestamp": "2026-09-13T00:00:00+00:00", "findings": findings}
        rows = {row["category"]: row for row in scan_log_history.flatten_run(record)}
        self.assertIn("+2 more", rows["clean"]["rationale"])


class MainCli(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_writes_flattened_jsonl(self):
        with tempfile.TemporaryDirectory() as log_dir, tempfile.TemporaryDirectory() as out_dir:
            write_log(log_dir, datetime.now(timezone.utc).isoformat(), [])
            out_path = os.path.join(out_dir, "trend.jsonl")
            proc = self.run_script("--log-dir", log_dir, "--out", out_path)
            self.assertEqual(proc.returncode, 0)
            with open(out_path) as f:
                lines = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(lines), len(scan_log_history.DERIVED_CHECKS))

    def test_no_logs_in_window_writes_empty_file(self):
        with tempfile.TemporaryDirectory() as log_dir, tempfile.TemporaryDirectory() as out_dir:
            out_path = os.path.join(out_dir, "trend.jsonl")
            proc = self.run_script("--log-dir", log_dir, "--out", out_path)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("No run logs found", proc.stdout)
            with open(out_path) as f:
                self.assertEqual(f.read(), "")

    def test_days_window_excludes_old_runs(self):
        with tempfile.TemporaryDirectory() as log_dir, tempfile.TemporaryDirectory() as out_dir:
            old = datetime.now(timezone.utc) - timedelta(days=90)
            write_log(log_dir, old.isoformat(), [])
            out_path = os.path.join(out_dir, "trend.jsonl")
            proc = self.run_script("--log-dir", log_dir, "--out", out_path, "--days", "30")
            self.assertIn("No run logs found", proc.stdout)

    def test_output_is_consumable_by_score_eval(self):
        # End-to-end proof this is a real bridge, not just a schema on paper:
        # feed the written JSONL straight into agent-eval's score_eval.py.
        score_eval = os.path.normpath(os.path.join(HERE, "..", "..", "agent-eval", "scripts", "score_eval.py"))
        with tempfile.TemporaryDirectory() as log_dir, tempfile.TemporaryDirectory() as out_dir:
            write_log(log_dir, datetime.now(timezone.utc).isoformat(), [finding(leak_class="secret")])
            out_path = os.path.join(out_dir, "trend.jsonl")
            self.run_script("--log-dir", log_dir, "--out", out_path)
            proc = subprocess.run([sys.executable, score_eval, out_path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Eval Report", proc.stdout)
            self.assertIn("By category", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
