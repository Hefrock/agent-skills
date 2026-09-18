#!/usr/bin/env python3
"""Unit + CLI tests for track_findings.py. Stdlib only (unittest). Run:

    python test_track_findings.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "track_findings.py")

spec = importlib.util.spec_from_file_location("track_findings", SCRIPT)
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)


def run_script(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


class IsValidFingerprint(unittest.TestCase):
    def test_accepts_typical_fingerprint(self):
        self.assertTrue(tf.is_valid_fingerprint("wiki-warehouse:aspirational:bin-intake-external-repo"))

    def test_rejects_empty_string(self):
        self.assertFalse(tf.is_valid_fingerprint(""))

    def test_rejects_leading_non_alnum(self):
        self.assertFalse(tf.is_valid_fingerprint(":leading-colon"))
        self.assertFalse(tf.is_valid_fingerprint("-leading-dash"))

    def test_rejects_disallowed_characters(self):
        self.assertFalse(tf.is_valid_fingerprint("has a space"))
        self.assertFalse(tf.is_valid_fingerprint("has#hash"))
        self.assertFalse(tf.is_valid_fingerprint("has\"quote"))

    def test_accepts_all_allowed_punctuation(self):
        self.assertTrue(tf.is_valid_fingerprint("a.b:c/d@e-f_g"))


class ComputeFileHash(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, name, content):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, "w") as f:
            f.write(content)
        return name

    def test_same_content_same_hash(self):
        self._write("a.md", "hello")
        self._write("b.md", "hello")
        self.assertEqual(
            tf.compute_file_hash(self.tmpdir.name, "a.md"),
            tf.compute_file_hash(self.tmpdir.name, "b.md"),
        )

    def test_different_content_different_hash(self):
        self._write("a.md", "hello")
        self._write("b.md", "goodbye")
        self.assertNotEqual(
            tf.compute_file_hash(self.tmpdir.name, "a.md"),
            tf.compute_file_hash(self.tmpdir.name, "b.md"),
        )

    def test_missing_file_returns_none(self):
        self.assertIsNone(tf.compute_file_hash(self.tmpdir.name, "does-not-exist.md"))

    def test_changed_content_changes_hash(self):
        name = self._write("a.md", "v1")
        h1 = tf.compute_file_hash(self.tmpdir.name, name)
        self._write("a.md", "v2")
        h2 = tf.compute_file_hash(self.tmpdir.name, name)
        self.assertNotEqual(h1, h2)


class LedgerRoundTrip(unittest.TestCase):
    def test_load_missing_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(tf.load_ledger(os.path.join(tmp, "nope.json")), [])

    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.json")
            records = [{"fingerprint": "b:1", "verdict": "drift"}, {"fingerprint": "a:1", "verdict": "silent"}]
            tf.save_ledger(path, records)
            loaded = tf.load_ledger(path)
            self.assertEqual(len(loaded), 2)

    def test_save_sorts_by_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.json")
            tf.save_ledger(path, [{"fingerprint": "z:1"}, {"fingerprint": "a:1"}])
            with open(path) as f:
                data = json.load(f)
            self.assertEqual([r["fingerprint"] for r in data], ["a:1", "z:1"])


class UpsertFinding(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = self.tmpdir.name
        with open(os.path.join(self.repo_root, "claim.md"), "w") as f:
            f.write("some claim text")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_adds_new_finding_with_computed_hash(self):
        records = tf.upsert_finding([], "x:drift:1", "drift", "Title", ["claim.md"], self.repo_root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["fingerprint"], "x:drift:1")
        self.assertEqual(records[0]["source_refs"][0]["file"], "claim.md")
        self.assertIsNotNone(records[0]["source_refs"][0]["hash"])

    def test_upsert_same_fingerprint_replaces_not_duplicates(self):
        records = tf.upsert_finding([], "x:drift:1", "drift", "First title", ["claim.md"], self.repo_root)
        records = tf.upsert_finding(records, "x:drift:1", "silent", "Second title", ["claim.md"], self.repo_root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "Second title")
        self.assertEqual(records[0]["verdict"], "silent")

    def test_rejects_invalid_fingerprint(self):
        with self.assertRaises(ValueError):
            tf.upsert_finding([], "bad fingerprint", "drift", "T", ["claim.md"], self.repo_root)

    def test_rejects_invalid_verdict(self):
        with self.assertRaises(ValueError):
            tf.upsert_finding([], "x:1", "not-a-real-verdict", "T", ["claim.md"], self.repo_root)

    def test_rejects_empty_source_refs(self):
        with self.assertRaises(ValueError):
            tf.upsert_finding([], "x:1", "drift", "T", [], self.repo_root)

    def test_rejects_nonexistent_source_ref(self):
        with self.assertRaises(ValueError):
            tf.upsert_finding([], "x:1", "drift", "T", ["does-not-exist.md"], self.repo_root)


class CheckLedger(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = self.tmpdir.name
        with open(os.path.join(self.repo_root, "claim.md"), "w") as f:
            f.write("original content")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_unchanged_file_reports_unchanged(self):
        records = tf.upsert_finding([], "x:1", "drift", "T", ["claim.md"], self.repo_root)
        report = tf.check_ledger(records, self.repo_root)
        self.assertEqual(len(report["unchanged"]), 1)
        self.assertEqual(report["changed"], [])
        self.assertEqual(report["missing_source"], [])

    def test_modified_file_reports_changed(self):
        records = tf.upsert_finding([], "x:1", "drift", "T", ["claim.md"], self.repo_root)
        with open(os.path.join(self.repo_root, "claim.md"), "w") as f:
            f.write("different content now")
        report = tf.check_ledger(records, self.repo_root)
        self.assertEqual(report["unchanged"], [])
        self.assertEqual(len(report["changed"]), 1)
        self.assertIn("content changed", report["changed"][0]["check_reasons"][0])

    def test_deleted_file_reports_missing_source(self):
        records = tf.upsert_finding([], "x:1", "drift", "T", ["claim.md"], self.repo_root)
        os.remove(os.path.join(self.repo_root, "claim.md"))
        report = tf.check_ledger(records, self.repo_root)
        self.assertEqual(report["unchanged"], [])
        self.assertEqual(report["changed"], [])
        self.assertEqual(len(report["missing_source"]), 1)
        self.assertIn("no longer exists", report["missing_source"][0]["check_reasons"][0])

    def test_missing_takes_priority_over_changed_across_multiple_refs(self):
        with open(os.path.join(self.repo_root, "second.md"), "w") as f:
            f.write("second file")
        records = tf.upsert_finding([], "x:1", "drift", "T", ["claim.md", "second.md"], self.repo_root)
        with open(os.path.join(self.repo_root, "second.md"), "w") as f:
            f.write("second file changed")
        os.remove(os.path.join(self.repo_root, "claim.md"))
        report = tf.check_ledger(records, self.repo_root)
        self.assertEqual(len(report["missing_source"]), 1)
        self.assertEqual(report["changed"], [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = self.tmpdir.name
        self.ledger_path = os.path.join(self.tmpdir.name, "ledger.json")
        with open(os.path.join(self.repo_root, "claim.md"), "w") as f:
            f.write("claim text")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_upsert_then_check_round_trip(self):
        r = run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root,
                        "upsert", "--fingerprint", "x:drift:1", "--verdict", "drift",
                        "--title", "A finding", "--source-ref", "claim.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(self.ledger_path))

        r = run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root, "check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data["unchanged"]), 1)
        self.assertEqual(data["unchanged"][0]["fingerprint"], "x:drift:1")

    def test_upsert_rejects_invalid_verdict_at_cli_level(self):
        r = run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root,
                        "upsert", "--fingerprint", "x:1", "--verdict", "not-real",
                        "--title", "T", "--source-ref", "claim.md")
        self.assertNotEqual(r.returncode, 0)

    def test_upsert_missing_source_ref_errors_cleanly(self):
        r = run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root,
                        "upsert", "--fingerprint", "x:1", "--verdict", "drift",
                        "--title", "T", "--source-ref", "nonexistent.md")
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not exist", r.stderr)

    def test_check_on_empty_ledger_reports_zero(self):
        r = run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root, "check", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data, {"unchanged": [], "changed": [], "missing_source": []})

    def test_changed_file_detected_through_full_cli_round_trip(self):
        run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root,
                   "upsert", "--fingerprint", "x:1", "--verdict", "silent",
                   "--title", "T", "--source-ref", "claim.md")
        with open(os.path.join(self.repo_root, "claim.md"), "w") as f:
            f.write("edited")
        r = run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root, "check", "--json")
        data = json.loads(r.stdout)
        self.assertEqual(len(data["changed"]), 1)

    def test_reupsert_updates_in_place_via_cli(self):
        run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root,
                   "upsert", "--fingerprint", "x:1", "--verdict", "drift",
                   "--title", "First", "--source-ref", "claim.md")
        run_script("--ledger-file", self.ledger_path, "--repo-root", self.repo_root,
                   "upsert", "--fingerprint", "x:1", "--verdict", "confirmed",
                   "--title", "Second", "--source-ref", "claim.md")
        with open(self.ledger_path) as f:
            data = json.load(f)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "Second")
        self.assertEqual(data[0]["verdict"], "confirmed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
