#!/usr/bin/env python3
"""Regression tests for health_score.py, against the SAME fixture vault
skills/wiki-librarian/scripts/fixtures/vault uses (see that directory's
test file for why: one fixture, not two drifting copies).

Run with: python skills/wiki-governor/scripts/test_health_score.py

Note on is_system_file(): _scoped()'s exemption is currently redundant
here too, for the same reason it's redundant in check_vault.py's orphan
check — KNOWLEDGE_GRAPH_PREFIXES never includes Maps/ or System/, so
those paths are excluded by prefix alone regardless of the guard.
Confirmed by deliberately removing the guard and re-running: no test
failed. It stays as the same defense-in-depth check_vault.py documents,
not because a test here proves it load-bearing.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from health_score import (
    compute_health_score,
    warehouse_integrity,
    _count_open_question_entries,
    _scoped,
)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "wiki-librarian", "scripts"))
from check_vault import load_vault  # noqa: E402

FIXTURE_VAULT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "wiki-librarian", "scripts", "fixtures", "vault"
)
NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


class HealthScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = compute_health_score(FIXTURE_VAULT, now=NOW)
        cls.notes = load_vault(FIXTURE_VAULT)

    # ── Hand-computed against the fixture, independently of the code ──────
    # (Every value here was worked out by counting the fixture's actual
    # links/statuses/dates by hand before running the script, not copied
    # from its output — see the PR description for the arithmetic.)

    def test_connectedness_matches_hand_count(self):
        # 9 of 11 Knowledge/ notes have >=2 total links (orphan.md and
        # premature-mature.md are the two exceptions, by design).
        self.assertAlmostEqual(self.result["components"]["connectedness"], 9 / 11)

    def test_maturity_matches_hand_count(self):
        # Knowledge/ (11) + Sources/ (1) = 12 scoped notes; 4 are mature
        # (premature-mature, healthy-linked-a, healthy-linked-b, example-source).
        self.assertAlmostEqual(self.result["components"]["maturity"], 4 / 12)

    def test_freshness_matches_hand_count(self):
        # Same 12 scoped notes; only stale-aged-out.md (updated 2025-01-01)
        # is more than 90 days before the reference date.
        self.assertAlmostEqual(self.result["components"]["freshness"], 11 / 12)

    def test_provenance_matches_hand_count(self):
        # 11 Knowledge/ notes; no-provenance.md and premature-mature.md
        # are the two missing a Captured-from/Source backlink.
        self.assertAlmostEqual(self.result["components"]["provenance"], 9 / 11)

    def test_resolution_matches_hand_count(self):
        # 11 Knowledge/ notes; has-open-questions.md contributes exactly 2
        # bullet entries, everything else contributes 0. 1 - 2/11.
        self.assertAlmostEqual(self.result["components"]["resolution"], 1 - 2 / 11)

    def test_resolution_counts_entries_not_just_presence(self):
        # Direct test of the subtle part: a page with 2 open questions
        # contributes 2, not 1 - this is what makes "floored at 0 but not
        # capped at 1" in the SKILL.md formula make sense at all.
        body = self.notes["Knowledge/has-open-questions.md"]["body"]
        self.assertEqual(_count_open_question_entries(body), 2)

    def test_confidence_low_no_open_questions_contributes_zero(self):
        body = self.notes["Knowledge/low-confidence-no-questions.md"]["body"]
        self.assertEqual(_count_open_question_entries(body), 0)

    # ── Warehouse integrity: conditional exclusion/renormalization ─────────

    def test_warehouse_excluded_when_not_in_use(self):
        self.assertIsNone(self.result["components"]["warehouse_integrity"])
        self.assertNotIn("warehouse_integrity", self.result["weights_used"])

    def test_weights_renormalize_to_one_without_warehouse(self):
        self.assertAlmostEqual(sum(self.result["weights_used"].values()), 1.0)

    def test_warehouse_included_and_renormalized_when_in_use(self):
        stats = {"total_linked_docs": 10, "corrupt": 0, "missing": 0, "dangling": 0, "drifted": 0}
        result = compute_health_score(FIXTURE_VAULT, now=NOW, warehouse_stats=stats)
        self.assertIsNotNone(result["components"]["warehouse_integrity"])
        self.assertIn("warehouse_integrity", result["weights_used"])
        self.assertAlmostEqual(sum(result["weights_used"].values()), 1.0)
        # All 6 present -> no renormalization needed -> weights match the defaults.
        self.assertAlmostEqual(result["weights_used"]["connectedness"], 0.20)
        self.assertAlmostEqual(result["weights_used"]["warehouse_integrity"], 0.20)

    def test_warehouse_integrity_clean_is_perfect_score(self):
        stats = {"total_linked_docs": 5, "corrupt": 0, "missing": 0, "dangling": 0, "drifted": 0}
        self.assertAlmostEqual(warehouse_integrity(stats), 1.0)

    def test_warehouse_integrity_corrupt_missing_dangling_are_full_penalty(self):
        stats = {"total_linked_docs": 4, "corrupt": 1, "missing": 1, "dangling": 1, "drifted": 0}
        # 3 full penalties out of 4 -> 1 - 3/4 = 0.25
        self.assertAlmostEqual(warehouse_integrity(stats), 0.25)

    def test_warehouse_integrity_drifted_is_half_penalty(self):
        stats = {"total_linked_docs": 4, "corrupt": 0, "missing": 0, "dangling": 0, "drifted": 2}
        # 2 drifted at 0.5 weight = 1.0 penalty out of 4 -> 1 - 1/4 = 0.75
        self.assertAlmostEqual(warehouse_integrity(stats), 0.75)

    def test_warehouse_integrity_floors_at_zero(self):
        stats = {"total_linked_docs": 2, "corrupt": 2, "missing": 2, "dangling": 0, "drifted": 0}
        self.assertEqual(warehouse_integrity(stats), 0.0)

    def test_warehouse_integrity_none_when_zero_linked_docs(self):
        self.assertIsNone(warehouse_integrity({"total_linked_docs": 0, "corrupt": 0, "missing": 0, "dangling": 0, "drifted": 0}))
        self.assertIsNone(warehouse_integrity(None))

    # ── Scope helper ────────────────────────────────────────────────────────
    # NOTE: this tests prefix-based scoping, which is what actually excludes
    # Maps/System/Journal here (see module docstring - the is_system_file
    # guard inside _scoped is redundant with this for every current caller).

    def test_scoped_excludes_out_of_domain_paths_by_prefix(self):
        scoped = _scoped(self.notes, ("Knowledge/", "Sources/", "Projects/"))
        self.assertNotIn("Maps/_context.md", scoped)
        self.assertNotIn("System/constitution-stub.md", scoped)
        self.assertNotIn("Journal/Daily/2026-07-01.md", scoped)
        self.assertIn("Knowledge/healthy-linked-a.md", scoped)
        self.assertIn("Sources/Papers/example-source.md", scoped)


if __name__ == "__main__":
    unittest.main(verbosity=2)
