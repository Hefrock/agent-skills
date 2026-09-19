#!/usr/bin/env python3
"""
Unit tests for bootstrap_stats.py.

Stdlib only (unittest). Run: python test_bootstrap_stats.py
"""

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "bootstrap_stats.py")

spec = importlib.util.spec_from_file_location("bootstrap_stats", SCRIPT)
bootstrap_stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap_stats)


class BootstrapMeanCi(unittest.TestCase):
    def test_point_estimate_is_plain_mean(self):
        result = bootstrap_stats.bootstrap_mean_ci([1.0, 0.0, 1.0, 1.0])
        self.assertEqual(result["point"], 0.75)

    def test_ci_contains_point_estimate(self):
        result = bootstrap_stats.bootstrap_mean_ci([1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0])
        self.assertLessEqual(result["ci_lo"], result["point"])
        self.assertGreaterEqual(result["ci_hi"], result["point"])

    def test_all_identical_values_gives_zero_width_ci(self):
        # No variance to resample -> every bootstrap draw reproduces the
        # same mean, so the CI collapses to a point.
        result = bootstrap_stats.bootstrap_mean_ci([0.8, 0.8, 0.8, 0.8, 0.8])
        self.assertEqual(result["ci_lo"], 0.8)
        self.assertEqual(result["ci_hi"], 0.8)

    def test_same_seed_is_reproducible(self):
        values = [1.0, 0.0, 1.0, 0.5, 0.3, 0.9, 0.0, 1.0]
        a = bootstrap_stats.bootstrap_mean_ci(values, boot_seed=42)
        b = bootstrap_stats.bootstrap_mean_ci(values, boot_seed=42)
        self.assertEqual(a, b)

    def test_different_seed_can_shift_ci_slightly(self):
        # Not asserting a specific delta, just that the seed actually
        # participates in the computation rather than being ignored. Uses
        # varied floats (not 0/1 flags) so the resampled-mean space isn't
        # so coarse/discrete that two independent seeds coincidentally
        # land on the identical rounded percentile by chance.
        values = [0.12, 0.87, 0.43, 0.91, 0.05, 0.66, 0.34, 0.78, 0.21, 0.59]
        results = [bootstrap_stats.bootstrap_mean_ci(values, boot_seed=s) for s in range(1, 6)]
        self.assertTrue(len({(r["ci_lo"], r["ci_hi"]) for r in results}) > 1)
        for r in results:
            self.assertAlmostEqual(r["point"], results[0]["point"])  # point estimate never depends on the seed

    def test_larger_sample_gives_narrower_or_equal_ci(self):
        small = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0] * 1
        large = small * 10
        small_ci = bootstrap_stats.bootstrap_mean_ci(small, boot_seed=7)
        large_ci = bootstrap_stats.bootstrap_mean_ci(large, boot_seed=7)
        small_width = small_ci["ci_hi"] - small_ci["ci_lo"]
        large_width = large_ci["ci_hi"] - large_ci["ci_lo"]
        self.assertLessEqual(large_width, small_width)

    def test_n_and_n_boot_reported(self):
        result = bootstrap_stats.bootstrap_mean_ci([1.0, 0.0, 1.0], n_boot=500)
        self.assertEqual(result["n"], 3)
        self.assertEqual(result["n_boot"], 500)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            bootstrap_stats.bootstrap_mean_ci([])

    def test_works_for_mean_score_not_just_pass_rate(self):
        # Non-0/1 floats (a mean score, not a pass-rate flag) work the same way.
        result = bootstrap_stats.bootstrap_mean_ci([0.9, 0.85, 0.6, 0.95, 0.7])
        self.assertAlmostEqual(result["point"], 0.8, places=4)


class PairedBootstrapDiff(unittest.TestCase):
    def test_point_estimates_and_diff(self):
        current = [1.0, 1.0, 1.0, 0.0]  # mean 0.75
        baseline = [1.0, 1.0, 0.0, 0.0]  # mean 0.5
        result = bootstrap_stats.paired_bootstrap_diff(current, baseline)
        self.assertEqual(result["point_a"], 0.75)
        self.assertEqual(result["point_b"], 0.5)
        self.assertEqual(result["diff"], 0.25)

    def test_identical_arrays_are_not_significant(self):
        values = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
        result = bootstrap_stats.paired_bootstrap_diff(values, values)
        self.assertEqual(result["diff"], 0.0)
        self.assertFalse(result["significant_at_0.05"])
        self.assertEqual(result["p_value"], 1.0)

    def test_large_consistent_drop_is_significant(self):
        # Every one of 30 paired cases dropped from pass to fail --
        # about as unambiguous a real regression as it gets.
        current = [0.0] * 30
        baseline = [1.0] * 30
        result = bootstrap_stats.paired_bootstrap_diff(current, baseline)
        self.assertTrue(result["significant_at_0.05"])
        self.assertLess(result["p_value"], 0.05)
        self.assertLess(result["ci_hi"], 0.0)  # the whole CI sits below zero

    def test_single_flipped_case_at_small_n_is_not_significant(self):
        # SKILL.md step 7's own example: "3/10 passed... too small a
        # sample." One case flipping out of a small paired set shouldn't
        # clear the bar -- this is the exact false-positive
        # find_regressions() alone can't distinguish from noise.
        current = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0]
        baseline = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        result = bootstrap_stats.paired_bootstrap_diff(current, baseline)
        self.assertFalse(result["significant_at_0.05"])

    def test_swapping_a_and_b_negates_diff_and_keeps_same_significance(self):
        a = [1.0] * 20
        b = [0.0] * 20
        forward = bootstrap_stats.paired_bootstrap_diff(a, b)
        backward = bootstrap_stats.paired_bootstrap_diff(b, a)
        self.assertEqual(forward["diff"], -backward["diff"])
        self.assertEqual(forward["significant_at_0.05"], backward["significant_at_0.05"])
        self.assertEqual(forward["p_value"], backward["p_value"])

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            bootstrap_stats.paired_bootstrap_diff([1.0, 0.0], [1.0])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            bootstrap_stats.paired_bootstrap_diff([], [])

    def test_same_seed_is_reproducible(self):
        a = [1.0, 0.0, 1.0, 0.0, 1.0]
        b = [1.0, 1.0, 0.0, 0.0, 1.0]
        r1 = bootstrap_stats.paired_bootstrap_diff(a, b, boot_seed=99)
        r2 = bootstrap_stats.paired_bootstrap_diff(a, b, boot_seed=99)
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
