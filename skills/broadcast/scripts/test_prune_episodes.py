#!/usr/bin/env python3
"""Tests for prune_episodes.py — all against real temp directories (this
module's whole job is filesystem listing/deletion, so faking os.listdir
would test less than just using a real, disposable tmp dir does).

Run: python test_prune_episodes.py"""

import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "prune_episodes.py")

spec = importlib.util.spec_from_file_location("prune_episodes", SCRIPT)
prune_episodes_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prune_episodes_mod)


def make_episode_dir(data_dir, iso_date, with_files=True):
    d = os.path.join(data_dir, "episodes", iso_date)
    os.makedirs(d, exist_ok=True)
    if with_files:
        with open(os.path.join(d, "report.json"), "w") as f:
            f.write("{}")
    return d


class ListEpisodeDirs(unittest.TestCase):
    def test_no_episodes_directory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(prune_episodes_mod.list_episode_dirs(tmp), [])

    def test_lists_valid_date_directories_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_episode_dir(tmp, "2026-09-02")
            make_episode_dir(tmp, "2026-08-15")
            self.assertEqual(prune_episodes_mod.list_episode_dirs(tmp), ["2026-08-15", "2026-09-02"])

    def test_non_date_directory_names_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_episode_dir(tmp, "2026-09-02")
            os.makedirs(os.path.join(tmp, "episodes", "not-a-date"))
            self.assertEqual(prune_episodes_mod.list_episode_dirs(tmp), ["2026-09-02"])

    def test_stray_files_directly_under_episodes_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_episode_dir(tmp, "2026-09-02")
            os.makedirs(os.path.join(tmp, "episodes"), exist_ok=True)
            with open(os.path.join(tmp, "episodes", "stray.txt"), "w") as f:
                f.write("x")
            self.assertEqual(prune_episodes_mod.list_episode_dirs(tmp), ["2026-09-02"])


class EpisodesOlderThan(unittest.TestCase):
    def test_returns_only_dirs_outside_the_retention_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_episode_dir(tmp, "2026-08-01")   # 32 days before current_date
            make_episode_dir(tmp, "2026-09-01")   # 1 day before
            stale = prune_episodes_mod.episodes_older_than(tmp, "2026-09-02", retention_days=14)
            self.assertEqual(stale, ["2026-08-01"])

    def test_exactly_at_the_cutoff_is_kept_not_pruned(self):
        # retention_days=14, current_date - 14 days == cutoff itself;
        # a dir dated exactly on the cutoff is NOT older than it.
        with tempfile.TemporaryDirectory() as tmp:
            make_episode_dir(tmp, "2026-08-19")  # exactly 14 days before 2026-09-02
            stale = prune_episodes_mod.episodes_older_than(tmp, "2026-09-02", retention_days=14)
            self.assertEqual(stale, [])

    def test_default_retention_days_is_ninety(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_episode_dir(tmp, "2026-05-01")  # well over 90 days before 2026-09-02
            stale = prune_episodes_mod.episodes_older_than(tmp, "2026-09-02")
            self.assertEqual(stale, ["2026-05-01"])


class PruneEpisodes(unittest.TestCase):
    def test_dry_run_default_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale_dir = make_episode_dir(tmp, "2026-08-01")
            reported = prune_episodes_mod.prune_episodes(tmp, "2026-09-02", retention_days=14)
            self.assertEqual(reported, ["2026-08-01"])
            self.assertTrue(os.path.isdir(stale_dir))  # still there — apply defaults to False

    def test_apply_true_actually_removes_stale_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale_dir = make_episode_dir(tmp, "2026-08-01")
            fresh_dir = make_episode_dir(tmp, "2026-09-01")
            reported = prune_episodes_mod.prune_episodes(tmp, "2026-09-02", retention_days=14, apply=True)
            self.assertEqual(reported, ["2026-08-01"])
            self.assertFalse(os.path.exists(stale_dir))
            self.assertTrue(os.path.isdir(fresh_dir))  # untouched — within retention

    def test_apply_true_with_nothing_stale_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh_dir = make_episode_dir(tmp, "2026-09-01")
            reported = prune_episodes_mod.prune_episodes(tmp, "2026-09-02", retention_days=14, apply=True)
            self.assertEqual(reported, [])
            self.assertTrue(os.path.isdir(fresh_dir))

    def test_apply_true_removes_the_episode_files_too_not_just_the_dir_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale_dir = make_episode_dir(tmp, "2026-08-01", with_files=True)
            report_path = os.path.join(stale_dir, "report.json")
            self.assertTrue(os.path.exists(report_path))
            prune_episodes_mod.prune_episodes(tmp, "2026-09-02", retention_days=14, apply=True)
            self.assertFalse(os.path.exists(report_path))


if __name__ == "__main__":
    unittest.main()
