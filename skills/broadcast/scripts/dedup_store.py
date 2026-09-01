#!/usr/bin/env python3
"""Rolling dedup / story-continuity store for the daily healthcare AI briefing.

Two dedup layers, per the design handoff:

  - Same-day: canonical ID exact-match first (DOI/PMID/URL), then embedding
    similarity for near-duplicates — catches two outlets covering the same
    story on the same day. A true duplicate: drop it, don't cite it twice.
  - Rolling window (default 14 days): catches a story still trending days
    later, so the ingest stage can mark it a follow-up rather than
    re-reporting it as new. NOT a duplicate to drop — a classification
    signal for the script-generation stage to use.

Deliberately skill-specific, not shared infrastructure — an earlier version
of this design assumed a "paper-scout" skill existed to share this store
with; it doesn't exist anywhere in this repo, so this store only ever serves
this skill. Lives entirely outside the Obsidian vault (no query dependency
on vault search, which has a confirmed-reproducible frontmatter parser bug
unrelated to this skill but relevant to why nothing here touches it).

Embedding generation is deliberately NOT part of this module. Every
similarity check here takes a pre-computed embedding vector (a list of
floats) as a plain argument — matching and storage logic never calls out to
a network. embed_text() is the one function that does, kept separate so
match logic can be unit-tested with small synthetic vectors instead of
requiring a live embeddings API call. The pipeline is expected to call
embed_text() once per ingested item and pass the result in.

stdlib only, matching this repo's other reference tooling — no numpy; at the
vector dimensions and daily item volumes this pipeline deals with, plain
Python cosine similarity is fast enough and keeps this dependency-free.
"""

import hashlib
import json
import math
import os
import re
import time
import urllib.request
from datetime import date, datetime, timedelta

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
PMID_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", re.IGNORECASE)

DEFAULT_RETENTION_DAYS = 14
DEFAULT_SIMILARITY_THRESHOLD = 0.92


# ── Canonical ID (same shape as evidence-pinning-mcp's, reimplemented in
#    Python rather than shared across a TypeScript/Python boundary — see the
#    module docstring) ─────────────────────────────────────────────────────

def canonicalize_id(url: str, id_hint: str | None = None) -> str:
    """DOI/PMID extraction from a URL, falling back to a hash of the URL
    itself when neither is present. Deterministic, no I/O."""
    if id_hint:
        if id_hint.startswith("doi:"):
            return f"doi:{id_hint[4:].lower()}"
        if id_hint.startswith("pmid:"):
            return f"pmid:{id_hint[5:]}"
    doi_match = DOI_RE.search(url)
    if doi_match:
        return f"doi:{doi_match.group(0).lower()}"
    pmid_match = PMID_URL_RE.search(url)
    if pmid_match:
        return f"pmid:{pmid_match.group(1)}"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"url:{digest}"


# ── Pure vector math ─────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"Embedding dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Store shape ───────────────────────────────────────────────────────────
#
# {
#   "entries": [
#     {
#       "story_id": "<sha256 of canonical_id>[:16]",
#       "canonical_id": "doi:... | pmid:... | url:...",
#       "title": "...",
#       "embedding": [0.1, ...],
#       "first_seen_date": "YYYY-MM-DD",
#       "last_seen_date": "YYYY-MM-DD",
#       "run_ids": ["YYYY-MM-DD", ...]   # every run that surfaced this story
#     },
#     ...
#   ]
# }

def _story_id(canonical_id: str) -> str:
    return hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]


def load_store(path: str) -> dict:
    if not os.path.exists(path):
        return {"entries": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_store(store: dict, path: str) -> None:
    """Atomic write — temp file in the same dir, then rename — so a run that
    dies mid-write never leaves a corrupt store for the next run to load."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    os.replace(tmp_path, path)


def prune_old_entries(store: dict, current_date: str, retention_days: int = DEFAULT_RETENTION_DAYS) -> dict:
    """Drop entries whose last_seen_date has fallen outside the retention
    window. Call this once per run before classifying new items, so the
    store stays bounded and old entries don't linger forever as phantom
    rolling-window matches."""
    cutoff = date.fromisoformat(current_date) - timedelta(days=retention_days)
    kept = [e for e in store["entries"] if date.fromisoformat(e["last_seen_date"]) >= cutoff]
    return {"entries": kept}


# ── Classification ───────────────────────────────────────────────────────

def classify_story(
    canonical_id: str,
    title: str,
    embedding: list[float],
    store: dict,
    current_date: str,
    same_day_entries: list[dict] | None = None,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> dict:
    """Classify one ingested item against everything already seen.

    same_day_entries: entries already recorded THIS run (before this item),
    passed separately from `store` because they may not be saved yet — the
    caller is expected to accumulate these across a single run and pass the
    growing list in, then merge them into `store` at the end of the run via
    record_story(). Same-day matches take priority over rolling-window
    matches: a story matched today is a duplicate to drop outright, not a
    follow-up.

    Returns one of:
      {"classification": "new"}
      {"classification": "same_day_duplicate", "matched_story_id": ..., "matched_title": ...}
      {"classification": "rolling_followup", "matched_story_id": ..., "matched_title": ...,
       "first_seen_date": ..., "days_since_first_seen": N}
    """
    same_day_entries = same_day_entries or []

    # Same-day: canonical ID exact match first, then embedding similarity.
    for entry in same_day_entries:
        if entry["canonical_id"] == canonical_id:
            return {"classification": "same_day_duplicate", "matched_story_id": entry["story_id"], "matched_title": entry["title"]}
    for entry in same_day_entries:
        if cosine_similarity(embedding, entry["embedding"]) >= similarity_threshold:
            return {"classification": "same_day_duplicate", "matched_story_id": entry["story_id"], "matched_title": entry["title"]}

    # Rolling window: same idea, against the persisted store (already pruned
    # to the retention window by the caller via prune_old_entries).
    for entry in store["entries"]:
        if entry["canonical_id"] == canonical_id:
            return _rolling_followup(entry, current_date)
    for entry in store["entries"]:
        if cosine_similarity(embedding, entry["embedding"]) >= similarity_threshold:
            return _rolling_followup(entry, current_date)

    return {"classification": "new"}


def _rolling_followup(entry: dict, current_date: str) -> dict:
    days_since = (date.fromisoformat(current_date) - date.fromisoformat(entry["first_seen_date"])).days
    return {
        "classification": "rolling_followup",
        "matched_story_id": entry["story_id"],
        "matched_title": entry["title"],
        "first_seen_date": entry["first_seen_date"],
        "days_since_first_seen": days_since,
    }


def record_story(store: dict, canonical_id: str, title: str, embedding: list[float], current_date: str) -> dict:
    """Add a new entry, or update last_seen_date/run_ids on an existing one
    (a rolling-window match, or a repeat call for the same story within a
    run). Returns the updated store; does not write to disk."""
    story_id = _story_id(canonical_id)
    for entry in store["entries"]:
        if entry["story_id"] == story_id:
            entry["last_seen_date"] = current_date
            if current_date not in entry["run_ids"]:
                entry["run_ids"].append(current_date)
            return store
    store["entries"].append({
        "story_id": story_id,
        "canonical_id": canonical_id,
        "title": title,
        "embedding": embedding,
        "first_seen_date": current_date,
        "last_seen_date": current_date,
        "run_ids": [current_date],
    })
    return store


# ── Network wrapper (not used by anything above) ────────────────────────

def embed_text(text: str, api_key: str, model: str = "gemini-embedding-001", timeout: float = 15.0) -> list[float]:
    """Call the Gemini API's embedContent endpoint. The one function in this
    module that touches the network — kept separate so everything above can
    be unit-tested without it. Requires outbound access to
    generativelanguage.googleapis.com; will fail under a default-deny egress
    policy that hasn't allowlisted that host (see mcp/evidence-pinning's
    README for the same class of constraint)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent?key={api_key}"
    payload = json.dumps({"content": {"parts": [{"text": text}]}}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["embedding"]["values"]
