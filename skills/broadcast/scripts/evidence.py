#!/usr/bin/env python3
"""Evidence-pinning integration (Phase 4) — pins each selected story from
rank.py's output as a claim against its own source, via
evidence_pinning_client.py's real MCP connection to evidence-pinning-mcp.

The mapping is not invented here: it's the one ingest.py's own module
docstring already commits to — "evidence-pinning-mcp's pin_claim (via
url/id_hint + summary as the excerpt source)". Concretely, per selected
story: register_source(item.url, item.title, item.id_hint), then
pin_claim(run_id, item.title, [that source_id], item.summary) — the
story's own headline becomes the pinned claim text, its own ingested
summary becomes the supporting excerpt. This is deliberately mechanical,
not an LLM-drafted claim extracted from the story: rank.py's output is
still close to the raw ingested item, so "the claim" and "the evidence
for it" are the same two fields every adapter already produces. A
later stage (script generation) may pin additional, more specific claims
as it drafts dialogue — this stage's job is only to make sure every
selected story has at least one grounded claim before that happens, per
the "distill, don't invent" principle: a script draft should never cite
a story that was never actually pinned to its source.

Items with no summary can't be pinned (pin_claim requires a non-empty
excerpt — the server itself rejects it) and are skipped rather than
sent to fail loudly against the server; a per-item EvidencePinningError
from the server (e.g. some other unexpected rejection) is caught and
recorded rather than aborting the whole batch, same "skip the one bad
item, not the whole run" policy ingest.py's parsers already use.

No new dependency: evidence_pinning_client.py already exists here; this
module just calls it in a loop. stdlib only."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from evidence_pinning_client import EvidencePinningError  # noqa: E402


def pin_evidence_for_stories(client, items: list[dict], run_id: str) -> dict:
    """client is a started EvidencePinningClient (see
    evidence_pinning_client.py). items is a flat list of rank.py-shaped
    survivor items (e.g. result["top_three"] + result["quick_hits"] from
    rank_stories()) — anything with url/title/summary/id_hint, ingest.py's
    normalize_item shape plus rank.py's added fields.

    Returns:
      {
        "pinned": [{"item": ..., "source_id": ..., "claim_id": ...}, ...],
        "skipped_no_summary": [item, ...],
        "failed": [{"item": ..., "error": "..."}, ...],
      }

    Idempotent per (run_id, item.title) — calling this twice for the same
    run_id and the same set of items (e.g. a retried pipeline run) re-pins
    the same claim_ids rather than creating duplicates, since both
    register_source and pin_claim are idempotent on the server side."""
    pinned = []
    skipped_no_summary = []
    failed = []

    for item in items:
        if not item.get("summary"):
            skipped_no_summary.append(item)
            continue
        try:
            source = client.register_source(item["url"], item["title"], item.get("id_hint"))
            claim = client.pin_claim(run_id, item["title"], [source["source_id"]], item["summary"])
            pinned.append({"item": item, "source_id": source["source_id"], "claim_id": claim["claim_id"]})
        except EvidencePinningError as e:
            failed.append({"item": item, "error": str(e)})

    return {"pinned": pinned, "skipped_no_summary": skipped_no_summary, "failed": failed}
