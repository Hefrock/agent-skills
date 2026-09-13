#!/usr/bin/env python3
"""Unit + integration tests for check_vault_privacy.py.

Uses a real temp directory of markdown notes (no MCP, no git) — this script
never touches an actual Obsidian vault or the obsidian-vault MCP server, only
a plain folder of .md files, so it's fully testable without either.

Covers: vault walking (dotfile/dir exemption), per-note scanning via a real
subprocess call to privacy-linter's scan_diff.py (proving the cross-skill
invocation actually works, not just that it's wired up), location cleanliness
(vault-relative, not an absolute path leaking the fixture's temp directory),
aggregation across multiple notes, the --block-on gate, and CLI behavior.

Stdlib only (unittest). Run: python test_check_vault_privacy.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_vault_privacy.py")

spec = importlib.util.spec_from_file_location("check_vault_privacy", SCRIPT)
check_vault_privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_vault_privacy)


def run_script(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True,
    )


class WalkVault(unittest.TestCase):
    def test_finds_markdown_files_recursively(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Journal", "Daily"))
            open(os.path.join(d, "note.md"), "w").close()
            open(os.path.join(d, "Journal", "Daily", "2026-09-01.md"), "w").close()
            notes = check_vault_privacy.walk_vault(d)
            self.assertEqual(set(notes), {"note.md", os.path.join("Journal", "Daily", "2026-09-01.md")})

    def test_ignores_dotfiles_and_dotdirs(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".obsidian"))
            open(os.path.join(d, ".obsidian", "config.md"), "w").close()
            open(os.path.join(d, ".hidden.md"), "w").close()
            open(os.path.join(d, "visible.md"), "w").close()
            self.assertEqual(check_vault_privacy.walk_vault(d), ["visible.md"])

    def test_ignores_non_markdown_files(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "photo.jpg"), "wb").close()
            open(os.path.join(d, "note.md"), "w").close()
            self.assertEqual(check_vault_privacy.walk_vault(d), ["note.md"])

    def test_empty_vault_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(check_vault_privacy.walk_vault(d), [])


class ScanNote(unittest.TestCase):
    def test_finding_reported_for_note_with_pii(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("contact: jane.doe@example.com\n")
            findings = check_vault_privacy.scan_note(d, "note.md")
            self.assertTrue(any(f["leak_class"] == "direct_pii" for f in findings))

    def test_location_is_vault_relative_not_absolute(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Journal"))
            with open(os.path.join(d, "Journal", "note.md"), "w") as f:
                f.write("contact: jane.doe@example.com\n")
            findings = check_vault_privacy.scan_note(d, os.path.join("Journal", "note.md"))
            self.assertTrue(findings)
            self.assertNotIn(d, findings[0]["location"])
            self.assertTrue(findings[0]["location"].startswith("Journal"))

    def test_clean_note_returns_no_findings(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("nothing sensitive here\n")
            self.assertEqual(check_vault_privacy.scan_note(d, "note.md"), [])

    def test_secret_in_note_detected(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("aws_key = AKIAIOSFODNN7EXAMPLE\n")
            findings = check_vault_privacy.scan_note(d, "note.md")
            self.assertTrue(any(f["leak_class"] == "secret" for f in findings))


class Run(unittest.TestCase):
    def test_aggregates_findings_across_multiple_notes(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.md"), "w") as f:
                f.write("ssn on file: 123-45-6789\n")
            with open(os.path.join(d, "b.md"), "w") as f:
                f.write("nothing sensitive\n")
            result = check_vault_privacy.run(d)
            self.assertEqual(result["notes_scanned"], 2)
            self.assertEqual(len(result["findings"]), 1)
            self.assertIn("a.md", result["findings"][0]["location"])

    def test_no_notes_scanned_for_empty_vault(self):
        with tempfile.TemporaryDirectory() as d:
            result = check_vault_privacy.run(d)
            self.assertEqual(result, {"notes_scanned": 0, "findings": []})

    def test_respects_privacy_linter_ignore_file(self):
        # scan_note() calls scan_diff.py --file directly, which doesn't
        # consult .privacy-linter-ignore (that's a staged-diff/scan_staged()
        # concept) — confirms this script inherits scan_diff.py's real
        # --file-mode behavior rather than assuming ignore rules apply
        # everywhere.
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".privacy-linter-ignore"), "w") as f:
                f.write("fixtures/*\n")
            os.makedirs(os.path.join(d, "fixtures"))
            with open(os.path.join(d, "fixtures", "sample.md"), "w") as f:
                f.write("jane.doe@example.com\n")
            result = check_vault_privacy.run(d)
            self.assertEqual(len(result["findings"]), 1)


class CheckGate(unittest.TestCase):
    def test_no_block_on_never_gates(self):
        findings = [{"severity": "high"}]
        self.assertFalse(check_vault_privacy.check_gate(findings, None))

    def test_gate_trips_at_or_above_threshold(self):
        findings = [{"severity": "high"}]
        self.assertTrue(check_vault_privacy.check_gate(findings, "medium"))

    def test_gate_does_not_trip_below_threshold(self):
        findings = [{"severity": "low"}]
        self.assertFalse(check_vault_privacy.check_gate(findings, "high"))


class Cli(unittest.TestCase):
    def test_clean_vault_reports_no_findings_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("nothing sensitive here\n")
            proc = run_script(d)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("no findings", proc.stdout)

    def test_finding_reported_and_advisory_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("ssn on file: 123-45-6789\n")
            proc = run_script(d)
            self.assertEqual(proc.returncode, 0)  # advisory by default, even with a high finding
            self.assertIn("ssn", proc.stdout)
            self.assertIn("note.md", proc.stdout)

    def test_json_output_is_valid_and_matches_run(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("jane.doe@example.com\n")
            proc = run_script(d, "--json")
            data = json.loads(proc.stdout)
            self.assertEqual(data["notes_scanned"], 1)
            self.assertEqual(len(data["findings"]), 1)

    def test_block_on_gate_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("ssn on file: 123-45-6789\n")
            proc = run_script(d, "--block-on", "high")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("BLOCKED", proc.stderr)

    def test_block_on_gate_passes_when_threshold_not_met(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "note.md"), "w") as f:
                f.write("server at 10.0.0.42\n")  # low severity only
            proc = run_script(d, "--block-on", "high")
            self.assertEqual(proc.returncode, 0)

    def test_nonexistent_vault_errors_cleanly(self):
        proc = run_script("/nonexistent/vault/path")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("is not a directory", proc.stderr)

    def test_multiple_notes_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Journal", "Daily"))
            os.makedirs(os.path.join(d, "Knowledge"))
            with open(os.path.join(d, "Journal", "Daily", "2026-09-01.md"), "w") as f:
                f.write("met with jane.doe@example.com today\n")
            with open(os.path.join(d, "Knowledge", "ai.md"), "w") as f:
                f.write("nothing sensitive\n")
            proc = run_script(d)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Scanned 2 note(s)", proc.stdout)
            self.assertIn("1 finding(s)", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
