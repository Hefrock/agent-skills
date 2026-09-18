#!/usr/bin/env python3
"""Unit + CLI tests for check_structural_claims.py — hand-crafted fixtures with
known, countable results, plus a real empirical check against this repo's own
current README.md.

Stdlib only (unittest). Run: python test_check_structural_claims.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "check_structural_claims.py")
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

spec = importlib.util.spec_from_file_location("check_structural_claims", SCRIPT)
csc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csc)


def run_script(*args):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)


class ExtractTestCountClaims(unittest.TestCase):
    def test_basic_suite_claim(self):
        claims = csc.extract_test_count_claims("├── broadcast/   # some pipeline, 392-test suite")
        self.assertEqual(claims, [{"skill": "broadcast", "claimed_count": 392,
                                    "line": "├── broadcast/   # some pipeline, 392-test suite"}])

    def test_regression_suite_wording(self):
        claims = csc.extract_test_count_claims("├── wiki-librarian/   # audits, 26-test regression suite")
        self.assertEqual(claims[0]["claimed_count"], 26)
        self.assertEqual(claims[0]["skill"], "wiki-librarian")

    def test_case_insensitive(self):
        claims = csc.extract_test_count_claims("├── foo/   # 10-TEST SUITE")
        self.assertEqual(claims[0]["claimed_count"], 10)

    def test_skill_token_is_first_word_slash_on_line_not_a_later_path(self):
        # "scripts/orchestrate.py" also matches the "word/" pattern, but it
        # appears after "broadcast/" on the line, so the directory token
        # extracted must be "broadcast", not "scripts".
        claims = csc.extract_test_count_claims(
            "├── broadcast/   # pipeline — scripts/orchestrate.py, 392-test suite"
        )
        self.assertEqual(claims[0]["skill"], "broadcast")

    def test_line_without_test_count_produces_no_claim(self):
        claims = csc.extract_test_count_claims("├── wiki-operator/   # on-demand vault operations")
        self.assertEqual(claims, [])

    def test_test_count_without_recognizable_directory_token_is_skipped(self):
        # A count claim with nothing that looks like "word/" earlier on the
        # line isn't guessed at -- skipped, not misassociated.
        claims = csc.extract_test_count_claims("Somewhere in prose: a 10-test suite exists.")
        self.assertEqual(claims, [])

    def test_multiple_lines_each_produce_a_claim(self):
        text = (
            "├── broadcast/   # 392-test suite\n"
            "├── wiki-librarian/   # 26-test regression suite\n"
        )
        claims = csc.extract_test_count_claims(text)
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0]["skill"], "broadcast")
        self.assertEqual(claims[1]["skill"], "wiki-librarian")

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(csc.extract_test_count_claims(""), [])


class CountActualTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def make_skill(self, name, test_files):
        """test_files: {filename: source}. Returns skills_dir root."""
        skill_dir = os.path.join(self.tmpdir.name, name)
        scripts_dir = os.path.join(skill_dir, "scripts")
        os.makedirs(scripts_dir)
        for filename, source in test_files.items():
            with open(os.path.join(scripts_dir, filename), "w") as f:
                f.write(source)
        return self.tmpdir.name

    def test_sums_across_multiple_test_files(self):
        two_test_case = (
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self): pass\n"
            "    def test_b(self): pass\n"
            "if __name__ == '__main__': unittest.main()\n"
        )
        three_test_case = (
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self): pass\n"
            "    def test_b(self): pass\n"
            "    def test_c(self): pass\n"
            "if __name__ == '__main__': unittest.main()\n"
        )
        skills_dir = self.make_skill("myskill", {
            "test_one.py": two_test_case,
            "test_two.py": three_test_case,
        })
        total, files = csc.count_actual_tests(skills_dir, "myskill")
        self.assertEqual(total, 5)
        self.assertEqual(len(files), 2)

    def test_no_test_files_returns_zero_and_empty_list(self):
        skill_dir = os.path.join(self.tmpdir.name, "emptyskill")
        os.makedirs(skill_dir)
        total, files = csc.count_actual_tests(self.tmpdir.name, "emptyskill")
        self.assertEqual(total, 0)
        self.assertEqual(files, [])

    def test_nonexistent_skill_directory_returns_zero_and_empty_list(self):
        total, files = csc.count_actual_tests(self.tmpdir.name, "doesnotexist")
        self.assertEqual(total, 0)
        self.assertEqual(files, [])

    def test_never_recurses_on_its_own_test_file(self):
        # Regression test for a real bug caught during this script's own
        # verification: a numbered claim naming "repo-pincer" caused this
        # script to shell out to test_check_structural_claims.py, which
        # itself re-invokes this script against the real README -- an
        # actual runaway subprocess tree, not a hypothetical. Asserts the
        # real repo-pincer skill directory (which does contain this exact
        # test file) reports zero regardless, never hangs, never recurses.
        real_skills_dir = os.path.join(REPO_ROOT, "skills")
        total, files = csc.count_actual_tests(real_skills_dir, "repo-pincer")
        self.assertEqual(total, 0)
        self.assertEqual(files, [])


class CheckClaims(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def make_skill_with_n_tests(self, name, n):
        skill_dir = os.path.join(self.tmpdir.name, name, "scripts")
        os.makedirs(skill_dir)
        body = "\n".join(f"    def test_{i}(self): pass" for i in range(n))
        source = f"import unittest\nclass T(unittest.TestCase):\n{body}\nif __name__ == '__main__': unittest.main()\n"
        with open(os.path.join(skill_dir, "test_x.py"), "w") as f:
            f.write(source)

    def test_matching_claim_is_confirmed_not_drift(self):
        self.make_skill_with_n_tests("myskill", 5)
        claims = [{"skill": "myskill", "claimed_count": 5, "line": "..."}]
        report = csc.check_claims(claims, self.tmpdir.name)
        self.assertEqual(report["confirmed_count"], 1)
        self.assertEqual(report["drift"], [])

    def test_mismatched_claim_is_drift_with_correct_delta(self):
        self.make_skill_with_n_tests("myskill", 5)
        claims = [{"skill": "myskill", "claimed_count": 3, "line": "..."}]
        report = csc.check_claims(claims, self.tmpdir.name)
        self.assertEqual(report["confirmed_count"], 0)
        self.assertEqual(len(report["drift"]), 1)
        self.assertEqual(report["drift"][0]["actual_count"], 5)
        self.assertEqual(report["drift"][0]["delta"], 2)

    def test_missing_skill_directory_reported_separately_from_drift(self):
        claims = [{"skill": "ghost-skill", "claimed_count": 10, "line": "..."}]
        report = csc.check_claims(claims, self.tmpdir.name)
        self.assertEqual(len(report["missing_skill"]), 1)
        self.assertEqual(report["drift"], [])

    def test_skill_with_no_tests_reported_separately_from_drift(self):
        os.makedirs(os.path.join(self.tmpdir.name, "notests"))
        claims = [{"skill": "notests", "claimed_count": 10, "line": "..."}]
        report = csc.check_claims(claims, self.tmpdir.name)
        self.assertEqual(len(report["no_tests_found"]), 1)
        self.assertEqual(report["drift"], [])


class Cli(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def make_md_file(self, content):
        fd, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        self._paths.append(path)
        return path

    def test_missing_claims_file_errors_cleanly(self):
        proc = run_script("--claims-file", "/nonexistent/README.md", "--skills-dir", "skills")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("could not read", proc.stderr)

    def test_missing_skills_dir_errors_cleanly(self):
        path = self.make_md_file("├── broadcast/   # 5-test suite")
        proc = run_script("--claims-file", path, "--skills-dir", "/nonexistent/skills")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not a directory", proc.stderr)

    def test_json_output_is_valid(self):
        path = self.make_md_file("├── broadcast/   # 5-test suite")
        proc = run_script("--claims-file", path, "--skills-dir", REPO_ROOT + "/skills", "--json")
        data = json.loads(proc.stdout)
        self.assertIn("drift", data)
        self.assertIn("confirmed_count", data)

    def test_against_this_repos_real_current_readme(self):
        # Empirical, not just fixture-based: the real README.md, checked
        # against the real skills/ directory, right now. As of this test
        # being written, every claim in the README matches reality (the
        # drift this tool was built to catch was already fixed) -- so this
        # asserts zero drift today, and will fail loudly the moment a real
        # test-count claim goes stale again, which is exactly the point.
        readme = os.path.join(REPO_ROOT, "README.md")
        skills_dir = os.path.join(REPO_ROOT, "skills")
        proc = run_script("--claims-file", readme, "--skills-dir", skills_dir, "--json")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["drift"], [], f"README.md test-count claims have drifted: {data['drift']}")
        self.assertEqual(data["missing_skill"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
