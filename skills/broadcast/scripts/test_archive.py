#!/usr/bin/env python3
"""Tests for archive.py — the daily text archive writer. Pure functions plus
one disk writer, all stdlib unittest, no network."""

import copy
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import archive  # noqa: E402
import source_registry  # noqa: E402

REGISTRY = source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))


def make_item(canonical_id, source_key="pubmed", title="A title", summary="A summary.", score=0.8, url=None):
    return {
        "canonical_id": canonical_id,
        "source_key": source_key,
        "title": title,
        "summary": summary,
        "url": url or f"https://example.org/{canonical_id}",
        "id_hint": None,
        "published_date": "2026-09-08",
        "relevance_score": score,
    }


def make_pinned(items_with_source_ids):
    return {
        "pinned": [
            {"item": item, "source_id": source_id, "claim_id": f"claim-{item['canonical_id']}"}
            for item, source_id in items_with_source_ids
        ],
        "skipped_no_summary": [],
        "failed": [],
    }


class BuildArchiveEntries(unittest.TestCase):
    def setUp(self):
        self.a = make_item("pmid:111", "pubmed", "Paper A", "Abstract A.", 0.9)
        self.b = make_item("pmid:222", "pubmed", "Paper B", "Abstract B.", 0.7)
        self.c = make_item("url:abc", "stat_news", "News C", "Summary C.", 0.5)
        self.rank_result = {"top_three": [self.a, self.b], "quick_hits": [self.c]}
        self.pinned = make_pinned([(self.a, "pmid:111"), (self.b, "doi:10.1000/b"), (self.c, "url:deadbeef")])

    def test_orders_top_three_then_quick_hits_with_group_and_position(self):
        entries = archive.build_archive_entries(self.rank_result, self.pinned, REGISTRY, "2026-09-08")
        self.assertEqual([e["canonical_id"] for e in entries], ["pmid:111", "pmid:222", "url:abc"])
        self.assertEqual([(e["rank_group"], e["position"]) for e in entries], [("top_three", 1), ("top_three", 2), ("quick_hits", 1)])

    def test_carries_the_fields_the_weekly_pack_needs(self):
        entry = archive.build_archive_entries(self.rank_result, self.pinned, REGISTRY, "2026-09-08")[0]
        for key in (
            "run_date", "headline", "summary", "url", "published_date", "source_key", "source_name",
            "category", "source_id", "id_type", "claim_id", "canonical_id", "relevance_score",
        ):
            self.assertIn(key, entry)
        self.assertEqual(entry["run_date"], "2026-09-08")
        self.assertEqual(entry["relevance_score"], 0.9)
        self.assertEqual(entry["category"], "peer_reviewed")
        self.assertEqual(entry["source_name"], "PubMed / NCBI E-utilities")
        self.assertEqual(entry["claim_id"], "claim-pmid:111")

    def test_summary_and_headline_are_verbatim_no_generated_words(self):
        entry = archive.build_archive_entries(self.rank_result, self.pinned, REGISTRY, "2026-09-08")[0]
        self.assertEqual(entry["headline"], self.a["title"])
        self.assertEqual(entry["summary"], self.a["summary"])

    def test_identifier_fields_follow_source_id_type(self):
        entries = archive.build_archive_entries(self.rank_result, self.pinned, REGISTRY, "2026-09-08")
        by_id = {e["canonical_id"]: e for e in entries}
        self.assertEqual(by_id["pmid:111"]["id_type"], "pmid")
        self.assertEqual(by_id["pmid:111"]["pmid"], "111")
        self.assertEqual(by_id["pmid:222"]["id_type"], "doi")
        self.assertEqual(by_id["pmid:222"]["doi"], "10.1000/b")
        self.assertEqual(by_id["url:abc"]["id_type"], "url")
        self.assertNotIn("doi", by_id["url:abc"])
        self.assertNotIn("pmid", by_id["url:abc"])

    def test_story_without_a_pinned_claim_is_left_out(self):
        pinned = make_pinned([(self.a, "pmid:111")])
        entries = archive.build_archive_entries(self.rank_result, pinned, REGISTRY, "2026-09-08")
        self.assertEqual([e["canonical_id"] for e in entries], ["pmid:111"])

    def test_does_not_mutate_its_inputs(self):
        rank_before, pinned_before = copy.deepcopy(self.rank_result), copy.deepcopy(self.pinned)
        archive.build_archive_entries(self.rank_result, self.pinned, REGISTRY, "2026-09-08")
        self.assertEqual(self.rank_result, rank_before)
        self.assertEqual(self.pinned, pinned_before)

    def test_empty_day_yields_no_entries(self):
        entries = archive.build_archive_entries({"top_three": [], "quick_hits": []}, make_pinned([]), REGISTRY, "2026-09-08")
        self.assertEqual(entries, [])


class RenderMarkdown(unittest.TestCase):
    def setUp(self):
        a = make_item("pmid:111", "pubmed", "Paper A", "Abstract A.", 0.9, url="https://pubmed.ncbi.nlm.nih.gov/111/")
        c = make_item("url:abc", "stat_news", "News [C] story", "Summary C.", 0.5)
        pinned = make_pinned([(a, "pmid:111"), (c, "url:deadbeef")])
        self.entries = archive.build_archive_entries({"top_three": [a], "quick_hits": [c]}, pinned, REGISTRY, "2026-09-08")

    def test_contains_headline_link_source_claim_and_category(self):
        md = archive.render_markdown(self.entries, "2026-09-08")
        self.assertIn("[Paper A](https://pubmed.ncbi.nlm.nih.gov/111/)", md)
        self.assertIn("Abstract A.", md)
        self.assertIn("PubMed / NCBI E-utilities", md)
        self.assertIn("claim-pmid:111", md)
        self.assertIn("peer_reviewed", md)

    def test_groups_are_headed_separately(self):
        md = archive.render_markdown(self.entries, "2026-09-08")
        self.assertLess(md.index("Top stories"), md.index("Quick hits"))

    def test_square_brackets_in_headline_cannot_break_the_link(self):
        md = archive.render_markdown(self.entries, "2026-09-08")
        self.assertIn(r"[News \[C\] story](", md)

    def test_states_that_text_is_source_summary_not_generated(self):
        md = archive.render_markdown(self.entries, "2026-09-08")
        self.assertIn("not written by a language model", md)

    def test_empty_day_says_so_instead_of_rendering_empty_sections(self):
        md = archive.render_markdown([], "2026-09-08")
        self.assertIn("No stories", md)
        self.assertNotIn("Top stories", md)


class RenderMarkdownHostileInput(unittest.TestCase):
    """Headlines, summaries and URLs come from third-party feeds."""

    def _render(self, **overrides):
        e = {
            "rank_group": "top_three", "headline": "Plain", "summary": "Plain summary.", "url": "https://x.org/a",
            "published_date": "2026-01-01", "source_name": "Src", "category": "cat", "claim_id": "k1",
        }
        e.update(overrides)
        return archive.render_markdown([e], "2026-01-01")

    def test_newline_in_headline_cannot_start_a_new_heading(self):
        md = self._render(headline="Big news\n# Evil")
        self.assertNotIn("\n# Evil", md)
        self.assertIn("### [Big news # Evil]", md)

    def test_summary_cannot_inject_headings_html_or_links(self):
        md = self._render(summary="x\n# Injected <img onerror=1> [y](javascript:alert(1))")
        self.assertNotIn("\n# Injected", md)
        self.assertNotIn("<img", md)
        # an escaped "\\]" is literal text, not link syntax
        self.assertIsNone(re.search(r"(?<!\\)\]\(javascript:", md))

    def test_summary_text_is_still_readable(self):
        md = self._render(summary="Alerts fell by 40% (p < 0.05).")
        self.assertIn("Alerts fell by 40%", md)

    def test_url_with_parens_or_spaces_cannot_break_the_link(self):
        md = self._render(url="https://x.org/a(b)c d")
        self.assertIn("](https://x.org/a%28b%29c%20d)", md)

    def test_non_http_url_is_not_linked(self):
        md = self._render(url="javascript:alert(1)")
        self.assertNotIn("](javascript:", md)
        self.assertIn("### Plain", md)

    def test_source_name_and_category_are_escaped(self):
        md = self._render(source_name="A <b>bold</b> *name*", category="c_at")
        self.assertNotIn("<b>", md)


class WriteArchive(unittest.TestCase):
    def test_writes_json_and_markdown_that_round_trip(self):
        entries = [{"run_date": "2026-09-08", "headline": "H", "canonical_id": "x"}]
        with tempfile.TemporaryDirectory() as d:
            paths = archive.write_archive(entries, "# md\n", d, "2026-09-08")
            with open(paths["json_path"], encoding="utf-8") as f:
                self.assertEqual(json.load(f), entries)
            with open(paths["markdown_path"], encoding="utf-8") as f:
                self.assertEqual(f.read(), "# md\n")
            self.assertTrue(paths["json_path"].endswith("2026-09-08.json"))
            self.assertTrue(paths["markdown_path"].endswith("2026-09-08.md"))


if __name__ == "__main__":
    unittest.main()
