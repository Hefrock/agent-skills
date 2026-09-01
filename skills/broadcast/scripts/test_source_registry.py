#!/usr/bin/env python3
"""
Unit tests for source_registry.py.

Covers registry validation (every required-field and referential-integrity
failure mode, each asserted with a specific error message substring — a
malformed config should fail loudly and specifically, not with a bare
KeyError three stages downstream), the relevance-score decay formula at its
boundary conditions, and throughline vs. broad_industry classification.
Also loads and validates the real config/sources.json shipped with this
skill, so a future edit to that file that breaks validation fails CI
immediately instead of surfacing as a confusing runtime error in the
ingest stage.

Stdlib only (unittest). Run: python test_source_registry.py
"""

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "source_registry.py")
REAL_CONFIG_PATH = os.path.join(HERE, "..", "config", "sources.json")

spec = importlib.util.spec_from_file_location("source_registry", SCRIPT)
source_registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source_registry)


def minimal_registry():
    return {
        "categories": {
            "regulatory": {"authority_floor": 0.9, "half_life_days": 30},
            "industry_press": {"authority_floor": 0.4, "half_life_days": 3},
        },
        "sources": [
            {"key": "fda_guidance", "name": "FDA Guidance", "category": "regulatory"},
            {"key": "stat_news", "name": "STAT News", "category": "industry_press"},
        ],
        "throughline_keywords": ["fhir", "clinical ai"],
    }


class ValidateRegistry(unittest.TestCase):
    def test_valid_minimal_registry_passes(self):
        source_registry.validate_registry(minimal_registry())  # should not raise

    def test_missing_categories_key_fails(self):
        registry = minimal_registry()
        del registry["categories"]
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_missing_sources_key_fails(self):
        registry = minimal_registry()
        del registry["sources"]
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_category_missing_authority_floor_fails(self):
        registry = minimal_registry()
        del registry["categories"]["regulatory"]["authority_floor"]
        with self.assertRaisesRegex(source_registry.RegistryValidationError, "regulatory.*authority_floor|authority_floor.*regulatory"):
            source_registry.validate_registry(registry)

    def test_category_missing_half_life_fails(self):
        registry = minimal_registry()
        del registry["categories"]["regulatory"]["half_life_days"]
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_authority_floor_above_1_fails(self):
        registry = minimal_registry()
        registry["categories"]["regulatory"]["authority_floor"] = 1.5
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_authority_floor_below_0_fails(self):
        registry = minimal_registry()
        registry["categories"]["regulatory"]["authority_floor"] = -0.1
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_authority_floor_boundary_values_pass(self):
        registry = minimal_registry()
        registry["categories"]["regulatory"]["authority_floor"] = 0.0
        registry["categories"]["industry_press"]["authority_floor"] = 1.0
        source_registry.validate_registry(registry)  # should not raise

    def test_zero_half_life_fails(self):
        registry = minimal_registry()
        registry["categories"]["regulatory"]["half_life_days"] = 0
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_negative_half_life_fails(self):
        registry = minimal_registry()
        registry["categories"]["regulatory"]["half_life_days"] = -5
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_source_missing_required_field_fails(self):
        registry = minimal_registry()
        del registry["sources"][0]["category"]
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_duplicate_source_key_fails(self):
        registry = minimal_registry()
        registry["sources"].append({"key": "fda_guidance", "name": "Duplicate", "category": "regulatory"})
        with self.assertRaisesRegex(source_registry.RegistryValidationError, "[Dd]uplicate"):
            source_registry.validate_registry(registry)

    def test_source_referencing_unknown_category_fails(self):
        registry = minimal_registry()
        registry["sources"][0]["category"] = "nonexistent_category"
        with self.assertRaises(source_registry.RegistryValidationError):
            source_registry.validate_registry(registry)

    def test_real_shipped_config_is_valid(self):
        # Loads and validates the actual config/sources.json this skill ships
        # with — catches a future hand-edit that breaks the schema, in CI,
        # rather than at ingest-stage runtime.
        source_registry.load_registry(REAL_CONFIG_PATH)

    def test_real_config_has_all_ten_handoff_sources(self):
        registry = source_registry.load_registry(REAL_CONFIG_PATH)
        keys = {s["key"] for s in registry["sources"]}
        expected = {
            "pubmed", "arxiv", "medrxiv", "fda_guidance", "regulations_gov",
            "onc_astp", "cms", "stat_news", "fierce_healthcare", "healthcare_it_news",
        }
        self.assertEqual(keys, expected)


class GetSource(unittest.TestCase):
    def setUp(self):
        self.registry = minimal_registry()

    def test_finds_existing_source(self):
        source = source_registry.get_source(self.registry, "fda_guidance")
        self.assertEqual(source["name"], "FDA Guidance")

    def test_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            source_registry.get_source(self.registry, "nonexistent")

    def test_get_category_for_source(self):
        category = source_registry.get_category_for_source(self.registry, "fda_guidance")
        self.assertEqual(category["authority_floor"], 0.9)


class RelevanceScore(unittest.TestCase):
    def test_age_zero_scores_1_regardless_of_category(self):
        self.assertAlmostEqual(source_registry.relevance_score(0, authority_floor=0.4, half_life_days=3), 1.0)
        self.assertAlmostEqual(source_registry.relevance_score(0, authority_floor=0.9, half_life_days=30), 1.0)

    def test_score_never_drops_below_authority_floor(self):
        # Even at a very large age, the score approaches but never goes below the floor.
        score = source_registry.relevance_score(10_000, authority_floor=0.4, half_life_days=3)
        self.assertGreaterEqual(score, 0.4)
        self.assertAlmostEqual(score, 0.4, places=6)

    def test_score_at_exactly_one_half_life_is_halfway_between_1_and_floor(self):
        floor = 0.4
        score = source_registry.relevance_score(3, authority_floor=floor, half_life_days=3)
        self.assertAlmostEqual(score, floor + (1.0 - floor) * 0.5)

    def test_slow_decay_category_scores_higher_than_fast_decay_at_same_age(self):
        age = 5
        slow = source_registry.relevance_score(age, authority_floor=0.9, half_life_days=30)  # regulatory
        fast = source_registry.relevance_score(age, authority_floor=0.4, half_life_days=3)   # industry_press
        self.assertGreater(slow, fast)

    def test_negative_age_raises(self):
        with self.assertRaises(ValueError):
            source_registry.relevance_score(-1, authority_floor=0.5, half_life_days=3)

    def test_score_source_item_uses_registry_category(self):
        registry = minimal_registry()
        score = source_registry.score_source_item(registry, "fda_guidance", age_days=0)
        self.assertAlmostEqual(score, 1.0)


class ClassifyTopicScope(unittest.TestCase):
    def setUp(self):
        self.keywords = ["fhir", "clinical ai", "agentic"]

    def test_matches_throughline_keyword_case_insensitively(self):
        result = source_registry.classify_topic_scope("New FHIR Interoperability Standard Released", self.keywords)
        self.assertEqual(result, "throughline")

    def test_matches_multi_word_keyword(self):
        result = source_registry.classify_topic_scope("Hospital adopts new clinical AI triage tool", self.keywords)
        self.assertEqual(result, "throughline")

    def test_no_match_falls_back_to_broad_industry(self):
        result = source_registry.classify_topic_scope("Hospital chain reports quarterly earnings", self.keywords)
        self.assertEqual(result, "broad_industry")

    def test_empty_keyword_list_always_broad_industry(self):
        result = source_registry.classify_topic_scope("Anything about FHIR", [])
        self.assertEqual(result, "broad_industry")


if __name__ == "__main__":
    unittest.main()
