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
    compare_runs,
    compute_health_score,
    warehouse_integrity,
    maturity,
    resolution,
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
        # Knowledge/ (11) + Sources/ (3: example-source, warehouse-distilled,
        # warehouse-dumped) = 14 scoped notes; 4 are mature (premature-mature,
        # healthy-linked-a, healthy-linked-b, example-source) - both warehouse
        # notes are status: draft, so they don't move this count, only the
        # denominator. The two Projects/ fixtures (status: paused/complete)
        # are excluded from `countable` entirely, so they change neither
        # side of this ratio - see test_maturity_excludes_paused_and_complete.
        self.assertAlmostEqual(self.result["components"]["maturity"], 4 / 14)

    def test_maturity_excludes_paused_and_complete(self):
        # Direct, isolated test of the exclusion - test_maturity_matches_
        # hand_count only proves it indirectly (the ratio staying 4/14
        # despite two more Projects/ notes existing in the fixture). A
        # synthetic notes dict of ONLY paused/complete notes should return
        # None (empty countable), not a fourth bucket or a ZeroDivisionError.
        synthetic = {
            "Projects/a.md": {"frontmatter": {"status": "paused"}, "body": "", "links": []},
            "Projects/b.md": {"frontmatter": {"status": "complete"}, "body": "", "links": []},
        }
        self.assertIsNone(maturity(synthetic))

    def test_maturity_mixed_paused_complete_and_mature_ignores_the_first_two(self):
        synthetic = {
            "Projects/a.md": {"frontmatter": {"status": "paused"}, "body": "", "links": []},
            "Projects/b.md": {"frontmatter": {"status": "complete"}, "body": "", "links": []},
            "Projects/c.md": {"frontmatter": {"status": "mature"}, "body": "", "links": []},
        }
        # Only c.md is countable -> 1/1, not 1/3.
        self.assertAlmostEqual(maturity(synthetic), 1.0)

    def test_freshness_matches_hand_count(self):
        # 16 scoped notes (14 + the two Projects/ fixtures added for the
        # paused/complete staleness exemption). Not-fresh: stale-aged-out.md
        # and both new Projects/ notes (all updated 2025-01-01) - 3 of 16.
        # freshness has no status exemption at all, matching how a mature
        # page isn't exempted either (see health_score.py's maturity()
        # docstring) - paused/complete only opt out of the maturity ratio
        # and the librarian's stale-flagging, not this metric.
        self.assertAlmostEqual(self.result["components"]["freshness"], 13 / 16)

    def test_provenance_matches_hand_count(self):
        # 11 Knowledge/ notes; no-provenance.md and premature-mature.md
        # are the two missing a Captured-from/Source backlink.
        self.assertAlmostEqual(self.result["components"]["provenance"], 9 / 11)

    def test_resolution_matches_hand_count(self):
        # 11 Knowledge/ notes; only has-open-questions.md has any open
        # question (2 entries), so 10 of 11 pages have zero.
        self.assertAlmostEqual(self.result["components"]["resolution"], 10 / 11)

    def test_resolution_does_not_floor_at_zero_on_dense_vaults(self):
        # The retired formula (1 - entries/pages) hit 0% at >=1 question per
        # page. 3 pages at 2 questions each plus 1 settled page must still
        # score 1/4, and an all-questions vault must score 0 without
        # going negative.
        q = "## Open questions\n- a\n- b\n"
        dense = {
            "Knowledge/a.md": {"frontmatter": {}, "body": q, "links": []},
            "Knowledge/b.md": {"frontmatter": {}, "body": q, "links": []},
            "Knowledge/c.md": {"frontmatter": {}, "body": q, "links": []},
            "Knowledge/d.md": {"frontmatter": {}, "body": "no questions", "links": []},
        }
        self.assertAlmostEqual(resolution(dense), 1 / 4)
        del dense["Knowledge/d.md"]
        self.assertEqual(resolution(dense), 0.0)

    def test_resolution_counts_entries_not_just_presence(self):
        # Entries are counted per bullet, so a page with 2 questions is
        # distinguishable from one with 1 (resolution() only needs ==0).
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

    # ── Run-over-run movement ──────────────────────────────────────────────

    @staticmethod
    def _run(score, **components):
        base = {"connectedness": 0.9, "maturity": 0.3, "freshness": 0.8,
                "provenance": 0.95, "resolution": 0.1, "warehouse_integrity": None}
        base.update(components)
        return {"score": score, "components": base}

    def test_compare_runs_leads_with_regressions_and_ignores_noise(self):
        prev = self._run(72.0)
        cur = self._run(71.4, maturity=0.35, freshness=0.7, provenance=0.951)
        m = compare_runs(cur, prev)
        self.assertEqual([n for n, *_ in m["regressed"]], ["freshness"])
        self.assertEqual([n for n, *_ in m["improved"]], ["maturity"])
        self.assertIn("provenance", m["steady"])  # +0.001 is below MOVE_EPSILON
        self.assertEqual(m["score_delta"], -0.6)

    def test_compare_runs_excluded_component_is_unavailable_not_a_move(self):
        prev = self._run(72.0, warehouse_integrity=1.0)
        cur = self._run(72.0)  # warehouse now None
        m = compare_runs(cur, prev)
        self.assertEqual(m["unavailable"], ["warehouse_integrity"])
        self.assertEqual(m["regressed"], [])

    def test_compare_runs_worst_regression_first(self):
        m = compare_runs(self._run(60.0, connectedness=0.5, freshness=0.7), self._run(72.0))
        self.assertEqual([n for n, *_ in m["regressed"]], ["connectedness", "freshness"])

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
