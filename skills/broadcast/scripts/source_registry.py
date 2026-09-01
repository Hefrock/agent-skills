#!/usr/bin/env python3
"""Source registry and relevance-scoring rubric for the daily healthcare AI
briefing (Phase 3a — see the design handoff's "Content rubric" section).

Two independent axes, deliberately kept separate:

  - Category (a SOURCE property, from config/sources.json): determines decay
    rate and authority floor. Regulatory/peer-reviewed sources get a high
    floor and slow decay; preprint/industry-press sources get a low floor
    and fast decay. This is the "balanced hybrid of recency and authority"
    the handoff calls for, with decay varying by source type.

  - Topic scope (an ITEM property, computed per-story via keyword match):
    "throughline" (FHIR, clinical AI, agentic systems, regulatory — the
    handoff's named portfolio focus) vs. "broad_industry" (everything else
    healthcare-AI-relevant). This maps onto the segment structure downstream
    — throughline stories anchor the top-three segment, broad_industry
    populates quick hits — but computing that mapping is the ranking
    stage's job, not this module's.

The exact weighting numbers (authority floors, half-lives, the throughline
keyword list) are a documented judgment call, not a validated measurement —
the handoff says exactly this ("working default... open to correction once
run against real data"). Revisit once a real run's output can be judged
against actual episodes.

stdlib only, matching this repo's other reference tooling.
"""

import json
import math
import os

REQUIRED_CATEGORY_FIELDS = {"authority_floor", "half_life_days"}
REQUIRED_SOURCE_FIELDS = {"key", "name", "category"}


class RegistryValidationError(ValueError):
    pass


def load_registry(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        registry = json.load(f)
    validate_registry(registry)
    return registry


def validate_registry(registry: dict) -> None:
    """Raises RegistryValidationError with a specific, actionable message on
    any problem — a malformed registry should fail loudly at load time, not
    surface as a confusing KeyError three stages downstream in the pipeline."""
    if "categories" not in registry or "sources" not in registry:
        raise RegistryValidationError("Registry must have top-level 'categories' and 'sources' keys")

    for cat_name, cat_config in registry["categories"].items():
        missing = REQUIRED_CATEGORY_FIELDS - set(cat_config.keys())
        if missing:
            raise RegistryValidationError(f"Category '{cat_name}' missing required field(s): {sorted(missing)}")
        floor = cat_config["authority_floor"]
        if not (0.0 <= floor <= 1.0):
            raise RegistryValidationError(f"Category '{cat_name}' authority_floor must be in [0, 1], got {floor}")
        half_life = cat_config["half_life_days"]
        if half_life <= 0:
            raise RegistryValidationError(f"Category '{cat_name}' half_life_days must be > 0, got {half_life}")

    seen_keys = set()
    for source in registry["sources"]:
        missing = REQUIRED_SOURCE_FIELDS - set(source.keys())
        if missing:
            raise RegistryValidationError(f"Source {source.get('key', '<no key>')} missing required field(s): {sorted(missing)}")
        if source["key"] in seen_keys:
            raise RegistryValidationError(f"Duplicate source key: {source['key']}")
        seen_keys.add(source["key"])
        if source["category"] not in registry["categories"]:
            raise RegistryValidationError(f"Source '{source['key']}' references unknown category '{source['category']}'")


def get_source(registry: dict, key: str) -> dict:
    for source in registry["sources"]:
        if source["key"] == key:
            return source
    raise KeyError(f"No source registered with key: {key}")


def get_category_for_source(registry: dict, source_key: str) -> dict:
    source = get_source(registry, source_key)
    return registry["categories"][source["category"]]


# ── Relevance scoring ────────────────────────────────────────────────────
#
# Exponential decay toward the category's authority floor: a brand-new item
# (age_days=0) scores 1.0 regardless of category; as age grows the score
# decays toward authority_floor at a rate set by half_life_days, never
# dropping below it. A regulatory item stays useful far longer than an
# industry-press item at the same age — that's the whole point of the floor
# + half-life split instead of a single global staleness threshold.

def relevance_score(age_days: float, authority_floor: float, half_life_days: float) -> float:
    if age_days < 0:
        raise ValueError(f"age_days must be >= 0, got {age_days}")
    decay = math.exp(-math.log(2) * age_days / half_life_days)
    return authority_floor + (1.0 - authority_floor) * decay


def score_source_item(registry: dict, source_key: str, age_days: float) -> float:
    category = get_category_for_source(registry, source_key)
    return relevance_score(age_days, category["authority_floor"], category["half_life_days"])


# ── Topic scope classification ───────────────────────────────────────────

def classify_topic_scope(text: str, throughline_keywords: list[str]) -> str:
    """'throughline' if any keyword appears as a substring (case-insensitive)
    of the text, else 'broad_industry'. Deliberately simple substring
    matching, not stemming/NLP — the keyword list is short and hand-curated
    (FHIR, clinical AI, agentic, etc.), so exact-ish phrase matching is the
    right level of sophistication for now; revisit if it under- or
    over-matches against real headlines."""
    lowered = text.lower()
    for keyword in throughline_keywords:
        if keyword.lower() in lowered:
            return "throughline"
    return "broad_industry"
