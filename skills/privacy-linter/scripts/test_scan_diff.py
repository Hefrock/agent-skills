#!/usr/bin/env python3
"""
Unit tests for scan_diff.py — synthetic PII-seeded fixtures, per the project's own
"test on synthetic PII-seeded diffs" milestone.

Covers: each PII pattern (true positive + a documented near-miss), the metadata
file-type heuristic, EXIF GPS confirmation (skipped if Pillow isn't installed —
reported, not silently passed), suppression (.privacy-linter-ignore + inline marker),
and end-to-end CLI behavior including a real temporary git repo for the staged-diff path.

Stdlib only (unittest). Run: python test_scan_diff.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "scan_diff.py")

spec = importlib.util.spec_from_file_location("scan_diff", SCRIPT)
scan_diff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan_diff)


def run_script(*args, cwd=None, input_text=None):
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True, text=True, cwd=cwd, input=input_text,
    )


class PiiPatterns(unittest.TestCase):
    def findings_for(self, text):
        return scan_diff.scan_text_for_pii(text, location_prefix="test")

    def test_email_detected(self):
        f = self.findings_for("contact me at jane.doe@example.com please")
        self.assertTrue(any(x.leak_class == "direct_pii" and "email" in x.finding for x in f))

    def test_ssn_strict_format_detected(self):
        f = self.findings_for("SSN on file: 123-45-6789")
        self.assertTrue(any("ssn" in x.finding for x in f))

    def test_ssn_without_dashes_not_matched(self):
        # Documented limitation: only the dashed ###-##-#### form is treated as SSN-shaped.
        f = self.findings_for("order number 123456789")
        self.assertFalse(any("ssn" in x.finding for x in f))

    def test_luhn_valid_card_detected(self):
        f = self.findings_for("card: 4111 1111 1111 1111")  # known valid Luhn test number
        self.assertTrue(any("credit_card" in x.finding for x in f))

    def test_luhn_invalid_number_not_flagged_as_card(self):
        f = self.findings_for("tracking id: 1234567890123456")  # same length, fails Luhn
        self.assertFalse(any("credit_card" in x.finding for x in f))

    def test_phone_number_detected(self):
        f = self.findings_for("call me at (555) 123-4567")
        self.assertTrue(any("phone" in x.finding for x in f))

    def test_ipv4_detected(self):
        f = self.findings_for("server lives at 10.0.0.42")
        self.assertTrue(any("ip_address" in x.finding for x in f))

    def test_ipv4_octet_out_of_range_not_matched(self):
        f = self.findings_for("version string 999.888.777.666")
        self.assertFalse(any("ip_address" in x.finding for x in f))

    def test_clean_text_no_findings(self):
        f = self.findings_for("this is a perfectly ordinary sentence about nothing sensitive")
        self.assertEqual(f, [])

    def test_inline_suppression_marker(self):
        f = self.findings_for("jane.doe@example.com  # privacy-linter: ignore")
        self.assertEqual(f, [])

    def test_line_number_reported(self):
        f = self.findings_for("line one\nline two\njane.doe@example.com\n")
        self.assertEqual(f[0].location, "test:3")


class SecretPatterns(unittest.TestCase):
    def findings_for(self, text):
        return scan_diff.scan_text_for_secrets(text, location_prefix="test")

    def test_aws_access_key_detected(self):
        f = self.findings_for("key_id = AKIAIOSFODNN7EXAMPLE")
        self.assertTrue(any(x.leak_class == "secret" and "aws_access_key" in x.finding for x in f))

    def test_github_token_detected(self):
        f = self.findings_for("export GITHUB_TOKEN=ghp_" + "a" * 36)
        self.assertTrue(any("github_token" in x.finding for x in f))

    def test_slack_token_detected(self):
        f = self.findings_for("SLACK_BOT_TOKEN=xoxb-" + "1" * 12 + "-" + "a" * 16)
        self.assertTrue(any("slack_token" in x.finding for x in f))

    def test_stripe_key_detected(self):
        f = self.findings_for("stripe_key = " + "sk_live_" + "a" * 24)
        self.assertTrue(any("stripe_key" in x.finding for x in f))

    def test_google_api_key_detected(self):
        f = self.findings_for("apiKey: " + "AIza" + "a" * 35)
        self.assertTrue(any("google_api_key" in x.finding for x in f))

    def test_anthropic_api_key_detected(self):
        f = self.findings_for("ANTHROPIC_API_KEY=sk-ant-" + "a" * 20)
        self.assertTrue(any("anthropic_api_key" in x.finding for x in f))

    def test_private_key_block_detected(self):
        f = self.findings_for("-----BEGIN RSA PRIVATE KEY-----\nMIIB...\n-----END RSA PRIVATE KEY-----")
        self.assertTrue(any("private_key_block" in x.finding for x in f))

    def test_private_key_block_without_algorithm_prefix_detected(self):
        f = self.findings_for("-----BEGIN PRIVATE KEY-----\nMIIB...\n-----END PRIVATE KEY-----")
        self.assertTrue(any("private_key_block" in x.finding for x in f))

    def test_generic_quoted_secret_assignment_detected(self):
        f = self.findings_for('password = "hunter2isaveryrealpassword"')
        self.assertTrue(any("generic_secret_assignment" in x.finding for x in f))

    def test_generic_secret_keyword_mid_identifier_detected(self):
        # "password" isn't at a word boundary in "db_password" — must still match.
        f = self.findings_for('db_password = "hunter2isaveryrealpassword"')
        self.assertTrue(any("generic_secret_assignment" in x.finding for x in f))

    def test_generic_placeholder_value_not_flagged(self):
        f = self.findings_for('api_key = "your_api_key_here"')
        self.assertFalse(any("generic_secret_assignment" in x.finding for x in f))

    def test_generic_short_value_not_flagged(self):
        f = self.findings_for('token = "short"')
        self.assertFalse(any("generic_secret_assignment" in x.finding for x in f))

    def test_generic_unquoted_assignment_not_flagged(self):
        # Documented gap: bare/unquoted values (.env-style) aren't covered —
        # too many false positives (function calls, env-var references).
        f = self.findings_for("API_KEY=abcdef123456")
        self.assertFalse(any("generic_secret_assignment" in x.finding for x in f))

    def test_clean_text_no_findings(self):
        f = self.findings_for("this is a perfectly ordinary sentence about nothing sensitive")
        self.assertEqual(f, [])

    def test_inline_suppression_marker(self):
        f = self.findings_for("AKIAIOSFODNN7EXAMPLE  # privacy-linter: ignore")
        self.assertEqual(f, [])

    def test_severity_is_high_for_prefixed_tokens(self):
        f = self.findings_for("key_id = AKIAIOSFODNN7EXAMPLE")
        self.assertEqual(f[0].severity, "high")

    def test_severity_is_medium_for_generic_assignment(self):
        f = self.findings_for('password = "hunter2isaveryrealpassword"')
        self.assertEqual(f[0].severity, "medium")


class MetadataHeuristic(unittest.TestCase):
    def test_image_extension_flagged(self):
        f = scan_diff.scan_metadata(["photos/vacation.jpg"], repo_root=None)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].leak_class, "metadata")

    def test_non_metadata_extension_not_flagged(self):
        f = scan_diff.scan_metadata(["src/main.py"], repo_root=None)
        self.assertEqual(f, [])

    def test_pillow_note_present_when_unavailable(self):
        if scan_diff.HAS_PIL:
            self.skipTest("Pillow is installed in this environment")
        f = scan_diff.scan_metadata(["photos/vacation.jpg"], repo_root=None)
        self.assertIn("Pillow not installed", f[0].reason)

    @unittest.skipUnless(scan_diff.HAS_PIL, "Pillow not installed — EXIF GPS check skipped")
    def test_exif_gps_confirmed_when_present(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            img = Image.new("RGB", (2, 2))
            exif = img.getexif()
            # Minimal GPSInfo IFD: GPSLatitudeRef='N', GPSLatitude=(10,0,0). Pillow
            # serializes a plain dict assigned to the GPS tag (0x8825) as a sub-IFD.
            exif[0x8825] = {1: "N", 2: (10.0, 0.0, 0.0)}
            rel = "photo.jpg"
            full = os.path.join(d, rel)
            try:
                img.save(full, exif=exif)
            except Exception as e:  # Pillow version differences in EXIF writing
                self.skipTest(f"could not write test EXIF fixture: {e}")

            gps = scan_diff._read_exif_gps(full)
            if not gps:
                self.skipTest("Pillow round-tripped no GPS IFD in this environment — "
                               "can't validate the positive path here")

            f = scan_diff.scan_metadata([rel], repo_root=d)
            self.assertEqual(len(f), 1)
            self.assertEqual(f[0].severity, "high")
            self.assertIn("GPS", f[0].finding)

    def test_exif_capable_without_repo_root_falls_back_to_heuristic(self):
        # No repo_root means no EXIF read is attempted, regardless of Pillow availability.
        f = scan_diff.scan_metadata(["photos/vacation.jpg"], repo_root=None)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].severity, "medium")


def _make_gps_jpeg(path, testcase):
    """Writes a real JPEG with a GPSInfo EXIF IFD to `path`, skipping the
    calling test (not failing it) if this Pillow version/environment
    can't round-trip a written GPS IFD — same tolerance the pre-existing
    test_exif_gps_confirmed_when_present already established, reused here
    rather than duplicating the try/except-skip dance in every new test."""
    from PIL import Image
    img = Image.new("RGB", (2, 2))
    exif = img.getexif()
    exif[0x8825] = {1: "N", 2: (10.0, 0.0, 0.0)}
    exif[0x010F] = "TestCameraMake"
    try:
        img.save(path, exif=exif)
    except Exception as e:
        testcase.skipTest(f"could not write test EXIF fixture: {e}")
    if not scan_diff._read_exif_gps(path):
        testcase.skipTest("Pillow round-tripped no GPS IFD in this environment — can't validate the positive path here")


@unittest.skipUnless(scan_diff.HAS_PIL, "Pillow not installed — --strip-metadata has no fallback")
class MetadataStripping(unittest.TestCase):
    def test_strip_removes_gps_and_reports_removed_tags(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "photo.jpg")
            _make_gps_jpeg(source, self)
            dest = os.path.join(d, "clean.jpg")

            removed = scan_diff.strip_exif_metadata(source, dest)
            self.assertIn("GPSInfo", removed)
            self.assertIn("Make", removed)
            self.assertIsNone(scan_diff._read_exif_gps(dest))

    def test_strip_on_image_with_no_exif_returns_empty_list(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "plain.jpg")
            Image.new("RGB", (2, 2)).save(source)
            dest = os.path.join(d, "clean.jpg")

            removed = scan_diff.strip_exif_metadata(source, dest)
            self.assertEqual(removed, [])
            self.assertTrue(os.path.isfile(dest))

    def test_strip_in_place_same_path_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "photo.jpg")
            _make_gps_jpeg(path, self)

            scan_diff.strip_exif_metadata(path, path)
            self.assertIsNone(scan_diff._read_exif_gps(path))

    def test_cli_strip_default_output_path(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "photo.jpg")
            _make_gps_jpeg(source, self)

            proc = run_script("--strip-metadata", source)
            self.assertEqual(proc.returncode, 0)
            dest = os.path.join(d, "photo.stripped.jpg")
            self.assertTrue(os.path.isfile(dest))
            self.assertIsNone(scan_diff._read_exif_gps(dest))
            self.assertIn("stripped EXIF", proc.stdout)

    def test_cli_strip_refuses_to_clobber_existing_default_output(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "photo.jpg")
            _make_gps_jpeg(source, self)
            existing = os.path.join(d, "photo.stripped.jpg")
            with open(existing, "w") as f:
                f.write("not a real image, just occupying the path")

            proc = run_script("--strip-metadata", source)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("already exists", proc.stderr)
            with open(existing) as f:
                self.assertEqual(f.read(), "not a real image, just occupying the path")

    def test_cli_strip_explicit_out_path(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "photo.jpg")
            _make_gps_jpeg(source, self)
            out = os.path.join(d, "custom_name.jpg")

            proc = run_script("--strip-metadata", source, "--out", out)
            self.assertEqual(proc.returncode, 0)
            self.assertTrue(os.path.isfile(out))
            self.assertIsNone(scan_diff._read_exif_gps(out))

    def test_cli_strip_in_place_flag(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "photo.jpg")
            _make_gps_jpeg(source, self)

            proc = run_script("--strip-metadata", source, "--in-place")
            self.assertEqual(proc.returncode, 0)
            self.assertIsNone(scan_diff._read_exif_gps(source))

    def test_cli_strip_unsupported_extension_errors_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "doc.pdf")
            with open(source, "w") as f:
                f.write("not a real pdf")

            proc = run_script("--strip-metadata", source)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("isn't implemented for .pdf", proc.stderr)

    def test_cli_strip_missing_file_errors_cleanly(self):
        proc = run_script("--strip-metadata", "/nonexistent/path/photo.jpg")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not found", proc.stderr)


class IgnorePatterns(unittest.TestCase):
    def test_glob_match(self):
        patterns = ["*.log", "fixtures/*"]
        self.assertTrue(scan_diff.is_path_ignored("debug.log", patterns))
        self.assertTrue(scan_diff.is_path_ignored("fixtures/sample.jpg", patterns))
        self.assertFalse(scan_diff.is_path_ignored("src/main.py", patterns))

    def test_load_ignore_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".privacy-linter-ignore"), "w") as f:
                f.write("# comment\n*.jpg\n\nfixtures/*\n")
            patterns = scan_diff.load_ignore_patterns(d)
            self.assertEqual(patterns, ["*.jpg", "fixtures/*"])

    def test_missing_ignore_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(scan_diff.load_ignore_patterns(d), [])


class DiffParsing(unittest.TestCase):
    def test_added_lines_extracted_with_line_numbers(self):
        diff = (
            "diff --git a/notes.txt b/notes.txt\n"
            "index e69de29..0000000 100644\n"
            "--- a/notes.txt\n"
            "+++ b/notes.txt\n"
            "@@ -0,0 +1,2 @@\n"
            "+first added line\n"
            "+jane.doe@example.com\n"
        )
        lines = list(scan_diff.added_lines_from_diff(diff))
        self.assertEqual(lines, [
            ("notes.txt", 1, "first added line"),
            ("notes.txt", 2, "jane.doe@example.com"),
        ])

    def test_removed_lines_ignored(self):
        diff = (
            "diff --git a/notes.txt b/notes.txt\n"
            "--- a/notes.txt\n"
            "+++ b/notes.txt\n"
            "@@ -1 +1 @@\n"
            "-old secret@example.com\n"
            "+new line, no pii here\n"
        )
        lines = list(scan_diff.added_lines_from_diff(diff))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][2], "new line, no pii here")


class CliFileMode(unittest.TestCase):
    def test_text_file_with_pii_reports_finding_and_exits_zero_by_default(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("reach me at jane.doe@example.com\n")
            path = f.name
        try:
            proc = run_script("--file", path)
            self.assertEqual(proc.returncode, 0)  # advisory by default, even with findings
            self.assertIn("email", proc.stdout)
        finally:
            os.unlink(path)

    def test_binary_extension_does_not_crash_and_uses_metadata_path(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff\xe0not a real jpeg but binary-ish bytes\x00\x01\x02")
            path = f.name
        try:
            proc = run_script("--file", path)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("metadata", proc.stdout)
        finally:
            os.unlink(path)

    def test_clean_file_reports_no_findings(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("nothing sensitive here at all\n")
            path = f.name
        try:
            proc = run_script("--file", path)
            self.assertIn("no findings", proc.stdout)
        finally:
            os.unlink(path)

    def test_stdin_text_mode(self):
        proc = run_script("--text", "-", input_text="ssn 123-45-6789\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("ssn", proc.stdout)

    def test_stdin_text_mode_detects_secrets_too(self):
        proc = run_script("--text", "-", input_text="key_id = AKIAIOSFODNN7EXAMPLE\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("aws_access_key", proc.stdout)

    def test_file_mode_detects_secrets_too(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("ANTHROPIC_API_KEY=sk-ant-" + "a" * 20 + "\n")
            path = f.name
        try:
            proc = run_script("--file", path)
            self.assertIn("anthropic_api_key", proc.stdout)
        finally:
            os.unlink(path)

    def test_json_output_is_valid(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("jane.doe@example.com\n")
            path = f.name
        try:
            proc = run_script("--file", path, "--json")
            data = json.loads(proc.stdout)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["leak_class"], "direct_pii")
        finally:
            os.unlink(path)

    def test_block_on_gate_exits_nonzero_when_threshold_met(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("ssn 123-45-6789\n")  # 'high' severity
            path = f.name
        try:
            proc = run_script("--file", path, "--block-on", "high")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("BLOCKED", proc.stderr)
        finally:
            os.unlink(path)

    def test_block_on_gate_passes_when_threshold_not_met(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("server at 10.0.0.42\n")  # 'low' severity only
            path = f.name
        try:
            proc = run_script("--file", path, "--block-on", "high")
            self.assertEqual(proc.returncode, 0)
        finally:
            os.unlink(path)


class CliGitStagedMode(unittest.TestCase):
    """End-to-end: a real temp git repo, staged content, default (no-args) mode."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, check=True)

    def _write(self, relpath, content):
        full = os.path.join(self.repo, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) else None
        with open(full, "w") as f:
            f.write(content)

    def test_staged_pii_detected_with_no_args(self):
        self._write("notes.txt", "contact: jane.doe@example.com\n")
        self._git("add", "notes.txt")
        proc = run_script(cwd=self.repo)
        self.assertEqual(proc.returncode, 0)  # advisory by default
        self.assertIn("email", proc.stdout)

    def test_staged_secret_detected_with_no_args(self):
        self._write(".env", "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n")
        self._git("add", ".env")
        proc = run_script(cwd=self.repo)
        self.assertEqual(proc.returncode, 0)  # advisory by default
        self.assertIn("aws_access_key", proc.stdout)
        self.assertIn("[class: secret]", proc.stdout)

    def test_ignored_path_suppresses_finding(self):
        self._write(".privacy-linter-ignore", "fixtures/*\n")
        self._write("fixtures/sample.txt", "jane.doe@example.com\n")
        self._git("add", "-A")
        proc = run_script(cwd=self.repo)
        self.assertIn("no findings", proc.stdout)

    def test_unstaged_changes_not_scanned(self):
        self._write("notes.txt", "nothing sensitive\n")
        self._git("add", "notes.txt")
        self._git("commit", "-q", "-m", "init")
        # Modify but do NOT stage — should not appear in the scan.
        self._write("notes.txt", "nothing sensitive\njane.doe@example.com\n")
        proc = run_script(cwd=self.repo)
        self.assertIn("no findings", proc.stdout)

    def test_block_on_gate_against_staged_repo(self):
        self._write("notes.txt", "ssn 123-45-6789\n")
        self._git("add", "notes.txt")
        proc = run_script("--block-on", "high", cwd=self.repo)
        self.assertEqual(proc.returncode, 1)

    def test_not_a_git_repo_errors_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_script(cwd=d)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("not inside a git repository", proc.stderr)


class ScanHistory(unittest.TestCase):
    """--scan-history: a real temp git repo with several commits, including
    one that introduces a secret and a LATER commit that removes it again —
    the case the staged-diff/working-tree scan structurally cannot see."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, check=True)

    def _write(self, relpath, content):
        full = os.path.join(self.repo, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) else None
        with open(full, "w") as f:
            f.write(content)

    def _commit(self, msg):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", msg)

    def test_finds_secret_introduced_and_later_removed(self):
        self._write("notes.txt", "nothing sensitive here\n")
        self._commit("init")
        self._write(".env", "aws_key = AKIAIOSFODNN7EXAMPLE\n")
        self._commit("oops, committed a key")
        self._git("rm", "-q", ".env")
        self._commit("remove the key")

        # Working tree/staged view has nothing — the secret is genuinely gone from HEAD.
        proc = run_script(cwd=self.repo)
        self.assertIn("no findings", proc.stdout)

        # History scan still finds it, because it's still reachable in an old commit.
        proc = run_script("--scan-history", cwd=self.repo)
        self.assertEqual(proc.returncode, 0)  # advisory by default
        self.assertIn("aws_access_key", proc.stdout)

    def test_findings_report_the_introducing_commit_and_path(self):
        self._write("notes.txt", "initial\n")
        self._commit("init")
        self._write("config.txt", "ssn on file: 123-45-6789\n")
        self._commit("add config")

        proc = run_script("--scan-history", cwd=self.repo)
        second_commit = self._git("rev-parse", "HEAD").stdout.strip()[:8]
        self.assertIn(second_commit, proc.stdout)
        self.assertIn("config.txt:1", proc.stdout)

    def test_findings_ordered_oldest_commit_first(self):
        self._write("a.txt", "ssn on file: 123-45-6789\n")
        self._commit("first leak")
        self._write("b.txt", "aws_key = AKIAIOSFODNN7EXAMPLE\n")
        self._commit("second leak")

        proc = run_script("--scan-history", "--json", cwd=self.repo)
        rows = json.loads(proc.stdout)
        locations = [r["location"] for r in rows]
        # a.txt's finding (first commit) must be reported before b.txt's (second commit).
        self.assertLess(
            next(i for i, loc in enumerate(locations) if "a.txt" in loc),
            next(i for i, loc in enumerate(locations) if "b.txt" in loc),
        )

    def test_ignored_path_suppressed_across_history(self):
        self._write(".privacy-linter-ignore", "fixtures/*\n")
        self._commit("add ignore file")
        self._write("fixtures/sample.txt", "jane.doe@example.com\n")
        self._commit("add fixture")

        proc = run_script("--scan-history", cwd=self.repo)
        self.assertIn("no findings", proc.stdout)

    def test_inline_suppression_marker_respected_in_history(self):
        self._write("notes.txt", "jane.doe@example.com  # privacy-linter: ignore\n")
        self._commit("add suppressed line")

        proc = run_script("--scan-history", cwd=self.repo)
        self.assertIn("no findings", proc.stdout)

    def test_max_commits_limits_scan_to_most_recent(self):
        self._write("a.txt", "ssn on file: 123-45-6789\n")
        self._commit("old leak")
        self._write("b.txt", "nothing sensitive\n")
        self._commit("unrelated recent commit")

        proc = run_script("--scan-history", "--max-commits", "1", cwd=self.repo)
        self.assertIn("no findings", proc.stdout)

    def test_merge_commit_does_not_crash_the_scan(self):
        self._write("base.txt", "nothing sensitive\n")
        self._commit("init")
        default_branch = self._git("branch", "--show-current").stdout.strip()
        self._git("checkout", "-q", "-b", "feature")
        self._write("feature.txt", "ssn on file: 123-45-6789\n")
        self._commit("feature work")
        self._git("checkout", "-q", default_branch)
        self._git("merge", "-q", "--no-edit", "feature")

        proc = run_script("--scan-history", cwd=self.repo)
        self.assertEqual(proc.returncode, 0)
        # The non-merge "feature work" commit is still scanned directly.
        self.assertIn("ssn", proc.stdout)

    def test_block_on_gate_applies_to_history_scan(self):
        self._write("notes.txt", "ssn on file: 123-45-6789\n")
        self._commit("leak")

        proc = run_script("--scan-history", "--block-on", "high", cwd=self.repo)
        self.assertEqual(proc.returncode, 1)

    def test_scan_history_outside_git_repo_errors_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run_script("--scan-history", cwd=d)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("--scan-history requires being inside a git repository", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
