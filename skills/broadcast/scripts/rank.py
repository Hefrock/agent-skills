#!/usr/bin/env python3
"""Rank / story-selection stage for the daily healthcare AI briefing
(Phase 3c) — the piece source_registry.py's docstring calls out as "the
ranking stage's job, not this module's": combining relevance_score,
topic_scope, and dedup classification into the two segments the handoff's
episode structure names — "top three" (throughline-anchored) and "quick
hits" (everything else healthcare-AI-relevant).

Pure data transformation, no I/O and no network calls, same discipline as
every other stage in this pipeline: embeddings are computed elsewhere
(dedup_store.embed_text, a network call) and passed in already-computed,
same as dedup_store's own functions expect. This module's only job is to
combine primitives that already exist and are already independently
tested (source_registry.score_source_item/classify_topic_scope,
dedup_store.classify_story/record_story/canonicalize_id) — it doesn't
reimplement any of them.

Segment sizes: "top three" throughline slots is a real constraint (named
in source_registry.py's docstring, itself from the design handoff).
"Quick hits" count has no handoff-specified number — DEFAULT_QUICK_HITS
below is a documented judgment call, not a validated one, exactly like
source_registry.py's authority floors and half-lives; revisit once a real
run's output can be judged against actual episodes.

If there are fewer than top_three_count throughline stories on a given
day, top_three is simply shorter — it is never backfilled with
broad_industry stories to hit a fixed count. A throughline story that
doesn't make the top-three cut (there were more than top_three_count
that day) competes for a quick_hits slot on the same relevance_score
footing as broad_industry stories, rather than being dropped outright.

stdlib only, matching this repo's other reference tooling.
"""

import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dedup_store  # noqa: E402
import source_registry  # noqa: E402

DEFAULT_TOP_THREE_COUNT = 3
DEFAULT_QUICK_HITS_COUNT = 7


def rank_stories(
    items: list[dict],
    embeddings: list[list[float]],
    registry: dict,
    store: dict,
    current_date: str,
    top_three_count: int = DEFAULT_TOP_THREE_COUNT,
    quick_hits_count: int = DEFAULT_QUICK_HITS_COUNT,
    retention_days: int = dedup_store.DEFAULT_RETENTION_DAYS,
    similarity_threshold: float = dedup_store.DEFAULT_SIMILARITY_THRESHOLD,
) -> dict:
    """items is a list of ingest.py-shaped normalized items (any source);
    embeddings[i] must be the pre-computed embedding for items[i] (same
    convention as dedup_store.classify_story — this function never calls
    embed_text itself). registry is a loaded config/sources.json (via
    source_registry.load_registry). store is a loaded dedup store (via
    dedup_store.load_store) — this function prunes it, classifies every
    item against it, and records every surviving (non-duplicate) item
    into it; the caller is expected to persist the returned store via
    dedup_store.save_store(), same as every other stateful step in this
    pipeline leaves persistence to the caller.

    Returns:
      {
        "top_three": [...],       # throughline items, highest score first
        "quick_hits": [...],      # everything else selected, highest score first
        "not_selected": [...],    # considered, not a duplicate, but cut for space
        "dropped_duplicates": [...],  # same_day_duplicate — never shown, never selected
        "store": {...},           # updated store; caller must save_store() it
      }

    Every surviving item in top_three/quick_hits/not_selected carries the
    original ingest.py fields plus: canonical_id, relevance_score,
    topic_scope, and dedup (the raw classify_story() result — "new" or
    "rolling_followup", since "same_day_duplicate" items are filtered out
    into dropped_duplicates instead).

    Raises ValueError if items and embeddings aren't the same length —
    fail loudly here rather than silently misaligning story N with
    embedding N+1 three lines later."""
    if len(items) != len(embeddings):
        raise ValueError(f"items and embeddings must be the same length, got {len(items)} and {len(embeddings)}")

    store = dedup_store.prune_old_entries(store, current_date, retention_days)

    same_day_entries: list[dict] = []
    dropped_duplicates: list[dict] = []
    throughline_candidates: list[dict] = []
    broad_candidates: list[dict] = []

    for item, embedding in zip(items, embeddings):
        canonical_id = dedup_store.canonicalize_id(item["url"], item.get("id_hint"))
        # A future-dated published_date (clock skew, a regulatory effective
        # date) is clamped to "brand new" rather than raising — this is a
        # batch stage over data another stage already validated at ingest
        # time; one odd date shouldn't take down the whole day's ranking.
        age_days = max(0, (date.fromisoformat(current_date) - date.fromisoformat(item["published_date"])).days)
        score = source_registry.score_source_item(registry, item["source_key"], age_days)
        scope = source_registry.classify_topic_scope(f"{item['title']} {item['summary']}", registry["throughline_keywords"])
        dedup_result = dedup_store.classify_story(
            canonical_id, item["title"], embedding, store, current_date, same_day_entries, similarity_threshold
        )

        ranked_item = {
            **item,
            "canonical_id": canonical_id,
            "relevance_score": score,
            "topic_scope": scope,
            "dedup": dedup_result,
        }

        if dedup_result["classification"] == "same_day_duplicate":
            dropped_duplicates.append(ranked_item)
            continue

        same_day_entries.append({
            "story_id": dedup_store.story_id(canonical_id),
            "canonical_id": canonical_id,
            "title": item["title"],
            "embedding": embedding,
        })
        store = dedup_store.record_story(store, canonical_id, item["title"], embedding, current_date)

        if scope == "throughline":
            throughline_candidates.append(ranked_item)
        else:
            broad_candidates.append(ranked_item)

    # Highest score first; among ties, the more recently published item
    # first — ISO date strings sort lexicographically the same as
    # chronologically, so this needs no parsing.
    sort_key = lambda x: (x["relevance_score"], x["published_date"])  # noqa: E731

    throughline_candidates.sort(key=sort_key, reverse=True)
    top_three = throughline_candidates[:top_three_count]
    overflow = throughline_candidates[top_three_count:]

    quick_hits_pool = overflow + broad_candidates
    quick_hits_pool.sort(key=sort_key, reverse=True)
    quick_hits = quick_hits_pool[:quick_hits_count]

    selected_canonical_ids = {x["canonical_id"] for x in top_three} | {x["canonical_id"] for x in quick_hits}
    not_selected = [x for x in throughline_candidates + broad_candidates if x["canonical_id"] not in selected_canonical_ids]

    return {
        "top_three": top_three,
        "quick_hits": quick_hits,
        "not_selected": not_selected,
        "dropped_duplicates": dropped_duplicates,
        "store": store,
    }
