#!/usr/bin/env python3
"""Regression tests for wiki_teacher.py.

Unlike test_check_vault.py and test_health_score.py, this file does NOT
use the shared fixtures/vault - deliberately. compute_checkin() and
spans_multiple_compartments() are both pure functions over an in-memory
notes dict, and every scenario worth covering (bootstrap precedence, the
tie-margin boundary, the cap-at-2 rule, interval defaulting) needs
precise, hand-picked dates and priority combinations that would be
fragile and hard to read if threaded through the shared fixture instead.
Adding them there would also ripple into every other test file's
hand-counted totals for Projects/, the way the paused/complete fixture
notes already did once - not worth repeating for logic this is already
directly, exactly testable in isolation.

Run with: python skills/wiki-teacher/scripts/test_wiki_teacher.py
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wiki_teacher import compute_checkin, spans_multiple_compartments, portfolio_breadth, _parse_interval

NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def project(status="draft", priority=None, checkin_interval=None, updated="2026-07-01"):
    fm = {"type": "project", "status": status, "updated": updated}
    if priority is not None:
        fm["priority"] = priority
    if checkin_interval is not None:
        fm["checkin_interval"] = checkin_interval
    return {"frontmatter": fm, "body": "", "links": []}


def concept(status="draft", updated="2025-01-01"):
    return {"frontmatter": {"type": "concept", "status": status, "updated": updated}, "body": "", "links": []}


class ComputeCheckinTests(unittest.TestCase):
    # ── Nothing flaggable ───────────────────────────────────────────────────

    def test_empty_vault(self):
        self.assertEqual(compute_checkin({}, NOW), {"status": "nothing_flaggable"})

    def test_nothing_flaggable_when_all_recent(self):
        notes = {"Projects/a.md": project(priority="high", updated="2026-07-15")}  # 5 days
        self.assertEqual(compute_checkin(notes, NOW), {"status": "nothing_flaggable"})

    def test_paused_and_complete_excluded_from_flaggable(self):
        notes = {
            "Projects/paused.md": project(status="paused", updated="2025-01-01"),
            "Projects/complete.md": project(status="complete", updated="2025-01-01"),
        }
        self.assertEqual(compute_checkin(notes, NOW), {"status": "nothing_flaggable"})

    def test_non_project_type_notes_never_considered(self):
        notes = {"Knowledge/old.md": concept(updated="2020-01-01")}
        self.assertEqual(compute_checkin(notes, NOW), {"status": "nothing_flaggable"})

    # ── Bootstrap precedence ─────────────────────────────────────────────────
    # Batched, not one-per-day: with several concurrent projects, asking
    # about missing priority one at a time could take a week+ before the
    # system has enough signal to be useful. Every flaggable project
    # missing priority is surfaced together, in one pass.

    def test_needs_bootstrap_when_priority_missing(self):
        # updated 2026-07-01 = 19 days before NOW, >= default interval of 14.
        notes = {"Projects/a.md": project(updated="2026-07-01")}
        self.assertEqual(compute_checkin(notes, NOW), {"status": "needs_bootstrap", "projects": ["Projects/a.md"]})

    def test_needs_bootstrap_batches_all_missing_priority_projects_sorted_by_path(self):
        notes = {
            "Projects/b.md": project(updated="2026-07-01"),
            "Projects/a.md": project(updated="2026-07-01"),
            "Projects/c.md": project(updated="2026-07-01"),
        }
        result = compute_checkin(notes, NOW)
        self.assertEqual(
            result,
            {"status": "needs_bootstrap", "projects": ["Projects/a.md", "Projects/b.md", "Projects/c.md"]},
        )

    def test_invalid_priority_value_treated_as_missing(self):
        notes = {"Projects/a.md": project(priority="urgent", updated="2026-07-01")}
        self.assertEqual(compute_checkin(notes, NOW), {"status": "needs_bootstrap", "projects": ["Projects/a.md"]})

    def test_missing_priority_wins_over_narrowing_even_with_others_prioritized(self):
        # Only the priority-less flaggable project appears in the batch -
        # the one that already has a priority isn't missing anything, so
        # it's not part of the bootstrap ask, even though it's also flaggable.
        notes = {
            "Projects/no-priority.md": project(updated="2026-07-01"),
            "Projects/has-priority.md": project(priority="high", updated="2026-06-01"),
        }
        result = compute_checkin(notes, NOW)
        self.assertEqual(result["status"], "needs_bootstrap")
        self.assertEqual(result["projects"], ["Projects/no-priority.md"])

    # ── Narrowing: single project ───────────────────────────────────────────

    def test_single_flaggable_project_surfaced_alone(self):
        notes = {"Projects/a.md": project(priority="high", updated="2026-07-01")}
        result = compute_checkin(notes, NOW)
        self.assertEqual(result, {"status": "surfaced", "top": ["Projects/a.md"], "remainder": 0})

    # ── Narrowing: priority tier beats raw overdue ratio ────────────────────

    def test_different_tiers_only_top_tier_surfaces(self):
        # A: high, ratio 2.0 (28 days / 14). B: medium, ratio 4.0 (56 / 14) -
        # a much higher raw ratio, but priority must still win.
        notes = {
            "Projects/a-high.md": project(priority="high", checkin_interval=14, updated="2026-06-22"),
            "Projects/b-medium.md": project(priority="medium", checkin_interval=14, updated="2026-05-25"),
        }
        result = compute_checkin(notes, NOW)
        self.assertEqual(result, {"status": "surfaced", "top": ["Projects/a-high.md"], "remainder": 1})

    # ── Narrowing: the 15% tie-margin boundary ──────────────────────────────

    def test_same_tier_within_margin_both_surface(self):
        # A: ratio 2.0 (28/14). C: ratio 1.9286 (27/14). diff = 3.57% <= 15%.
        notes = {
            "Projects/a.md": project(priority="high", checkin_interval=14, updated="2026-06-22"),
            "Projects/c.md": project(priority="high", checkin_interval=14, updated="2026-06-23"),
        }
        result = compute_checkin(notes, NOW)
        self.assertEqual(result["status"], "surfaced")
        self.assertEqual(set(result["top"]), {"Projects/a.md", "Projects/c.md"})
        self.assertEqual(result["remainder"], 0)

    def test_same_tier_not_within_margin_only_top_surfaces(self):
        # A: ratio 2.0 (28/14). D: ratio 1.5 (21/14). diff = 25% > 15%.
        notes = {
            "Projects/a.md": project(priority="high", checkin_interval=14, updated="2026-06-22"),
            "Projects/d.md": project(priority="high", checkin_interval=14, updated="2026-06-29"),
        }
        result = compute_checkin(notes, NOW)
        self.assertEqual(result, {"status": "surfaced", "top": ["Projects/a.md"], "remainder": 1})

    def test_cap_at_two_even_with_three_close_candidates(self):
        # A: ratio 2.0, C: ratio 1.9 (within 5% of A), E: ratio 1.8 (within
        # 10% of A too - but only ranked[0] vs ranked[1] is ever compared,
        # so E must never appear in `top` regardless of its own margin.
        notes = {
            "Projects/a.md": project(priority="high", checkin_interval=10, updated="2026-06-30"),
            "Projects/c.md": project(priority="high", checkin_interval=10, updated="2026-07-01"),
            "Projects/e.md": project(priority="high", checkin_interval=10, updated="2026-07-02"),
        }
        result = compute_checkin(notes, NOW)
        self.assertEqual(result["status"], "surfaced")
        self.assertEqual(len(result["top"]), 2)
        self.assertNotIn("Projects/e.md", result["top"])
        self.assertEqual(result["remainder"], 1)

    # ── checkin_interval defaulting ──────────────────────────────────────────

    def test_checkin_interval_missing_defaults_to_14(self):
        self.assertEqual(_parse_interval(None), 14)

    def test_checkin_interval_invalid_defaults_to_14(self):
        self.assertEqual(_parse_interval("abc"), 14)
        self.assertEqual(_parse_interval("0"), 14)
        self.assertEqual(_parse_interval("-5"), 14)

    def test_missing_checkin_interval_behaves_as_14_in_flagging(self):
        # 19 days since update, no checkin_interval field - flaggable under
        # the default of 14, exactly like an explicit checkin_interval: 14.
        notes = {"Projects/a.md": project(priority="high", updated="2026-07-01")}
        result = compute_checkin(notes, NOW)
        self.assertEqual(result["status"], "surfaced")

    # ── explicit_request: "what should I work on" when nothing's overdue ────
    # Closes the gap between wiki-teacher's own description (which lists
    # "what should I be working on" as a trigger phrase) and what /checkin's
    # algorithm actually answered - accountability-only, silent whenever
    # nothing happened to be overdue.

    def test_suggested_never_returned_without_explicit_request(self):
        # Proves the silent default (e.g. session-start auto-check) really
        # is silent, even when a suggestion would be available.
        notes = {"Projects/a.md": project(priority="high", updated="2026-07-18")}  # 2 days, not flaggable
        self.assertEqual(compute_checkin(notes, NOW), {"status": "nothing_flaggable"})
        self.assertEqual(compute_checkin(notes, NOW, explicit_request=False), {"status": "nothing_flaggable"})

    def test_suggested_when_explicit_and_nothing_flaggable(self):
        notes = {
            "Projects/medium.md": project(priority="medium", updated="2026-07-18"),
            "Projects/high.md": project(priority="high", updated="2026-07-19"),
        }
        result = compute_checkin(notes, NOW, explicit_request=True)
        self.assertEqual(result, {"status": "suggested", "project": "Projects/high.md"})

    def test_suggested_picks_longest_untouched_among_top_tier(self):
        notes = {
            "Projects/touched-recently.md": project(priority="high", updated="2026-07-18"),
            "Projects/touched-longer-ago.md": project(priority="high", updated="2026-07-10"),
        }
        result = compute_checkin(notes, NOW, explicit_request=True)
        self.assertEqual(result, {"status": "suggested", "project": "Projects/touched-longer-ago.md"})

    def test_suggested_excludes_paused_and_complete(self):
        notes = {
            "Projects/paused-high.md": project(status="paused", priority="high", updated="2020-01-01"),
            "Projects/active-low.md": project(status="draft", priority="low", updated="2026-07-18"),
        }
        result = compute_checkin(notes, NOW, explicit_request=True)
        self.assertEqual(result, {"status": "suggested", "project": "Projects/active-low.md"})

    def test_no_signal_when_explicit_nothing_flaggable_and_no_priorities(self):
        notes = {"Projects/a.md": project(updated="2026-07-18")}  # recent, no priority
        result = compute_checkin(notes, NOW, explicit_request=True)
        self.assertEqual(result, {"status": "no_signal"})

    def test_no_signal_ignores_invalid_priority_values(self):
        notes = {"Projects/a.md": project(priority="urgent", updated="2026-07-18")}
        result = compute_checkin(notes, NOW, explicit_request=True)
        self.assertEqual(result, {"status": "no_signal"})

    def test_explicit_request_does_not_override_flaggable_bootstrap(self):
        # A flaggable project missing priority still wins even when the
        # request was explicit - staleness makes the missing signal
        # time-sensitive regardless of how the check-in was triggered.
        notes = {"Projects/a.md": project(updated="2026-07-01")}  # 19 days, flaggable
        result = compute_checkin(notes, NOW, explicit_request=True)
        self.assertEqual(result, {"status": "needs_bootstrap", "projects": ["Projects/a.md"]})


class PortfolioBreadthTests(unittest.TestCase):
    def test_empty_vault(self):
        self.assertEqual(
            portfolio_breadth({}, NOW),
            {"active_count": 0, "days_since_last_completion": None, "never_completed": True},
        )

    def test_active_count_excludes_paused_and_complete(self):
        notes = {
            "Projects/active.md": project(status="draft", updated="2026-07-01"),
            "Projects/paused.md": project(status="paused", updated="2026-07-01"),
            "Projects/complete.md": project(status="complete", updated="2026-07-01"),
        }
        result = portfolio_breadth(notes, NOW)
        self.assertEqual(result["active_count"], 1)

    def test_never_completed_when_no_complete_projects_exist(self):
        notes = {"Projects/active.md": project(status="draft", updated="2026-07-01")}
        result = portfolio_breadth(notes, NOW)
        self.assertTrue(result["never_completed"])
        self.assertIsNone(result["days_since_last_completion"])

    def test_days_since_last_completion(self):
        notes = {"Projects/done.md": project(status="complete", updated="2026-07-10")}  # 10 days ago
        result = portfolio_breadth(notes, NOW)
        self.assertFalse(result["never_completed"])
        self.assertEqual(result["days_since_last_completion"], 10)

    def test_picks_most_recent_completion_among_several(self):
        notes = {
            "Projects/older.md": project(status="complete", updated="2026-07-10"),  # 10 days ago
            "Projects/newer.md": project(status="complete", updated="2026-07-15"),  # 5 days ago
        }
        result = portfolio_breadth(notes, NOW)
        self.assertEqual(result["days_since_last_completion"], 5)

    def test_complete_project_missing_updated_is_skipped_not_crashed(self):
        notes = {
            "Projects/broken.md": {
                "frontmatter": {"type": "project", "status": "complete"},
                "body": "",
                "links": [],
            }
        }
        result = portfolio_breadth(notes, NOW)
        self.assertTrue(result["never_completed"])

    def test_non_project_and_non_complete_notes_never_count_as_completions(self):
        notes = {
            "Knowledge/done-sounding.md": concept(status="complete", updated="2026-07-01"),
            "Projects/draft.md": project(status="draft", updated="2026-07-01"),
        }
        result = portfolio_breadth(notes, NOW)
        self.assertTrue(result["never_completed"])


class SpansMultipleCompartmentsTests(unittest.TestCase):
    def _note(self, compartment=None):
        fm = {"type": "project", "status": "draft", "updated": "2026-07-01"}
        if compartment is not None:
            fm["compartment"] = compartment
        return {"frontmatter": fm, "body": "", "links": []}

    def test_single_project_declared_compartment_not_spanning(self):
        notes = {"Projects/a.md": self._note("personal")}
        self.assertFalse(spans_multiple_compartments(["Projects/a.md"], notes))

    def test_two_projects_same_compartment_not_spanning(self):
        notes = {
            "Projects/a.md": self._note("personal"),
            "Projects/b.md": self._note("personal"),
        }
        self.assertFalse(spans_multiple_compartments(["Projects/a.md", "Projects/b.md"], notes))

    def test_two_projects_different_compartments_spans(self):
        notes = {
            "Projects/a.md": self._note("personal"),
            "Projects/b.md": self._note("public-professional"),
        }
        self.assertTrue(spans_multiple_compartments(["Projects/a.md", "Projects/b.md"], notes))

    def test_undeclared_compartment_treated_as_spanning(self):
        # Conservative: an undeclared compartment is never assumed safe,
        # even when the other project's compartment is declared and identical
        # to what the undeclared one might turn out to be.
        notes = {
            "Projects/a.md": self._note("personal"),
            "Projects/b.md": self._note(None),
        }
        self.assertTrue(spans_multiple_compartments(["Projects/a.md", "Projects/b.md"], notes))

    def test_invalid_compartment_value_treated_as_spanning(self):
        notes = {
            "Projects/a.md": self._note("personal"),
            "Projects/b.md": self._note("secret"),
        }
        self.assertTrue(spans_multiple_compartments(["Projects/a.md", "Projects/b.md"], notes))


if __name__ == "__main__":
    unittest.main()
