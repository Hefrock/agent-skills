#!/usr/bin/env python3
"""Unit tests for jsonl_io.py — the shared loader extracted from
score_eval.load_results()/run_judge.load_cases()/calibrate_judge.
load_scores_by_id() after a repo-pincer review found the three had
independently drifted into copies of the same pattern.

Run: python test_jsonl_io.py"""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("jsonl_io", os.path.join(HERE, "jsonl_io.py"))
jsonl_io = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jsonl_io)


def write_jsonl(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
    return path


class LoadJsonl(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def load(self, rows, required_keys=("id",)):
        path = write_jsonl(rows)
        self._paths.append(path)
        with contextlib.redirect_stderr(io.StringIO()):
            return jsonl_io.load_jsonl(path, required_keys=required_keys)

    def test_valid_rows_parse(self):
        rows = self.load([{"id": "a", "score": 1.0}, {"id": "b", "score": 0.0}])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "a")

    def test_blank_lines_skipped_silently(self):
        path = write_jsonl([{"id": "a"}, "", "   ", {"id": "b"}])
        self._paths.append(path)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rows = jsonl_io.load_jsonl(path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(stderr.getvalue(), "")  # blank lines don't even warn

    def test_malformed_json_skipped_with_warning(self):
        path = write_jsonl([{"id": "a"}, "{not valid json", {"id": "b"}])
        self._paths.append(path)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            rows = jsonl_io.load_jsonl(path)
        self.assertEqual([r["id"] for r in rows], ["a", "b"])
        self.assertIn("malformed line", stderr.getvalue())

    def test_missing_required_key_skipped_with_warning(self):
        stderr = io.StringIO()
        path = write_jsonl([{"score": 1.0}, {"id": "b", "score": 0.5}])
        self._paths.append(path)
        with contextlib.redirect_stderr(stderr):
            rows = jsonl_io.load_jsonl(path, required_keys=("id",))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "b")
        self.assertIn("missing", stderr.getvalue())

    def test_multiple_required_keys_all_checked(self):
        rows = self.load([{"id": "a"}, {"score": 1.0}, {"id": "b", "score": 1.0}], required_keys=("id", "score"))
        self.assertEqual([r["id"] for r in rows], ["b"])

    def test_default_required_keys_is_just_id(self):
        rows = self.load([{"id": "a", "anything_else": True}])
        self.assertEqual(len(rows), 1)

    def test_empty_file_returns_empty_list(self):
        rows = self.load([])
        self.assertEqual(rows, [])

    def test_extra_fields_preserved_untouched(self):
        rows = self.load([{"id": "a", "custom_field": [1, 2, 3], "nested": {"x": 1}}])
        self.assertEqual(rows[0]["custom_field"], [1, 2, 3])
        self.assertEqual(rows[0]["nested"], {"x": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
