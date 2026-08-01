#!/usr/bin/env python3
"""Regression tests for check_vault.py against the fixture vault.

Run with: python skills/wiki-librarian/scripts/test_check_vault.py

Two things are worth reading before editing this file:

1. check_vault.py implements the mechanical subset of wiki-librarian's
   checks. This is the first real operational mileage the wiki system has
   had — every other wiki skill (operator/synthesizer/librarian/governor)
   is still pure prompt spec with zero automated verification (see
   knowledge-os/sitrep.md, P1). This doesn't close that gap; it closes it
   for the one skill whose rules are mechanical enough to script.

2. is_system_file() is *structurally redundant* with check_orphans() and
   the provenance check in this fixture, because both are already scoped to
   Knowledge/+Sources/+Projects/ and system files live in Maps/ or System/ —
   they'd never be scanned by those checks regardless of the exemption
   logic. That redundancy is intentional, not wasted: it is exactly the
   insurance wiki-librarian/SKILL.md asks for ("do not fix this check by
   widening it to the whole vault") — if scope ever *does* widen by
   mistake, is_system_file() is what still catches it. Because of that
   redundancy, testing the exemption only through run() would prove
   nothing; test_is_system_file_* below tests the classifier directly,
   which is where the actual coverage is.

   The one check where the exemption is NOT redundant is premature_mature
   (Law 6) — that check has no path-prefix scoping at all, so it was
   flagging every system file with status: mature before the exemption was
   added here (found by running this fixture during construction, not by
   inspection — see the PR that added it).
"""

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_vault import run, is_system_file, parse_frontmatter, resolve_link

FIXTURE_VAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "vault")
NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def find_notes(findings, key="note"):
    return {f[key] for f in findings}


class CheckVaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run(FIXTURE_VAULT, now=NOW)

    def test_scanned_all_fixture_notes(self):
        self.assertEqual(self.result["notes_scanned"], 17)

    # ── Check 1 — broken links ────────────────────────────────────────────

    def test_broken_link_detected(self):
        notes = find_notes(self.result["broken_links"])
        self.assertEqual(notes, {"Knowledge/broken-link.md"})

    def test_no_false_positive_broken_links(self):
        # Every other wikilink in the fixture must resolve.
        for f in self.result["broken_links"]:
            self.assertEqual(f["note"], "Knowledge/broken-link.md")

    # ── Check 2 — orphans ─────────────────────────────────────────────────

    def test_orphan_detected(self):
        notes = find_notes(self.result["orphans"])
        self.assertEqual(notes, {"Knowledge/orphan.md"})

    def test_premature_mature_is_not_also_flagged_as_orphan(self):
        # It has exactly one inbound link, by design - proves the fixture
        # isolates "premature mature" from "orphan" rather than conflating them.
        self.assertNotIn("Knowledge/premature-mature.md", find_notes(self.result["orphans"]))

    def test_healthy_pages_never_flagged_as_orphans(self):
        orphan_notes = find_notes(self.result["orphans"])
        self.assertNotIn("Knowledge/healthy-linked-a.md", orphan_notes)
        self.assertNotIn("Knowledge/healthy-linked-b.md", orphan_notes)
        self.assertNotIn("Sources/Papers/example-source.md", orphan_notes)

    # ── Check 3 — stale ───────────────────────────────────────────────────

    def test_stale_aged_out_by_date(self):
        notes = find_notes(self.result["stale"])
        self.assertIn("Knowledge/stale-aged-out.md", notes)

    def test_stale_explicit_status(self):
        notes = find_notes(self.result["stale"])
        self.assertIn("Knowledge/stale-explicit.md", notes)

    def test_exactly_two_stale_notes(self):
        # Pins the count so a scope-widening mistake elsewhere (e.g. stale
        # firing on a mature or recently-updated page) is caught immediately.
        self.assertEqual(len(self.result["stale"]), 2)

    # ── Check 6 — schema gaps ─────────────────────────────────────────────

    def test_missing_required_field_detected(self):
        gaps = self.result["schema_gaps"]["missing_required_fields"]
        by_note = {f["note"]: f["missing"] for f in gaps}
        self.assertEqual(by_note.get("Knowledge/missing-fields.md"), ["confidence"])
        self.assertEqual(len(gaps), 1)

    def test_confidence_low_missing_open_questions_detected(self):
        notes = find_notes(self.result["schema_gaps"]["confidence_low_missing_open_questions"])
        self.assertEqual(notes, {"Knowledge/low-confidence-no-questions.md"})

    def test_premature_mature_status_detected(self):
        gaps = self.result["schema_gaps"]["premature_mature_status"]
        by_note = {f["note"]: f["total_links"] for f in gaps}
        self.assertEqual(by_note, {"Knowledge/premature-mature.md": 1})

    def test_missing_provenance_detected(self):
        notes = find_notes(self.result["schema_gaps"]["missing_provenance_backlink"])
        self.assertEqual(notes, {"Knowledge/no-provenance.md", "Knowledge/premature-mature.md"})

    # ── System-file exemption (Laws 6, 7, 8) — the actual regression lock ──

    def test_is_system_file_matches_underscore_maps_files(self):
        self.assertTrue(is_system_file("Maps/_context.md"))
        self.assertTrue(is_system_file("Maps/_ask_log.md"))
        self.assertTrue(is_system_file("Maps/_gaps.md"))

    def test_is_system_file_matches_system_dir(self):
        self.assertTrue(is_system_file("System/constitution-stub.md"))
        self.assertTrue(is_system_file("System/anything.md"))

    def test_is_system_file_excludes_hand_authored_maps_pages(self):
        # The constitution is explicit: Maps/AI.md is an ordinary note, not
        # a system file, even though it lives in the same folder as _context.md.
        self.assertFalse(is_system_file("Maps/AI.md"))

    def test_is_system_file_excludes_ordinary_knowledge_pages(self):
        self.assertFalse(is_system_file("Knowledge/orphan.md"))

    def test_system_files_never_appear_in_any_finding(self):
        # Belt-and-suspenders: no matter which check produced a finding,
        # a system file must never be in it. This is the test that would
        # catch a *future* regression if premature_mature (or any new check)
        # is added without carrying the exemption forward.
        system_paths = {
            "Maps/_context.md", "Maps/_ask_log.md", "Maps/_gaps.md",
            "System/constitution-stub.md",
        }
        all_flagged_notes = set()
        all_flagged_notes |= find_notes(self.result["broken_links"])
        all_flagged_notes |= find_notes(self.result["orphans"])
        all_flagged_notes |= find_notes(self.result["stale"])
        for gap_list in self.result["schema_gaps"].values():
            all_flagged_notes |= find_notes(gap_list)
        self.assertTrue(
            system_paths.isdisjoint(all_flagged_notes),
            f"system files wrongly flagged: {system_paths & all_flagged_notes}",
        )

    def test_hand_authored_map_page_still_scanned(self):
        # Maps/AI.md must be scanned as an ordinary note (present in the
        # vault load), proving is_system_file's exclusion is narrow -
        # underscore-prefixed files only, not "everything under Maps/".
        self.assertTrue(os.path.exists(os.path.join(FIXTURE_VAULT, "Maps", "AI.md")))


class ParserUnitTests(unittest.TestCase):
    """Direct tests of the frontmatter/link helpers, independent of the fixture."""

    def test_parse_frontmatter_scalars(self):
        raw = "---\ntype: concept\nstatus: draft\n---\nBody text.\n"
        fm, body = parse_frontmatter(raw)
        self.assertEqual(fm, {"type": "concept", "status": "draft"})
        self.assertEqual(body, "Body text.\n")

    def test_parse_frontmatter_inline_list(self):
        raw = "---\ntags: [a, b, c]\n---\nBody.\n"
        fm, _ = parse_frontmatter(raw)
        self.assertEqual(fm["tags"], ["a", "b", "c"])

    def test_parse_frontmatter_missing_delimiter_returns_whole_body(self):
        raw = "No frontmatter here.\n"
        fm, body = parse_frontmatter(raw)
        self.assertEqual(fm, {})
        self.assertEqual(body, raw)

    def test_resolve_link_by_basename(self):
        notes = {"Knowledge/foo.md": {}, "Journal/Daily/2026-01-01.md": {}}
        self.assertEqual(resolve_link("Knowledge/foo", notes), "Knowledge/foo.md")
        self.assertEqual(resolve_link("foo", notes), "Knowledge/foo.md")
        self.assertIsNone(resolve_link("nonexistent", notes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
