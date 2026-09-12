#!/usr/bin/env python3
"""
Unit tests for dedup_store.py.

Covers canonical ID extraction (DOI/PMID/URL fallback), cosine similarity,
same-day vs. rolling-window classification (including that same-day always
wins over a rolling match, and that the exact-ID path and the embedding
path both work independently), pruning, and store persistence (atomic
write, round-trip). embed_text() — the one network-touching function — is
deliberately not covered here; everything else in the module takes
pre-computed embedding vectors as plain arguments, which is the entire
point of the split (see the module docstring).

Stdlib only (unittest). Run: python test_dedup_store.py
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "dedup_store.py")

spec = importlib.util.spec_from_file_location("dedup_store", SCRIPT)
dedup_store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dedup_store)


def vec(*values):
    return list(values)


class StoryId(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(dedup_store.story_id("doi:10.1/a"), dedup_store.story_id("doi:10.1/a"))

    def test_different_ids_hash_differently(self):
        self.assertNotEqual(dedup_store.story_id("doi:10.1/a"), dedup_store.story_id("doi:10.1/b"))

    def test_matches_what_record_story_assigns(self):
        # rank.py builds same_day_entries dicts with this before the item is
        # persisted — must match record_story()'s own assignment exactly.
        store = dedup_store.record_story({"entries": []}, "doi:10.1/a", "Title A", vec(1, 0, 0), "2026-09-01")
        self.assertEqual(store["entries"][0]["story_id"], dedup_store.story_id("doi:10.1/a"))


class CanonicalizeId(unittest.TestCase):
    def test_extracts_doi_from_url(self):
        self.assertEqual(dedup_store.canonicalize_id("https://doi.org/10.1000/xyz1"), "doi:10.1000/xyz1")

    def test_extracts_pmid_from_pubmed_url(self):
        self.assertEqual(dedup_store.canonicalize_id("https://pubmed.ncbi.nlm.nih.gov/12345678"), "pmid:12345678")

    def test_falls_back_to_url_hash(self):
        result = dedup_store.canonicalize_id("https://statnews.com/some-article")
        self.assertTrue(result.startswith("url:"))

    def test_url_hash_is_deterministic(self):
        a = dedup_store.canonicalize_id("https://statnews.com/x")
        b = dedup_store.canonicalize_id("https://statnews.com/x")
        self.assertEqual(a, b)

    def test_different_urls_hash_differently(self):
        a = dedup_store.canonicalize_id("https://statnews.com/x")
        b = dedup_store.canonicalize_id("https://statnews.com/y")
        self.assertNotEqual(a, b)

    def test_id_hint_forces_doi_over_url_parsing(self):
        result = dedup_store.canonicalize_id("https://example.com/report", id_hint="doi:10.2000/forced")
        self.assertEqual(result, "doi:10.2000/forced")

    def test_id_hint_forces_pmid(self):
        result = dedup_store.canonicalize_id("https://example.com/report", id_hint="pmid:999")
        self.assertEqual(result, "pmid:999")

    def test_doi_lowercased(self):
        result = dedup_store.canonicalize_id("https://doi.org/10.1000/XYZ1")
        self.assertEqual(result, "doi:10.1000/xyz1")

    def test_id_hint_forces_docket(self):
        result = dedup_store.canonicalize_id("https://www.fda.gov/some-guidance", id_hint="docket:FDA-2026-D-0042")
        self.assertEqual(result, "docket:FDA-2026-D-0042")

    def test_docket_not_lowercased(self):
        # Unlike DOIs, docket numbers are conventionally uppercase
        # (FDA-2026-D-0042) — preserve case rather than normalizing it away.
        result = dedup_store.canonicalize_id("https://example.com/x", id_hint="docket:fda-2026-d-0042")
        self.assertEqual(result, "docket:fda-2026-d-0042")


class CosineSimilarity(unittest.TestCase):
    def test_identical_vectors_are_1(self):
        self.assertAlmostEqual(dedup_store.cosine_similarity(vec(1, 0, 0), vec(1, 0, 0)), 1.0)

    def test_orthogonal_vectors_are_0(self):
        self.assertAlmostEqual(dedup_store.cosine_similarity(vec(1, 0), vec(0, 1)), 0.0)

    def test_opposite_vectors_are_negative_1(self):
        self.assertAlmostEqual(dedup_store.cosine_similarity(vec(1, 0), vec(-1, 0)), -1.0)

    def test_zero_vector_returns_0_not_a_crash(self):
        self.assertEqual(dedup_store.cosine_similarity(vec(0, 0), vec(1, 1)), 0.0)

    def test_dimension_mismatch_raises(self):
        with self.assertRaises(ValueError):
            dedup_store.cosine_similarity(vec(1, 2), vec(1, 2, 3))

    def test_scaled_vectors_have_same_similarity_as_unscaled(self):
        a, b = vec(1, 2, 3), vec(2, 4, 6)
        self.assertAlmostEqual(dedup_store.cosine_similarity(a, b), 1.0)


class Classify(unittest.TestCase):
    def setUp(self):
        self.store = {"entries": []}

    def test_new_story_with_empty_store(self):
        result = dedup_store.classify_story("doi:10.1/a", "Title A", vec(1, 0, 0), self.store, "2026-09-01")
        self.assertEqual(result["classification"], "new")

    def test_same_day_duplicate_by_exact_canonical_id(self):
        same_day = [{"story_id": "s1", "canonical_id": "doi:10.1/a", "title": "Title A", "embedding": vec(0, 1, 0)}]
        result = dedup_store.classify_story("doi:10.1/a", "Title A (rehosted)", vec(1, 0, 0), self.store, "2026-09-01", same_day_entries=same_day)
        self.assertEqual(result["classification"], "same_day_duplicate")
        self.assertEqual(result["matched_story_id"], "s1")

    def test_same_day_duplicate_by_embedding_similarity(self):
        same_day = [{"story_id": "s1", "canonical_id": "url:aaa", "title": "Outlet A's headline", "embedding": vec(1, 0, 0)}]
        # Different canonical_id (different outlet's URL), near-identical embedding.
        result = dedup_store.classify_story("url:bbb", "Outlet B's slightly different headline", vec(0.999, 0.001, 0), self.store, "2026-09-01", same_day_entries=same_day)
        self.assertEqual(result["classification"], "same_day_duplicate")

    def test_same_day_below_threshold_is_new(self):
        same_day = [{"story_id": "s1", "canonical_id": "url:aaa", "title": "Unrelated story", "embedding": vec(1, 0, 0)}]
        result = dedup_store.classify_story("url:bbb", "A different story entirely", vec(0, 1, 0), self.store, "2026-09-01", same_day_entries=same_day)
        self.assertEqual(result["classification"], "new")

    def test_rolling_followup_by_exact_canonical_id(self):
        self.store["entries"].append({
            "story_id": "s1", "canonical_id": "doi:10.1/a", "title": "Original coverage",
            "embedding": vec(1, 0, 0), "first_seen_date": "2026-08-25", "last_seen_date": "2026-08-25", "run_ids": ["2026-08-25"],
        })
        result = dedup_store.classify_story("doi:10.1/a", "Follow-up coverage", vec(0, 1, 0), self.store, "2026-09-01")
        self.assertEqual(result["classification"], "rolling_followup")
        self.assertEqual(result["matched_story_id"], "s1")
        self.assertEqual(result["days_since_first_seen"], 7)

    def test_rolling_followup_by_embedding_similarity(self):
        self.store["entries"].append({
            "story_id": "s1", "canonical_id": "url:original", "title": "Original coverage",
            "embedding": vec(1, 0, 0), "first_seen_date": "2026-08-20", "last_seen_date": "2026-08-20", "run_ids": ["2026-08-20"],
        })
        result = dedup_store.classify_story("url:different-outlet", "Same story, different outlet", vec(0.999, 0.001, 0), self.store, "2026-09-01")
        self.assertEqual(result["classification"], "rolling_followup")
        self.assertEqual(result["days_since_first_seen"], 12)

    def test_same_day_match_wins_over_rolling_match(self):
        # A story that matches something from 10 days ago in the persisted
        # store, AND matches something ingested earlier in today's run — the
        # same-day duplicate classification must win (drop it), not the
        # rolling-followup one (which would otherwise cause it to be cited
        # twice in one episode as a "new" follow-up).
        self.store["entries"].append({
            "story_id": "old1", "canonical_id": "doi:10.1/a", "title": "Old coverage",
            "embedding": vec(1, 0, 0), "first_seen_date": "2026-08-22", "last_seen_date": "2026-08-22", "run_ids": ["2026-08-22"],
        })
        same_day = [{"story_id": "today1", "canonical_id": "doi:10.1/a", "title": "Today's first mention", "embedding": vec(1, 0, 0)}]
        result = dedup_store.classify_story("doi:10.1/a", "Today's second mention", vec(1, 0, 0), self.store, "2026-09-01", same_day_entries=same_day)
        self.assertEqual(result["classification"], "same_day_duplicate")
        self.assertEqual(result["matched_story_id"], "today1")

    def test_custom_similarity_threshold(self):
        same_day = [{"story_id": "s1", "canonical_id": "url:aaa", "title": "Story A", "embedding": vec(1, 0, 0)}]
        # 0.8 similarity: passes a loose 0.7 threshold, fails a strict 0.95 one.
        candidate_embedding = vec(0.8, 0.6, 0)
        loose = dedup_store.classify_story("url:bbb", "Story B", candidate_embedding, self.store, "2026-09-01", same_day_entries=same_day, similarity_threshold=0.7)
        strict = dedup_store.classify_story("url:bbb", "Story B", candidate_embedding, self.store, "2026-09-01", same_day_entries=same_day, similarity_threshold=0.95)
        self.assertEqual(loose["classification"], "same_day_duplicate")
        self.assertEqual(strict["classification"], "new")


class PruneOldEntries(unittest.TestCase):
    def test_drops_entries_outside_retention_window(self):
        store = {"entries": [
            {"story_id": "recent", "canonical_id": "a", "title": "Recent", "embedding": [], "first_seen_date": "2026-08-30", "last_seen_date": "2026-08-30", "run_ids": []},
            {"story_id": "stale", "canonical_id": "b", "title": "Stale", "embedding": [], "first_seen_date": "2026-08-01", "last_seen_date": "2026-08-01", "run_ids": []},
        ]}
        pruned = dedup_store.prune_old_entries(store, "2026-09-01", retention_days=14)
        ids = [e["story_id"] for e in pruned["entries"]]
        self.assertIn("recent", ids)
        self.assertNotIn("stale", ids)

    def test_entry_exactly_at_the_boundary_is_kept(self):
        store = {"entries": [
            {"story_id": "boundary", "canonical_id": "a", "title": "Boundary", "embedding": [], "first_seen_date": "2026-08-18", "last_seen_date": "2026-08-18", "run_ids": []},
        ]}
        # 2026-09-01 minus 14 days = 2026-08-18 exactly.
        pruned = dedup_store.prune_old_entries(store, "2026-09-01", retention_days=14)
        self.assertEqual(len(pruned["entries"]), 1)

    def test_empty_store_stays_empty(self):
        pruned = dedup_store.prune_old_entries({"entries": []}, "2026-09-01")
        self.assertEqual(pruned["entries"], [])


class RecordStory(unittest.TestCase):
    def test_adds_a_new_entry(self):
        store = {"entries": []}
        store = dedup_store.record_story(store, "doi:10.1/a", "Title A", vec(1, 0, 0), "2026-09-01")
        self.assertEqual(len(store["entries"]), 1)
        self.assertEqual(store["entries"][0]["first_seen_date"], "2026-09-01")
        self.assertEqual(store["entries"][0]["run_ids"], ["2026-09-01"])

    def test_updates_last_seen_date_on_existing_story(self):
        store = {"entries": []}
        store = dedup_store.record_story(store, "doi:10.1/a", "Title A", vec(1, 0, 0), "2026-08-20")
        store = dedup_store.record_story(store, "doi:10.1/a", "Title A", vec(1, 0, 0), "2026-09-01")
        self.assertEqual(len(store["entries"]), 1)
        self.assertEqual(store["entries"][0]["first_seen_date"], "2026-08-20")
        self.assertEqual(store["entries"][0]["last_seen_date"], "2026-09-01")
        self.assertEqual(store["entries"][0]["run_ids"], ["2026-08-20", "2026-09-01"])

    def test_recording_the_same_run_id_twice_does_not_duplicate_it(self):
        store = {"entries": []}
        store = dedup_store.record_story(store, "doi:10.1/a", "Title A", vec(1, 0, 0), "2026-09-01")
        store = dedup_store.record_story(store, "doi:10.1/a", "Title A", vec(1, 0, 0), "2026-09-01")
        self.assertEqual(store["entries"][0]["run_ids"], ["2026-09-01"])


class StorePersistence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.path = os.path.join(self.tmpdir, "nested", "store.json")

    def test_load_missing_file_returns_empty_store(self):
        store = dedup_store.load_store(self.path)
        self.assertEqual(store, {"entries": []})

    def test_save_creates_parent_directories(self):
        dedup_store.save_store({"entries": []}, self.path)
        self.assertTrue(os.path.exists(self.path))

    def test_round_trip_preserves_content(self):
        store = {"entries": []}
        store = dedup_store.record_story(store, "doi:10.1/a", "Title A", vec(1, 0, 0), "2026-09-01")
        dedup_store.save_store(store, self.path)
        loaded = dedup_store.load_store(self.path)
        self.assertEqual(loaded, store)

    def test_save_does_not_leave_a_temp_file_behind(self):
        dedup_store.save_store({"entries": []}, self.path)
        siblings = os.listdir(os.path.dirname(self.path))
        self.assertEqual(siblings, ["store.json"])


if __name__ == "__main__":
    unittest.main()
