#!/usr/bin/env python3
"""Live network smoke test for broadcast's ingest adapters and
dedup_store.embed_text().

NOT part of the regular test suite (test_ingest.py, test_dedup_store.py,
etc. stay hermetic and network-free by design) — this is a separate,
manually-triggered check that exercises the real external APIs. It exists
to close two specific verification gaps: fetch_pubmed/fetch_arxiv/fetch_rss
had only ever been confirmed to correctly SURFACE a blocked-network error
(this dev sandbox's egress policy), never to return a real successful
response; embed_text() had never been executed against the live Gemini API
at all. Passing here is the actual close of those gaps — not an inference
by analogy from a different code path succeeding elsewhere.

Also doubles as the real verification for the three RSS feed_url values,
which config/sources.json marks feed_url_verified: false — a wrong URL
here surfaces as a real, actionable failure, not a silent "trust me."

Run manually via the "broadcast live smoke test" GitHub Actions workflow
(workflow_dispatch), or locally from an environment with real network
egress: python live_smoke_test.py

Exits non-zero if any check that should be reachable fails. The Gemini
embeddings check is SKIPPED (not failed) if GEMINI_API_KEY isn't set in
the environment, since that secret is configured separately and its
absence isn't itself a failure of this script."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest  # noqa: E402
import dedup_store  # noqa: E402
import source_registry  # noqa: E402

passed = 0
failed = 0
skipped = 0


def check(label, fn):
    global passed, failed
    try:
        detail = fn()
        print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))
        passed += 1
    except Exception as e:
        print(f"  FAIL  {label} — {type(e).__name__}: {e}")
        failed += 1


def skip(label, reason):
    global skipped
    print(f"  SKIP  {label} — {reason}")
    skipped += 1


print("── PubMed ──────────────────────────────────────────────────")


def _pubmed():
    items = ingest.fetch_pubmed("FHIR AND clinical decision support", max_results=3)
    assert len(items) > 0, "query returned zero items"
    assert items[0]["title"], "first item has no title"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_pubmed returns real results", _pubmed)

print("\n── arXiv ────────────────────────────────────────────────────")


def _arxiv():
    items = ingest.fetch_arxiv("cat:cs.AI AND abs:clinical", max_results=3)
    assert len(items) > 0, "query returned zero items"
    assert items[0]["title"], "first item has no title"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_arxiv returns real results", _arxiv)

print("\n── medRxiv ──────────────────────────────────────────────────")


def _medrxiv():
    items = ingest.fetch_medrxiv(days=14, max_results=5)
    assert len(items) > 0, "no postings in the last 14 days (unlikely — check the API response shape)"
    assert items[0]["title"], "first item has no title"
    assert items[0]["id_hint"].startswith("doi:"), "first item has no doi id_hint"
    return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"


check("fetch_medrxiv returns real results", _medrxiv)

print("\n── Industry RSS feeds ──────────────────────────────────────")
registry = source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
for key in ("stat_news", "fierce_healthcare", "healthcare_it_news"):
    source = source_registry.get_source(registry, key)

    def _rss(feed_url=source["feed_url"], source_key=key):
        items = ingest.fetch_rss(feed_url, source_key)
        assert len(items) > 0, "feed returned zero items"
        assert items[0]["title"], "first item has no title"
        return f"{len(items)} items, e.g. '{items[0]['title'][:60]}'"

    check(f"fetch_rss({key}) returns real results — feed_url_verified was false, this is the verification", _rss)

print("\n── Gemini embeddings ────────────────────────────────────────")
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    skip("embed_text returns a real embedding vector", "GEMINI_API_KEY not set in environment")
else:
    def _embed():
        vec = dedup_store.embed_text("Test sentence for embedding verification.", api_key)
        assert isinstance(vec, list) and len(vec) > 0, "embedding vector is empty or not a list"
        assert all(isinstance(x, (int, float)) for x in vec), "embedding contains non-numeric values"
        return f"{len(vec)}-dim vector, first values: {[round(v, 4) for v in vec[:3]]}"

    check("embed_text returns a real embedding vector", _embed)

print(f"\n{'─' * 62}\n  {passed} passed  —  {failed} failed  —  {skipped} skipped")
if failed > 0:
    sys.exit(1)
