"""End-to-end tests for install_hook.sh: a real temp git repo, the real script,
the real installed hook actually run against real staged content -- not just a
string match on the generated file. This is a regression suite for a real bug:
the previous hook template appended an unconditional `exit 0` after invoking
scan_diff.py, so even a user who followed the script's own printed instructions
("edit this file to add --block-on high") and did so would still get a hook that
always let the commit through. See install_hook.sh's own comment for the fix.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
INSTALL_HOOK = str(SCRIPT_DIR / "install_hook.sh")


class InstallHook(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _git(self, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, check=check
        )

    def _install(self, *extra_args, input_text=None):
        return subprocess.run(
            ["bash", INSTALL_HOOK, self.repo, *extra_args],
            capture_output=True, text=True, input=input_text,
        )

    def _write(self, relpath, content):
        full = os.path.join(self.repo, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) else None
        with open(full, "w") as f:
            f.write(content)

    def _hook_path(self):
        return os.path.join(self.repo, ".git", "hooks", "pre-commit")

    def _run_hook(self):
        return subprocess.run([self._hook_path()], cwd=self.repo, capture_output=True, text=True)

    def test_default_install_blocks_on_high(self):
        proc = self._install()
        self.assertEqual(proc.returncode, 0)
        hook = Path(self._hook_path()).read_text()
        self.assertIn("--block-on high", hook)
        # The exact bug this replaced: an unconditional exit 0 after the scan
        # invocation would silently defeat --block-on no matter what threshold
        # the file names.
        self.assertNotRegex(hook, r"\nexit 0\s*$")

    def test_default_hook_actually_blocks_a_high_severity_finding(self):
        self._install()
        self._write("notes.txt", "ssn 123-45-6789\n")
        self._git("add", "notes.txt")
        proc = self._run_hook()
        self.assertNotEqual(proc.returncode, 0)

    def test_default_hook_does_not_block_a_clean_commit(self):
        self._install()
        self._write("notes.txt", "nothing sensitive here\n")
        self._git("add", "notes.txt")
        proc = self._run_hook()
        self.assertEqual(proc.returncode, 0)

    def test_block_on_none_installs_purely_advisory_hook(self):
        proc = self._install("--block-on", "none")
        self.assertEqual(proc.returncode, 0)
        hook = Path(self._hook_path()).read_text()
        invocation_line = [l for l in hook.splitlines() if l.startswith("python3")][0]
        self.assertNotIn("--block-on", invocation_line)

    def test_block_on_none_never_blocks_even_a_high_severity_finding(self):
        self._install("--block-on", "none")
        self._write("notes.txt", "ssn 123-45-6789\n")
        self._git("add", "notes.txt")
        proc = self._run_hook()
        self.assertEqual(proc.returncode, 0)

    def test_block_on_medium_threshold_is_honored(self):
        self._install("--block-on", "medium")
        hook = Path(self._hook_path()).read_text()
        self.assertIn("--block-on medium", hook)

    def test_invalid_block_on_value_rejected(self):
        proc = self._install("--block-on", "critical")
        self.assertNotEqual(proc.returncode, 0)

    def test_not_a_git_repo_errors_cleanly(self):
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(["bash", INSTALL_HOOK, d], capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("not a git repository", proc.stderr)

    def test_no_args_errors_cleanly(self):
        proc = subprocess.run(["bash", INSTALL_HOOK], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)

    def test_existing_hook_requires_confirmation(self):
        self._write(".git/hooks/pre-commit", "#!/usr/bin/env bash\necho existing\n")
        proc = self._install(input_text="n\n")
        self.assertNotEqual(proc.returncode, 0)
        hook = Path(self._hook_path()).read_text()
        self.assertIn("existing", hook)

    def test_existing_hook_overwritten_on_confirmation(self):
        self._write(".git/hooks/pre-commit", "#!/usr/bin/env bash\necho existing\n")
        proc = self._install(input_text="y\n")
        self.assertEqual(proc.returncode, 0)
        hook = Path(self._hook_path()).read_text()
        self.assertNotIn("echo existing", hook)
        self.assertIn("--block-on high", hook)


if __name__ == "__main__":
    unittest.main()
