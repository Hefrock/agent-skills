"""End-to-end tests for cron_check.sh -- the unattended wrapper around
check_vault_privacy.py meant for an OS-level scheduler (systemd user timer,
launchd, cron), not a live Claude session. Uses a real temp vault directory and
a real subprocess call to the real script; fake notify-send/osascript binaries
are injected via PATH to test the notification branches without depending on
an actual desktop notification daemon being present in CI.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRON_CHECK = str(HERE / "cron_check.sh")


def _write_fake_bin(bin_dir, name, log_path):
    """A fake notifier executable that just records it was called (args and all)
    to log_path, so a test can assert on invocation without a real notify daemon."""
    path = os.path.join(bin_dir, name)
    with open(path, "w") as f:
        f.write(f'#!/usr/bin/env bash\necho "$@" >> "{log_path}"\n')
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class CronCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.vault = os.path.join(self.tmp, "vault")
        os.makedirs(self.vault)
        self.log_file = os.path.join(self.tmp, "audit.log")
        self.bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_note(self, relpath, content):
        full = os.path.join(self.vault, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) else None
        with open(full, "w") as f:
            f.write(content)

    def _run(self, restrict_path=False):
        env = dict(os.environ)
        if restrict_path:
            # Only the fake-bin dir plus the real dirs behind python3/date/coreutils --
            # NOT /run/current-system/sw/bin, which on NixOS is a symlink farm of
            # *every* system command including a real notify-send, which would defeat
            # the point of "no notifier available" tests. Resolve each needed command
            # to its real (non-symlink-farm) directory instead.
            import shutil
            python_dir = os.path.dirname(sys.executable)
            coreutils_dir = os.path.dirname(os.path.realpath(shutil.which("date")))
            bash_dir = os.path.dirname(os.path.realpath(shutil.which("bash")))
            git_dir = os.path.dirname(os.path.realpath(shutil.which("git")))
            env["PATH"] = f"{self.bin_dir}:{python_dir}:{coreutils_dir}:{bash_dir}:{git_dir}"
        return subprocess.run(
            ["bash", CRON_CHECK, self.vault, self.log_file],
            capture_output=True, text=True, env=env,
        )

    def _log_lines(self):
        if not os.path.exists(self.log_file):
            return []
        with open(self.log_file) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_clean_vault_logs_not_blocked_and_exits_zero(self):
        self._write_note("notes.md", "nothing sensitive here\n")
        proc = self._run()
        self.assertEqual(proc.returncode, 0)
        lines = self._log_lines()
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0]["blocked"])
        self.assertEqual(lines[0]["result"]["findings"], [])

    def test_high_severity_finding_logs_blocked_but_still_exits_zero(self):
        self._write_note("notes.md", "ssn 123-45-6789\n")
        proc = self._run()
        # Always exits 0 -- the scheduler's own success/failure is "did this run,"
        # not "did it find something" (see cron_check.sh's closing comment).
        self.assertEqual(proc.returncode, 0)
        lines = self._log_lines()
        self.assertTrue(lines[0]["blocked"])
        self.assertTrue(len(lines[0]["result"]["findings"]) >= 1)

    def test_log_never_contains_the_actual_matched_secret_text(self):
        self._write_note("notes.md", "ssn 123-45-6789\n")
        self._run()
        with open(self.log_file) as f:
            raw = f.read()
        self.assertNotIn("123-45-6789", raw)

    def test_appends_across_multiple_runs(self):
        self._write_note("notes.md", "nothing sensitive\n")
        self._run()
        self._run()
        self.assertEqual(len(self._log_lines()), 2)

    def test_notify_send_invoked_when_available_and_blocked(self):
        notify_log = os.path.join(self.tmp, "notify_send.calls")
        _write_fake_bin(self.bin_dir, "notify-send", notify_log)
        self._write_note("notes.md", "ssn 123-45-6789\n")
        self._run(restrict_path=True)
        self.assertTrue(os.path.exists(notify_log))

    def test_notify_send_not_invoked_when_clean(self):
        notify_log = os.path.join(self.tmp, "notify_send.calls")
        _write_fake_bin(self.bin_dir, "notify-send", notify_log)
        self._write_note("notes.md", "nothing sensitive\n")
        self._run(restrict_path=True)
        self.assertFalse(os.path.exists(notify_log))

    def test_falls_back_to_stderr_when_no_notifier_available(self):
        self._write_note("notes.md", "ssn 123-45-6789\n")
        proc = self._run(restrict_path=True)
        self.assertIn("run /privacy-audit", proc.stderr)

    def test_default_log_path_used_when_omitted(self):
        # Override HOME to a temp dir so this never touches the real user's
        # actual ~/.wiki-privacy-audit.log.
        self._write_note("notes.md", "nothing sensitive\n")
        fake_home = os.path.join(self.tmp, "fake_home")
        os.makedirs(fake_home)
        env = dict(os.environ, HOME=fake_home)
        subprocess.run(["bash", CRON_CHECK, self.vault], capture_output=True, text=True, env=env)
        self.assertTrue(os.path.exists(os.path.join(fake_home, ".wiki-privacy-audit.log")))


if __name__ == "__main__":
    unittest.main()
