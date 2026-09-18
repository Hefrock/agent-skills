#!/usr/bin/env python3
"""Unit + CLI tests for find_closing_references.py.

Stdlib only (unittest). Run: python test_find_closing_references.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "find_closing_references.py")

spec = importlib.util.spec_from_file_location("find_closing_references", SCRIPT)
fcr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fcr)


def run_script(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


class FindReferencedIssueNumbers(unittest.TestCase):
    def test_closes_keyword(self):
        self.assertEqual(fcr.find_referenced_issue_numbers("Closes #6"), [6])

    def test_fixes_keyword(self):
        self.assertEqual(fcr.find_referenced_issue_numbers("Fixes #12"), [12])

    def test_resolved_past_tense(self):
        self.assertEqual(fcr.find_referenced_issue_numbers("This resolved #3 for good"), [3])

    def test_case_insensitive(self):
        self.assertEqual(fcr.find_referenced_issue_numbers("CLOSES #6"), [6])

    def test_fix_colon_form(self):
        self.assertEqual(fcr.find_referenced_issue_numbers("Fix: #99"), [99])

    def test_multiple_references_in_one_body(self):
        self.assertEqual(
            fcr.find_referenced_issue_numbers("Closes #1. Also fixes #2 and resolves #3."),
            [1, 2, 3],
        )

    def test_bare_issue_number_without_keyword_not_matched(self):
        # Plain "#6" (a mention, not a closing reference) must not match.
        self.assertEqual(fcr.find_referenced_issue_numbers("See #6 for context"), [])

    def test_empty_body_returns_empty_list(self):
        self.assertEqual(fcr.find_referenced_issue_numbers(""), [])

    def test_none_body_returns_empty_list(self):
        self.assertEqual(fcr.find_referenced_issue_numbers(None), [])

    def test_word_boundary_not_fooled_by_prefix_word(self):
        # "prefixes #6" contains "fixes #6" as a substring but isn't the word "fixes".
        self.assertEqual(fcr.find_referenced_issue_numbers("prefixes #6"), [])


class FindStaleClosingReferences(unittest.TestCase):
    def test_finds_a_stale_reference(self):
        issues = [{"number": 6, "title": "Build wiki-teacher skill"}]
        prs = [{"number": 50, "title": "Add wiki-teacher", "body": "Closes #6", "mergedAt": "2026-07-10"}]
        findings = fcr.find_stale_closing_references(issues, prs)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["issue_number"], 6)
        self.assertEqual(findings[0]["pr_number"], 50)

    def test_no_finding_when_issue_not_open(self):
        # PR references #6, but #6 isn't in the open-issues list -> already closed, no finding.
        issues = [{"number": 7, "title": "Something else"}]
        prs = [{"number": 50, "title": "Add wiki-teacher", "body": "Closes #6", "mergedAt": "2026-07-10"}]
        self.assertEqual(fcr.find_stale_closing_references(issues, prs), [])

    def test_no_finding_when_pr_body_has_no_closing_keyword(self):
        issues = [{"number": 6, "title": "Build wiki-teacher skill"}]
        prs = [{"number": 50, "title": "Add wiki-teacher", "body": "See #6 for context", "mergedAt": "2026-07-10"}]
        self.assertEqual(fcr.find_stale_closing_references(issues, prs), [])

    def test_issue_referenced_by_multiple_prs_gets_one_finding_each(self):
        issues = [{"number": 6, "title": "Build wiki-teacher skill"}]
        prs = [
            {"number": 40, "title": "Early attempt", "body": "Closes #6", "mergedAt": "2026-06-01"},
            {"number": 50, "title": "Real fix", "body": "Fixes #6", "mergedAt": "2026-07-10"},
        ]
        findings = fcr.find_stale_closing_references(issues, prs)
        self.assertEqual(len(findings), 2)

    def test_empty_inputs_return_empty_list(self):
        self.assertEqual(fcr.find_stale_closing_references([], []), [])

    def test_accepts_snake_case_merged_at_key(self):
        # gh CLI emits mergedAt; some other sources might emit merged_at -- accept both.
        issues = [{"number": 6, "title": "X"}]
        prs = [{"number": 50, "title": "Y", "body": "Closes #6", "merged_at": "2026-07-10"}]
        findings = fcr.find_stale_closing_references(issues, prs)
        self.assertEqual(findings[0]["pr_merged_at"], "2026-07-10")


class Cli(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def make_json_file(self, data):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        self._paths.append(path)
        return path

    def test_report_output(self):
        issues_path = self.make_json_file([{"number": 6, "title": "Build wiki-teacher skill"}])
        prs_path = self.make_json_file([{"number": 50, "title": "Add wiki-teacher", "body": "Closes #6", "mergedAt": "2026-07-10"}])
        proc = run_script("--issues", issues_path, "--prs", prs_path)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Issue #6", proc.stdout)
        self.assertIn("PR #50", proc.stdout)

    def test_no_findings_reports_cleanly(self):
        issues_path = self.make_json_file([{"number": 7, "title": "Unrelated"}])
        prs_path = self.make_json_file([{"number": 50, "title": "X", "body": "no keyword here", "mergedAt": "2026-07-10"}])
        proc = run_script("--issues", issues_path, "--prs", prs_path)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no stale closing references", proc.stdout)

    def test_json_output_is_valid(self):
        issues_path = self.make_json_file([{"number": 6, "title": "Build wiki-teacher skill"}])
        prs_path = self.make_json_file([{"number": 50, "title": "Add wiki-teacher", "body": "Closes #6", "mergedAt": "2026-07-10"}])
        proc = run_script("--issues", issues_path, "--prs", prs_path, "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["issue_number"], 6)

    def test_missing_file_errors_cleanly(self):
        proc = run_script("--issues", "/nonexistent/issues.json", "--prs", "/nonexistent/prs.json")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("could not read input", proc.stderr)

    def test_malformed_json_errors_cleanly(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            f.write("not valid json{{{")
        self._paths.append(path)
        prs_path = self.make_json_file([])
        proc = run_script("--issues", path, "--prs", prs_path)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("could not read input", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
