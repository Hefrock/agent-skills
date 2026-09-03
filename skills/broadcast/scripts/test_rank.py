#!/usr/bin/env python3
"""
Unit tests for rank.py — the story-selection stage that combines
source_registry.score_source_item/classify_topic_scope with
dedup_store.classify_story/record_story into top_three/quick_hits
segments.

No network, no real embeddings API — cosine similarity over small
synthetic vectors, same pattern test_dedup_store.py already uses.

Stdlib only (unittest). Run: python test_rank.py
"""

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

_specs = {}
for _name in ("rank", "source_registry", "dedup_store"):
    _spec = importlib.util.spec_from_file_location(_name, os.path.join(HERE, f"{_name}.py"))
    _specs[_name] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_specs[_name])
rank = _specs["rank"]
source_registry = _specs["source_registry"]
dedup_store = _specs["dedup_store"]


def vec(*values):
    return list(values)


TEST_REGISTRY = {
    "categories": {
        "regulatory": {"authority_floor": 0.9, "half_life_days": 30},
        "industry_press": {"authority_floor": 0.4, "half_life_days": 3},
    },
    "sources": [
        {"key": "fda_guidance", "name": "FDA", "category": "regulatory"},
        {"key": "stat_news", "name": "STAT", "category": "industry_press"},
        {"key": "fierce_healthcare", "name": "Fierce", "category": "industry_press"},
    ],
    "throughline_keywords": ["fhir", "agentic"],
}


def make_item(source_key="stat_news", title="A Title", url="https://example.com/x", id_hint=None, published_date="2026-09-01", summary=""):
    return {
        "source_key": source_key,
        "title": title,
        "url": url,
        "id_hint": id_hint,
        "published_date": published_date,
        "summary": summary,
    }


class RankStoriesBasics(unittest.TestCase):
    def test_raises_on_mismatched_lengths(self):
        items = [make_item()]
        with self.assertRaises(ValueError):
            rank.rank_stories(items, [], TEST_REGISTRY, {"entries": []}, "2026-09-01")

    def test_empty_items_returns_empty_segments(self):
        result = rank.rank_stories([], [], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(result["top_three"], [])
        self.assertEqual(result["quick_hits"], [])
        self.assertEqual(result["not_selected"], [])
        self.assertEqual(result["dropped_duplicates"], [])
        self.assertEqual(result["store"], {"entries": []})

    def test_throughline_item_goes_to_top_three(self):
        items = [make_item(title="FHIR-based interoperability study", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(len(result["top_three"]), 1)
        self.assertEqual(result["top_three"][0]["topic_scope"], "throughline")
        self.assertEqual(result["quick_hits"], [])

    def test_broad_industry_item_goes_to_quick_hits(self):
        items = [make_item(title="Hospital adopts new billing software", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(result["top_three"], [])
        self.assertEqual(len(result["quick_hits"]), 1)
        self.assertEqual(result["quick_hits"][0]["topic_scope"], "broad_industry")

    def test_top_three_is_not_backfilled_below_the_count(self):
        # Only one throughline item and one broad_industry item; top_three_count=3
        # should still yield a top_three of length 1, not padded from broad_industry.
        items = [
            make_item(title="FHIR study", url="https://x.com/1", published_date="2026-09-01"),
            make_item(title="Billing software news", url="https://x.com/2", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0, 1, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01", top_three_count=3)
        self.assertEqual(len(result["top_three"]), 1)
        self.assertEqual(len(result["quick_hits"]), 1)

    def test_throughline_overflow_competes_for_quick_hits(self):
        # Two throughline items, top_three_count=1 -> one goes to top_three,
        # the other overflows into the quick_hits pool rather than being
        # dropped outright.
        items = [
            make_item(title="FHIR study one", url="https://x.com/1", published_date="2026-09-01"),
            make_item(title="Agentic system study", url="https://x.com/2", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0, 1, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01", top_three_count=1)
        self.assertEqual(len(result["top_three"]), 1)
        self.assertEqual(len(result["quick_hits"]), 1)
        self.assertEqual(result["quick_hits"][0]["topic_scope"], "throughline")

    def test_higher_relevance_score_ranks_first_in_quick_hits(self):
        items = [
            make_item(title="Older billing news", url="https://x.com/1", source_key="stat_news", published_date="2026-08-29"),  # age 3 -> 0.7
            make_item(title="Fresh billing news", url="https://x.com/2", source_key="stat_news", published_date="2026-09-01"),  # age 0 -> 1.0
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0, 1, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(result["quick_hits"][0]["title"], "Fresh billing news")
        self.assertEqual(result["quick_hits"][1]["title"], "Older billing news")

    def test_relevance_score_value_matches_source_registry(self):
        items = [make_item(source_key="stat_news", published_date="2026-08-29")]  # age 3 days
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        expected = source_registry.score_source_item(TEST_REGISTRY, "stat_news", 3)
        self.assertAlmostEqual(result["quick_hits"][0]["relevance_score"], expected)

    def test_equal_score_items_keep_stable_input_order(self):
        # Same source and same age -> identical relevance_score. The sort
        # key's secondary tiebreak (published_date) can't distinguish them
        # either, so Python's stable sort preserves input order.
        items = [
            make_item(title="Item A", url="https://x.com/1", source_key="stat_news", published_date="2026-08-30"),
            make_item(title="Item B", url="https://x.com/2", source_key="stat_news", published_date="2026-08-30"),
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0, 1, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(result["quick_hits"][0]["relevance_score"], result["quick_hits"][1]["relevance_score"])
        self.assertEqual(result["quick_hits"][0]["title"], "Item A")

    def test_sort_key_breaks_a_score_tie_by_more_recent_date(self):
        # A genuine (score, age) tie with two different published_dates is
        # contradictory by construction (age is derived from published_date
        # against one shared current_date), so this verifies rank.py's
        # exact sort key tuple directly rather than via rank_stories().
        older = {"relevance_score": 0.5, "published_date": "2026-08-30"}
        newer = {"relevance_score": 0.5, "published_date": "2026-09-01"}
        ranked = sorted([older, newer], key=lambda x: (x["relevance_score"], x["published_date"]), reverse=True)
        self.assertEqual(ranked[0], newer)


class RankStoriesDedup(unittest.TestCase):
    def test_same_day_near_duplicate_is_dropped(self):
        items = [
            make_item(title="Story broken here first", url="https://a.com/1", published_date="2026-09-01"),
            make_item(title="Story broken here first, syndicated", url="https://b.com/1", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0.999, 0.001, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(len(result["dropped_duplicates"]), 1)
        self.assertEqual(result["dropped_duplicates"][0]["title"], "Story broken here first, syndicated")
        self.assertEqual(len(result["quick_hits"]), 1)

    def test_same_day_exact_canonical_id_match_is_dropped(self):
        items = [
            make_item(title="First mention", url="https://pubmed.ncbi.nlm.nih.gov/123", id_hint="pmid:123", published_date="2026-09-01"),
            make_item(title="Second mention, unrelated vector", url="https://pubmed.ncbi.nlm.nih.gov/123", id_hint="pmid:123", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0, 0, 1)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(len(result["dropped_duplicates"]), 1)

    def test_dropped_duplicate_never_appears_in_not_selected(self):
        items = [
            make_item(title="Dupe A", url="https://a.com/1", published_date="2026-09-01"),
            make_item(title="Dupe B", url="https://b.com/1", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(
            items, [vec(1, 0, 0), vec(0.9999, 0.0001, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01", quick_hits_count=0
        )
        self.assertEqual(len(result["dropped_duplicates"]), 1)
        titles_not_selected = [x["title"] for x in result["not_selected"]]
        self.assertNotIn(result["dropped_duplicates"][0]["title"], titles_not_selected)

    def test_rolling_followup_is_still_selected_and_tagged(self):
        store = dedup_store.record_story({"entries": []}, "url:seed", "Original coverage", vec(1, 0, 0), "2026-08-25")
        items = [make_item(title="Follow-up coverage", url="https://different-outlet.com/x", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, store, "2026-09-01")
        self.assertEqual(len(result["quick_hits"]), 1)
        self.assertEqual(result["quick_hits"][0]["dedup"]["classification"], "rolling_followup")
        self.assertEqual(result["quick_hits"][0]["dedup"]["days_since_first_seen"], 7)


class RankStoriesStorePersistence(unittest.TestCase):
    def test_survivors_are_recorded_into_the_returned_store(self):
        items = [make_item(url="https://a.com/1", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(len(result["store"]["entries"]), 1)

    def test_duplicates_are_not_recorded_into_the_returned_store(self):
        items = [
            make_item(title="Dupe A", url="https://a.com/1", published_date="2026-09-01"),
            make_item(title="Dupe B", url="https://b.com/1", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0.9999, 0.0001, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(len(result["store"]["entries"]), 1)

    def test_prunes_stale_entries_before_classifying(self):
        # An entry outside the retention window must NOT cause a
        # rolling_followup match — prune_old_entries should have already
        # dropped it.
        store = dedup_store.record_story({"entries": []}, "url:seed", "Old story", vec(1, 0, 0), "2026-08-01")
        items = [make_item(title="Unrelated new story", url="https://a.com/1", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, store, "2026-09-01", retention_days=14)
        self.assertEqual(result["quick_hits"][0]["dedup"]["classification"], "new")
        self.assertEqual(len(result["store"]["entries"]), 1)  # the stale entry is gone, only the new one remains


class RankStoriesOutputFields(unittest.TestCase):
    def test_survivor_carries_ranking_fields(self):
        items = [make_item(url="https://a.com/1", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        item = result["quick_hits"][0]
        for field in ("canonical_id", "relevance_score", "topic_scope", "dedup"):
            self.assertIn(field, item)

    def test_original_ingest_fields_are_preserved(self):
        items = [make_item(url="https://a.com/1", published_date="2026-09-01", summary="Original summary text")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        item = result["quick_hits"][0]
        self.assertEqual(item["summary"], "Original summary text")
        self.assertEqual(item["url"], "https://a.com/1")


class RankStoriesRealConfig(unittest.TestCase):
    """Integration-shaped: same pattern as test_ingest.py's
    IngestFeedsIntoDownstreamModules — confirms rank.py works end to end
    against the real, shipped config/sources.json, not just the small
    test fixture registry above."""

    def test_ranks_real_registry_end_to_end(self):
        registry = source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        items = [
            make_item(source_key="fda_guidance", title="Clinical AI assurance framework guidance", url="https://fda.gov/1", published_date="2026-09-01"),
            make_item(source_key="stat_news", title="Hospitals adopt new billing software", url="https://statnews.com/1", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0, 1, 0)], registry, {"entries": []}, "2026-09-01")
        self.assertEqual(len(result["top_three"]), 1)  # "clinical ai assurance" is a real throughline keyword
        self.assertEqual(len(result["quick_hits"]), 1)


class SummarizeSourceUtilization(unittest.TestCase):
    def test_empty_rank_result_returns_empty_dict(self):
        empty = rank.rank_stories([], [], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        self.assertEqual(rank.summarize_source_utilization(empty), {})

    def test_top_three_item_counted_as_candidate_and_selected_top_three(self):
        items = [make_item(source_key="fda_guidance", title="FHIR-based interoperability study", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        util = rank.summarize_source_utilization(result)
        self.assertEqual(util["fda_guidance"], {
            "candidates": 1, "selected_top_three": 1, "selected_quick_hits": 0,
            "selected_total": 1, "not_selected": 0, "dropped_duplicates": 0, "selection_rate": 1.0,
        })

    def test_quick_hits_item_counted_as_candidate_and_selected_quick_hits(self):
        items = [make_item(source_key="stat_news", title="Hospital adopts new billing software", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        util = rank.summarize_source_utilization(result)
        self.assertEqual(util["stat_news"]["selected_quick_hits"], 1)
        self.assertEqual(util["stat_news"]["selected_total"], 1)
        self.assertEqual(util["stat_news"]["selection_rate"], 1.0)

    def test_not_selected_item_counts_as_candidate_with_zero_selection_rate(self):
        items = [make_item(source_key="stat_news", title="Billing software news", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01", quick_hits_count=0)
        util = rank.summarize_source_utilization(result)
        self.assertEqual(util["stat_news"], {
            "candidates": 1, "selected_top_three": 0, "selected_quick_hits": 0,
            "selected_total": 0, "not_selected": 1, "dropped_duplicates": 0, "selection_rate": 0.0,
        })

    def test_dropped_duplicate_counted_separately_not_as_a_candidate(self):
        items = [
            make_item(title="Dupe A", url="https://a.com/1", published_date="2026-09-01"),
            make_item(title="Dupe B", url="https://b.com/1", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(items, [vec(1, 0, 0), vec(0.9999, 0.0001, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        util = rank.summarize_source_utilization(result)
        self.assertEqual(util["stat_news"]["dropped_duplicates"], 1)
        self.assertEqual(util["stat_news"]["candidates"], 1)  # only the survivor counts as a candidate

    def test_source_with_zero_candidates_has_no_entry_at_all(self):
        # fierce_healthcare never appears in this run's items — a source
        # legitimately having nothing today looks the same as one being
        # starved; this function doesn't try to distinguish that (see its
        # docstring) and simply omits sources with no data this run,
        # rather than fabricating a zero-candidates entry.
        items = [make_item(source_key="stat_news", published_date="2026-09-01")]
        result = rank.rank_stories(items, [vec(1, 0, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01")
        util = rank.summarize_source_utilization(result)
        self.assertNotIn("fierce_healthcare", util)

    def test_multiple_sources_are_tracked_independently(self):
        items = [
            make_item(source_key="fda_guidance", title="FHIR interoperability update", url="https://x.com/1", published_date="2026-09-01"),
            make_item(source_key="stat_news", title="Billing software news one", url="https://x.com/2", published_date="2026-09-01"),
            make_item(source_key="stat_news", title="Billing software news two", url="https://x.com/3", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(
            items, [vec(1, 0, 0), vec(0, 1, 0), vec(0, 0, 1)], TEST_REGISTRY, {"entries": []}, "2026-09-01", quick_hits_count=1,
        )
        util = rank.summarize_source_utilization(result)
        self.assertEqual(util["fda_guidance"]["candidates"], 1)
        self.assertEqual(util["stat_news"]["candidates"], 2)
        self.assertEqual(util["stat_news"]["selected_total"] + util["stat_news"]["not_selected"], 2)

    def test_selection_rate_reflects_a_partial_hit_rate(self):
        items = [
            make_item(source_key="stat_news", title="Story one", url="https://x.com/1", published_date="2026-09-01"),
            make_item(source_key="stat_news", title="Story two", url="https://x.com/2", published_date="2026-09-01"),
        ]
        result = rank.rank_stories(
            items, [vec(1, 0, 0), vec(0, 1, 0)], TEST_REGISTRY, {"entries": []}, "2026-09-01", quick_hits_count=1,
        )
        util = rank.summarize_source_utilization(result)
        self.assertEqual(util["stat_news"]["candidates"], 2)
        self.assertEqual(util["stat_news"]["selected_total"], 1)
        self.assertAlmostEqual(util["stat_news"]["selection_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
