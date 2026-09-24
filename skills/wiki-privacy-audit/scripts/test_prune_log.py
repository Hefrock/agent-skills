"""Tests for prune_log.py -- retention for cron_check.sh's JSON-per-line audit log."""
import json
import os
import tempfile
import unittest

from prune_log import prune_log_file, prune_log_lines


def _line(timestamp, blocked=False):
    return json.dumps({"timestamp": timestamp, "blocked": blocked, "result": {"findings": []}}) + "\n"


class PruneLogLines(unittest.TestCase):
    def test_keeps_lines_within_retention_window(self):
        lines = [_line("2026-09-01T00:00:00Z"), _line("2026-09-20T00:00:00Z")]
        kept = prune_log_lines(lines, current_date="2026-09-24", retention_days=90)
        self.assertEqual(kept, lines)

    def test_drops_lines_older_than_retention_window(self):
        old = _line("2025-01-01T00:00:00Z")
        recent = _line("2026-09-20T00:00:00Z")
        kept = prune_log_lines([old, recent], current_date="2026-09-24", retention_days=90)
        self.assertEqual(kept, [recent])

    def test_boundary_line_at_exact_cutoff_is_kept(self):
        # cutoff = 2026-09-24 - 90 days; a line exactly on the cutoff date should
        # survive (>=, not >) -- same convention as dedup_store.prune_old_entries.
        cutoff_line = _line("2026-06-26T00:00:00Z")
        kept = prune_log_lines([cutoff_line], current_date="2026-09-24", retention_days=90)
        self.assertEqual(kept, [cutoff_line])

    def test_malformed_json_line_is_kept_not_dropped(self):
        garbage = "not json at all\n"
        kept = prune_log_lines([garbage], current_date="2026-09-24", retention_days=90)
        self.assertEqual(kept, [garbage])

    def test_line_missing_timestamp_field_is_kept(self):
        no_timestamp = json.dumps({"blocked": False}) + "\n"
        kept = prune_log_lines([no_timestamp], current_date="2026-09-24", retention_days=90)
        self.assertEqual(kept, [no_timestamp])

    def test_empty_input_returns_empty(self):
        self.assertEqual(prune_log_lines([], current_date="2026-09-24"), [])


class PruneLogFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp, "audit.log")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_log_file_is_not_an_error(self):
        dropped = prune_log_file(self.log_file, current_date="2026-09-24")
        self.assertEqual(dropped, 0)
        self.assertFalse(os.path.exists(self.log_file))

    def test_rewrites_file_dropping_only_stale_lines(self):
        old = _line("2025-01-01T00:00:00Z")
        recent = _line("2026-09-20T00:00:00Z")
        with open(self.log_file, "w") as f:
            f.writelines([old, recent])

        dropped = prune_log_file(self.log_file, current_date="2026-09-24", retention_days=90)

        self.assertEqual(dropped, 1)
        with open(self.log_file) as f:
            self.assertEqual(f.readlines(), [recent])

    def test_no_op_when_nothing_stale_leaves_file_untouched(self):
        recent = _line("2026-09-20T00:00:00Z")
        with open(self.log_file, "w") as f:
            f.writelines([recent])
        mtime_before = os.path.getmtime(self.log_file)

        dropped = prune_log_file(self.log_file, current_date="2026-09-24", retention_days=90)

        self.assertEqual(dropped, 0)
        self.assertEqual(os.path.getmtime(self.log_file), mtime_before)

    def test_no_leftover_tmp_file_after_pruning(self):
        old = _line("2025-01-01T00:00:00Z")
        with open(self.log_file, "w") as f:
            f.writelines([old])

        prune_log_file(self.log_file, current_date="2026-09-24", retention_days=90)

        self.assertFalse(os.path.exists(f"{self.log_file}.tmp"))


if __name__ == "__main__":
    unittest.main()
