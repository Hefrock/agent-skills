#!/usr/bin/env python3
"""Daily text archive — one entry per selected, evidence-pinned story,
written from rank.py's output and evidence.py's pinned claims.

Deliberately NOT built from script.json: script.json is written after the
narration stage, so its story text is Gemini-written for every segment that
passed grounding. This archive carries no generated words at all — the
headline and summary are the ingested source's own, verbatim — which is what
lets the weekly show treat it as ground truth for the stories it discusses.

Each entry is self-contained (it stores the excerpt, claim_id, identifiers,
category and relevance_score), so a later weekly pack can be built from the
archive files alone without the evidence store or dedup store surviving
between runs.

Pure functions plus one disk writer. stdlib only."""

import html
import json
import os
import re
import sys
from urllib.parse import quote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import source_registry  # noqa: E402

GROUPS = (("top_three", "Top stories"), ("quick_hits", "Quick hits"))


def _identifier_fields(source_id: str) -> dict:
    """source_id is evidence-pinning-mcp's canonical id: 'doi:<doi>',
    'pmid:<pmid>', or 'url:<hash>'. Expose the DOI/PMID itself when there is
    one; a url: id carries no readable identifier, so none is added."""
    id_type, _, value = source_id.partition(":")
    fields = {"id_type": id_type}
    if id_type == "doi":
        fields["doi"] = value
    elif id_type == "pmid":
        fields["pmid"] = value
    return fields


def build_archive_entries(rank_result: dict, pinned: dict, registry: dict, run_date: str) -> list[dict]:
    """Entries for every selected story that has a pinned claim, top_three
    first then quick_hits, in rank order. A selected story with no pinned
    claim (no summary, or a pin failure) is left out — the archive only
    records stories whose text can be traced to a claim_id."""
    claim_index = {
        entry["item"]["canonical_id"]: {"claim_id": entry["claim_id"], "source_id": entry["source_id"]}
        for entry in pinned["pinned"]
    }

    entries = []
    for group, _ in GROUPS:
        position = 0
        for item in rank_result[group]:
            claim = claim_index.get(item["canonical_id"])
            if claim is None:
                continue
            position += 1
            source = source_registry.get_source(registry, item["source_key"])
            entries.append({
                "run_date": run_date,
                "rank_group": group,
                "position": position,
                "headline": item["title"],
                "summary": item["summary"],
                "url": item["url"],
                "published_date": item["published_date"],
                "source_key": item["source_key"],
                "source_name": source["name"],
                "category": source["category"],
                "source_id": claim["source_id"],
                "claim_id": claim["claim_id"],
                "canonical_id": item["canonical_id"],
                "relevance_score": item["relevance_score"],
                **_identifier_fields(claim["source_id"]),
            })
    return entries


# Everything rendered below came from a third-party feed, so none of it is
# trusted: collapse to one line (no line-start constructs from embedded
# newlines), HTML-escape, backslash-escape inline markdown, and neutralize a
# leading block marker. The JSON archive keeps the raw text; only this
# rendering is escaped.
_INLINE_MD = re.compile(r"([\\`*_\[\]~|])")
_LEADING_BLOCK = re.compile(r"^(\s*)([#>+\-=]|\d+[.)])")


def _oneline(text: str) -> str:
    return " ".join(str(text).split())


def _escape_md(text: str) -> str:
    escaped = _INLINE_MD.sub(r"\\\1", html.escape(_oneline(text), quote=False))
    return _LEADING_BLOCK.sub(lambda m: m.group(1) + "\\" + m.group(2), escaped)


def _safe_url(url: str) -> str | None:
    """http(s) only; parentheses, spaces and angle brackets percent-encoded so
    the URL cannot terminate the markdown link early."""
    candidate = str(url).strip()
    if urlsplit(candidate).scheme.lower() not in ("http", "https"):
        return None
    return quote(candidate, safe="%:/?#[]@!$&'*+,;=-._~")


def render_markdown(entries: list[dict], run_date: str, show_name: str = "Healthcare AI Briefing") -> str:
    lines = [f"# {show_name}: {run_date}", ""]
    if not entries:
        lines.append("No stories were selected for this day.")
        return "\n".join(lines) + "\n"

    lines += [
        "Headlines and summaries below are each source's own text, not written by a language model. "
        "Every story carries a claim ID that traces to its pinned source.",
        "",
    ]
    for group, heading in GROUPS:
        group_entries = [e for e in entries if e["rank_group"] == group]
        if not group_entries:
            continue
        lines += [f"## {heading}", ""]
        for e in group_entries:
            headline = _escape_md(e["headline"])
            url = _safe_url(e["url"])
            lines += [
                f"### [{headline}]({url})" if url else f"### {headline}",
                "",
                _escape_md(e["summary"]),
                "",
                f"- **Source:** {_escape_md(e['source_name'])} ({e['category']}), published {e['published_date']}",
                f"- **Claim ID:** {e['claim_id']}",
            ]
            if "doi" in e:
                lines.append(f"- **DOI:** {e['doi']}")
            if "pmid" in e:
                lines.append(f"- **PMID:** {e['pmid']}")
            lines.append("")
    return "\n".join(lines)


def write_archive(entries: list[dict], markdown: str, out_dir: str, run_date: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, f"{run_date}.json")
    markdown_path = os.path.join(out_dir, f"{run_date}.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return {"json_path": json_path, "markdown_path": markdown_path}
