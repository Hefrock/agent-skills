#!/usr/bin/env python3
"""Tests for source_health_report.py — real temp directories for the
filesystem-facing functions (same discipline as test_prune_episodes.py:
this module's whole job is reading real files, so faking that would test
less than a real, disposable tmp dir does), pure-dict fixtures for the
aggregation logic itself.

Run: python test_source_health_report.py"""

import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


source_health_report = load("source_health_report")
source_registry = load("source_registry")


def make_report(data_dir, iso_date, source_utilization=None):
    d = os.path.join(data_dir, "episodes", iso_date)
    os.makedirs(d, exist_ok=True)
    report = {"run_date": iso_date}
    if source_utilization is not None:
        report["source_utilization"] = source_utilization
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f)


TEST_REGISTRY = {
    "categories": {
        "regulatory": {"authority_floor": 0.9, "half_life_days": 30},
        "industry_press": {"authority_floor": 0.4, "half_life_days": 3},
    },
    "sources": [
        {"key": "fda_guidance", "name": "FDA", "category": "regulatory"},
        {"key": "stat_news", "name": "STAT", "category": "industry_press"},
        {"key": "hit_consultant", "name": "HIT Consultant", "category": "industry_press"},
    ],
    "throughline_keywords": ["fhir"],
}


class LoadSourceUtilization(unittest.TestCase):
    def test_missing_report_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(source_health_report.load_source_utilization(tmp, "2026-09-02"))

    def test_report_without_source_utilization_returns_none(self):
        # An older episode run from before this field existed.
        with tempfile.TemporaryDirectory() as tmp:
            make_report(tmp, "2026-09-02", source_utilization=None)
            self.assertIsNone(source_health_report.load_source_utilization(tmp, "2026-09-02"))

    def test_malformed_json_returns_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "episodes", "2026-09-02")
            os.makedirs(d)
            with open(os.path.join(d, "report.json"), "w") as f:
                f.write("{not valid json")
            self.assertIsNone(source_health_report.load_source_utilization(tmp, "2026-09-02"))

    def test_real_source_utilization_is_returned(self):
        util = {"stat_news": {"candidates": 2, "selected_total": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            make_report(tmp, "2026-09-02", source_utilization=util)
            self.assertEqual(source_health_report.load_source_utilization(tmp, "2026-09-02"), util)


class AggregateSourceUtilization(unittest.TestCase):
    def test_empty_input_returns_empty_dict(self):
        self.assertEqual(source_health_report.aggregate_source_utilization([]), {})

    def test_sums_a_single_source_across_multiple_days(self):
        per_day = [
            {"stat_news": {"candidates": 2, "selected_top_three": 0, "selected_quick_hits": 1, "selected_total": 1, "not_selected": 1, "dropped_duplicates": 0}},
            {"stat_news": {"candidates": 3, "selected_top_three": 0, "selected_quick_hits": 2, "selected_total": 2, "not_selected": 1, "dropped_duplicates": 0}},
        ]
        result = source_health_report.aggregate_source_utilization(per_day)
        self.assertEqual(result["stat_news"]["days_with_data"], 2)
        self.assertEqual(result["stat_news"]["candidates"], 5)
        self.assertEqual(result["stat_news"]["selected_total"], 3)
        self.assertEqual(result["stat_news"]["not_selected"], 2)

    def test_selection_rate_is_computed_over_the_full_window_not_averaged_per_day(self):
        # 1/2 one day, 2/3 the next -> pooled 3/5 = 0.6, not the average
        # of the two daily rates (0.5 and 0.667 average to 0.583).
        per_day = [
            {"stat_news": {"candidates": 2, "selected_top_three": 0, "selected_quick_hits": 1, "selected_total": 1, "not_selected": 1, "dropped_duplicates": 0}},
            {"stat_news": {"candidates": 3, "selected_top_three": 0, "selected_quick_hits": 2, "selected_total": 2, "not_selected": 1, "dropped_duplicates": 0}},
        ]
        result = source_health_report.aggregate_source_utilization(per_day)
        self.assertAlmostEqual(result["stat_news"]["selection_rate"], 0.6)

    def test_source_absent_on_some_days_only_counts_days_with_data(self):
        per_day = [
            {"stat_news": {"candidates": 1, "selected_top_three": 0, "selected_quick_hits": 1, "selected_total": 1, "not_selected": 0, "dropped_duplicates": 0}},
            {},  # stat_news had zero candidates this day, omitted entirely (matches rank.py's own convention)
        ]
        result = source_health_report.aggregate_source_utilization(per_day)
        self.assertEqual(result["stat_news"]["days_with_data"], 1)
        self.assertEqual(result["stat_news"]["candidates"], 1)

    def test_source_with_zero_candidates_every_day_gets_a_null_selection_rate(self):
        result = source_health_report.aggregate_source_utilization([{}, {}])
        self.assertEqual(result, {})  # never appears at all -> not in the dict; see find_never_appearing_sources

    def test_multiple_sources_tracked_independently(self):
        per_day = [
            {
                "stat_news": {"candidates": 1, "selected_top_three": 0, "selected_quick_hits": 1, "selected_total": 1, "not_selected": 0, "dropped_duplicates": 0},
                "fda_guidance": {"candidates": 1, "selected_top_three": 1, "selected_quick_hits": 0, "selected_total": 1, "not_selected": 0, "dropped_duplicates": 0},
            },
        ]
        result = source_health_report.aggregate_source_utilization(per_day)
        self.assertEqual(set(result.keys()), {"stat_news", "fda_guidance"})
        self.assertEqual(result["fda_guidance"]["selected_top_three"], 1)


class FindNeverAppearingSources(unittest.TestCase):
    def test_registered_source_with_no_aggregated_entry_is_flagged(self):
        aggregated = {"stat_news": {}, "fda_guidance": {}}
        never = source_health_report.find_never_appearing_sources(TEST_REGISTRY, aggregated)
        self.assertEqual(never, ["hit_consultant"])

    def test_all_sources_present_returns_empty_list(self):
        aggregated = {"stat_news": {}, "fda_guidance": {}, "hit_consultant": {}}
        self.assertEqual(source_health_report.find_never_appearing_sources(TEST_REGISTRY, aggregated), [])

    def test_empty_aggregated_flags_every_registered_source(self):
        never = source_health_report.find_never_appearing_sources(TEST_REGISTRY, {})
        self.assertEqual(never, ["fda_guidance", "hit_consultant", "stat_news"])

    def test_real_config_produces_no_crash_and_a_sorted_list(self):
        registry = source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        never = source_health_report.find_never_appearing_sources(registry, {})
        self.assertEqual(never, sorted(never))
        self.assertGreater(len(never), 0)


class MainCli(unittest.TestCase):
    """Real end-to-end: writes real report.json files, runs the real
    main() against them, only mocking sys.argv and capturing stdout."""

    def test_window_filtering_excludes_reports_outside_the_days_argument(self):
        import sys
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            make_report(tmp, "2026-08-01", source_utilization={"stat_news": {"candidates": 1, "selected_top_three": 0, "selected_quick_hits": 1, "selected_total": 1, "not_selected": 0, "dropped_duplicates": 0}})  # outside a 7-day window from 2026-09-02
            make_report(tmp, "2026-09-01", source_utilization={"fda_guidance": {"candidates": 1, "selected_top_three": 1, "selected_quick_hits": 0, "selected_total": 1, "not_selected": 0, "dropped_duplicates": 0}})  # inside

            argv = ["source_health_report.py", "--data-dir", tmp, "--date", "2026-09-02", "--days", "7"]
            stdout = __import__("io").StringIO()
            with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stdout", stdout):
                exit_code = source_health_report.main()
            output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn("fda_guidance", output)
        # stat_news's only report was outside the window, so it correctly
        # has no row in the utilization table (not just absent from
        # output entirely — it's a real registered source in the actual
        # config, so it legitimately appears in the "zero candidates in
        # this window" line instead, which is the correct behavior, not
        # a bug: the window filtering worked).
        table_section = output.split("Registered but ZERO candidates")[0]
        self.assertNotIn("stat_news", table_section)
        self.assertIn("stat_news", output.split("Registered but ZERO candidates")[1])

    def test_no_data_in_window_reports_that_plainly(self):
        import sys
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            argv = ["source_health_report.py", "--data-dir", tmp, "--date", "2026-09-02", "--days", "30"]
            stdout = __import__("io").StringIO()
            with mock.patch.object(sys, "argv", argv), mock.patch.object(sys, "stdout", stdout):
                exit_code = source_health_report.main()
            output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertIn("No episodes", output)


if __name__ == "__main__":
    unittest.main()
