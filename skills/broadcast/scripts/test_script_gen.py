#!/usr/bin/env python3
"""Tests for script_gen.py — pure logic, no network, no spawned server
(unlike test_evidence.py/test_evidence_pinning_client.py): pinned results
here are hand-constructed dicts in evidence.py's real return shape, not
driven through a live client, since generate_script() only ever reads
that shape and never calls the client itself.

Run: python test_script_gen.py"""

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "script_gen.py")

spec = importlib.util.spec_from_file_location("script_gen", SCRIPT)
script_gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script_gen)

REGISTRY = {
    "categories": {
        "peer_reviewed": {"authority_floor": 0.9, "half_life_days": 30},
        "industry_press": {"authority_floor": 0.4, "half_life_days": 3},
    },
    "sources": [
        {"key": "pubmed", "name": "PubMed / NCBI E-utilities", "category": "peer_reviewed"},
        {"key": "stat_news", "name": "STAT News", "category": "industry_press"},
    ],
    "throughline_keywords": ["fhir"],
}


def make_item(canonical_id, title="A Title", source_key="pubmed", summary="A summary."):
    return {
        "source_key": source_key,
        "title": title,
        "url": f"https://example.com/{canonical_id}",
        "id_hint": None,
        "published_date": "2026-09-01",
        "summary": summary,
        "canonical_id": canonical_id,
        "relevance_score": 0.9,
        "topic_scope": "throughline",
        "dedup": {"classification": "new"},
    }


def make_pinned_entry(item, claim_id="claim-1", source_id="url:abc"):
    return {"item": item, "source_id": source_id, "claim_id": claim_id}


def make_rank_result(top_three=(), quick_hits=()):
    return {"top_three": list(top_three), "quick_hits": list(quick_hits), "not_selected": [], "dropped_duplicates": [], "store": {}}


class FormatDateForSpeech(unittest.TestCase):
    def test_strips_leading_zero_from_day(self):
        self.assertEqual(script_gen._format_date_for_speech("2026-09-02"), "September 2, 2026")

    def test_double_digit_day_unaffected(self):
        self.assertEqual(script_gen._format_date_for_speech("2026-09-21"), "September 21, 2026")


class GenerateScript(unittest.TestCase):
    def test_intro_and_outro_always_present(self):
        result = script_gen.generate_script(make_rank_result(), {"pinned": []}, REGISTRY, "2026-09-02")
        types = [s["segment_type"] for s in result["segments"]]
        self.assertEqual(types, ["intro", "outro"])

    def test_top_three_item_included_when_pinned(self):
        item = make_item("c1", title="Story One")
        pinned = {"pinned": [make_pinned_entry(item)]}
        result = script_gen.generate_script(make_rank_result(top_three=[item]), pinned, REGISTRY, "2026-09-02")
        types = [s["segment_type"] for s in result["segments"]]
        self.assertEqual(types, ["intro", "top_three_item", "outro"])
        self.assertEqual(result["excluded_no_evidence"], [])

    def test_top_three_item_excluded_when_not_pinned(self):
        item = make_item("c1", title="Story One")
        result = script_gen.generate_script(make_rank_result(top_three=[item]), {"pinned": []}, REGISTRY, "2026-09-02")
        types = [s["segment_type"] for s in result["segments"]]
        self.assertEqual(types, ["intro", "outro"])
        self.assertEqual(result["excluded_no_evidence"], [item])

    def test_quick_hits_transition_only_emitted_when_a_quick_hit_survives(self):
        item = make_item("c1", title="Quick Hit One", source_key="stat_news")
        pinned = {"pinned": [make_pinned_entry(item)]}
        result = script_gen.generate_script(make_rank_result(quick_hits=[item]), pinned, REGISTRY, "2026-09-02")
        types = [s["segment_type"] for s in result["segments"]]
        self.assertEqual(types, ["intro", "quick_hits_transition", "quick_hits_item", "outro"])

    def test_quick_hits_transition_suppressed_when_all_quick_hits_excluded(self):
        item = make_item("c1", title="Quick Hit One", source_key="stat_news")
        result = script_gen.generate_script(make_rank_result(quick_hits=[item]), {"pinned": []}, REGISTRY, "2026-09-02")
        types = [s["segment_type"] for s in result["segments"]]
        self.assertEqual(types, ["intro", "outro"])
        self.assertEqual(result["excluded_no_evidence"], [item])

    def test_story_segment_carries_canonical_id_claim_id_and_source_id(self):
        item = make_item("c1", title="Story One")
        pinned = {"pinned": [make_pinned_entry(item, claim_id="claim-xyz", source_id="url:xyz")]}
        result = script_gen.generate_script(make_rank_result(top_three=[item]), pinned, REGISTRY, "2026-09-02")
        story_segment = result["segments"][1]
        self.assertEqual(story_segment["canonical_id"], "c1")
        self.assertEqual(story_segment["claim_id"], "claim-xyz")
        self.assertEqual(story_segment["source_id"], "url:xyz")

    def test_connective_segments_carry_no_claim_id(self):
        result = script_gen.generate_script(make_rank_result(), {"pinned": []}, REGISTRY, "2026-09-02")
        for segment in result["segments"]:
            self.assertIsNone(segment["claim_id"])
            self.assertIsNone(segment["canonical_id"])
            self.assertIsNone(segment["source_id"])

    def test_story_text_includes_title_summary_and_resolved_source_name(self):
        item = make_item("c1", title="Story One", source_key="stat_news", summary="Here is the summary.")
        pinned = {"pinned": [make_pinned_entry(item)]}
        result = script_gen.generate_script(make_rank_result(top_three=[item]), pinned, REGISTRY, "2026-09-02")
        text = result["segments"][1]["text"]
        self.assertIn("Story One", text)
        self.assertIn("Here is the summary.", text)
        self.assertIn("STAT News", text)

    def test_top_three_items_precede_quick_hits_in_segment_order(self):
        top = make_item("c1", title="Top Story")
        quick = make_item("c2", title="Quick Story", source_key="stat_news")
        pinned = {"pinned": [make_pinned_entry(top, claim_id="claim-top"), make_pinned_entry(quick, claim_id="claim-quick")]}
        result = script_gen.generate_script(make_rank_result(top_three=[top], quick_hits=[quick]), pinned, REGISTRY, "2026-09-02")
        types = [s["segment_type"] for s in result["segments"]]
        self.assertEqual(types, ["intro", "top_three_item", "quick_hits_transition", "quick_hits_item", "outro"])

    def test_multiple_top_three_items_preserve_input_order(self):
        first = make_item("c1", title="First")
        second = make_item("c2", title="Second")
        pinned = {"pinned": [make_pinned_entry(first, claim_id="claim-1"), make_pinned_entry(second, claim_id="claim-2")]}
        result = script_gen.generate_script(make_rank_result(top_three=[first, second]), pinned, REGISTRY, "2026-09-02")
        story_segments = [s for s in result["segments"] if s["segment_type"] == "top_three_item"]
        self.assertEqual([s["canonical_id"] for s in story_segments], ["c1", "c2"])

    def test_intro_counts_only_included_stories_not_pre_exclusion_counts(self):
        included = make_item("c1", title="Included")
        excluded = make_item("c2", title="Excluded")
        pinned = {"pinned": [make_pinned_entry(included)]}
        result = script_gen.generate_script(make_rank_result(top_three=[included, excluded]), pinned, REGISTRY, "2026-09-02")
        self.assertIn("1 top story", result["segments"][0]["text"])
        self.assertEqual(result["excluded_no_evidence"], [excluded])

    def test_intro_pluralizes_correctly_for_multiple_stories(self):
        a = make_item("c1", title="A")
        b = make_item("c2", title="B", source_key="stat_news")
        pinned = {"pinned": [make_pinned_entry(a, claim_id="claim-a"), make_pinned_entry(b, claim_id="claim-b")]}
        result = script_gen.generate_script(make_rank_result(top_three=[a], quick_hits=[b]), pinned, REGISTRY, "2026-09-02")
        self.assertIn("1 top story", result["segments"][0]["text"])
        self.assertIn("1 quick hit", result["segments"][0]["text"])
        self.assertNotIn("1 quick hits", result["segments"][0]["text"])

    def test_join_is_by_canonical_id_value_not_object_identity(self):
        # A pinned entry whose "item" is a distinct-but-equal-canonical_id
        # dict (not the exact same object reference as the rank_result
        # item) must still be recognized as grounding that story — the
        # index join is value-based (canonical_id), matching the
        # docstring's stated contract.
        rank_item = make_item("c1", title="Story One")
        pinned_item_copy = make_item("c1", title="Story One")
        pinned = {"pinned": [make_pinned_entry(pinned_item_copy)]}
        result = script_gen.generate_script(make_rank_result(top_three=[rank_item]), pinned, REGISTRY, "2026-09-02")
        self.assertEqual(result["excluded_no_evidence"], [])

    def test_show_name_and_run_date_are_echoed_in_the_result(self):
        result = script_gen.generate_script(make_rank_result(), {"pinned": []}, REGISTRY, "2026-09-02", show_name="Custom Show")
        self.assertEqual(result["run_date"], "2026-09-02")
        self.assertEqual(result["show_name"], "Custom Show")

    def test_custom_show_name_appears_in_intro_and_outro_text(self):
        result = script_gen.generate_script(make_rank_result(), {"pinned": []}, REGISTRY, "2026-09-02", show_name="Custom Show")
        self.assertIn("Custom Show", result["segments"][0]["text"])
        self.assertIn("Custom Show", result["segments"][-1]["text"])

    def test_empty_selection_still_produces_a_valid_intro_and_outro(self):
        result = script_gen.generate_script(make_rank_result(), {"pinned": []}, REGISTRY, "2026-09-02")
        self.assertIn("0 top stories", result["segments"][0]["text"])
        self.assertIn("0 quick hits", result["segments"][0]["text"])


if __name__ == "__main__":
    unittest.main()
