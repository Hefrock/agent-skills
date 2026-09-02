#!/usr/bin/env python3
"""Distribution (Phase 8b) — turns a published episode (script.json +
episode.wav, from orchestrate.py's --data-dir) into the artifacts a
listener or a vault actually consumes: a podcast RSS feed and an Obsidian
vault note.

Same split as every other stage: build_episode_metadata()/build_feed_xml()/
build_vault_note() are pure — no I/O, no network, fully unit-testable —
and publish_episode() is the one place that touches disk, mirroring
orchestrate.py's own CLI-only-touches-disk boundary.

Scope, deliberately: this module produces the FILES a GitHub Pages source
(a docs/ folder, or a gh-pages branch) would serve — it does not commit or
push them to git itself. Publishing a podcast feed is a one-way, public,
hard-to-fully-undo action (once syndicated, a feed URL is expected to keep
working), so the actual "make this live" step is left to the caller/human
to run deliberately, the same caution this project already applies to any
other irreversible action.

Nor does this module write directly into an Obsidian vault. wiki-operator
(skills/wiki-operator/SKILL.md) only operates a vault through a live
obsidian-vault MCP connection most environments running this pipeline
won't have configured — reimplementing vault-writing here would duplicate
logic this project has already learned (see evidence_pinning_client.py's
docstring) leads to real bugs when two copies drift apart. So
build_vault_note() produces real Markdown matching wiki-operator's own
"type: source" note schema (skills/wiki-operator/assets/source.md) as a
plain file; landing it in a real vault is wiki-operator's own /source (or
/learn) flow, run by a human or a live Claude session. "Concepts
referenced" and "Quotes worth keeping" are left as empty placeholders in
that schema — filling them in requires searching the live vault for
related concept pages, which this offline generator has no access to and
shouldn't guess at.

RSS format note: plain RSS 2.0 (title/link/description/language per
channel; title/description/pubDate/enclosure/guid per item) — no
itunes:-namespaced tags yet (itunes:author, itunes:image, etc., which
some podcast apps use for extra metadata/artwork). A real, valid,
subscribable feed doesn't require them; flagged as a follow-up if better
podcast-app compatibility becomes a priority. Enclosure type is
"audio/wav", matching what audio_synth.py actually produces — no format
conversion (e.g. to MP3) happens anywhere in this pipeline.

stdlib only, matching this repo's other reference tooling."""

import argparse
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import format_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qa_gate  # noqa: E402

DEFAULT_LANGUAGE = "en-us"
DEFAULT_AUDIO_MIME_TYPE = "audio/wav"


def _rfc822_date(iso_date: str) -> str:
    """run_date is a bare ISO date (no time-of-day) — treated as midnight
    UTC for the feed's pubDate, since this pipeline doesn't track a real
    publish time, only a run date."""
    d = date.fromisoformat(iso_date)
    dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return format_datetime(dt, usegmt=True)


def build_episode_metadata(script: dict, audio_byte_length: int, audio_url: str, mime_type: str = DEFAULT_AUDIO_MIME_TYPE) -> dict:
    """script is script_gen.generate_script()'s return value.
    audio_byte_length is a plain int (e.g. os.path.getsize() on the
    written episode.wav) rather than the raw audio bytes themselves —
    keeps this decoupled from audio_synth's in-memory output shape and
    testable with a made-up number. audio_url is the public URL this
    episode's audio will be hosted at; this module has no way to know
    that itself.

    description is built mechanically from the script's own story
    segments' already-composed narration text (title + summary + source,
    per script_gen.py) — the same "distill, don't invent" principle
    every stage before this one already follows; nothing here drafts new
    prose.

    Returns one feed <item>'s worth of data: {"title", "description",
    "pub_date_rfc822", "guid", "audio_url", "audio_byte_length",
    "mime_type", "run_date"} — run_date is carried through so
    build_feed_xml() can sort chronologically without re-parsing
    pub_date_rfc822 (RFC 822 strings don't sort lexically the way ISO
    dates do)."""
    story_texts = [s["text"] for s in script["segments"] if s["segment_type"] in qa_gate.STORY_SEGMENT_TYPES]
    return {
        "title": f"{script['show_name']} — {script['run_date']}",
        "description": " ".join(story_texts),
        "pub_date_rfc822": _rfc822_date(script["run_date"]),
        "guid": f"{script['show_name']}-{script['run_date']}",
        "audio_url": audio_url,
        "audio_byte_length": audio_byte_length,
        "mime_type": mime_type,
        "run_date": script["run_date"],
    }


def build_feed_xml(episodes: list[dict], feed_config: dict) -> str:
    """episodes: a list of build_episode_metadata()'s return dicts, one
    per published episode — sorted newest-first here (standard podcast
    feed convention) by each episode's run_date, regardless of input
    order. feed_config: {"title", "link", "description", "language"
    (optional, defaults to DEFAULT_LANGUAGE), "author" (optional)}.

    Returns a complete RSS 2.0 podcast feed as an XML string. Built via
    xml.etree.ElementTree, not hand-rolled string concatenation — ET
    escapes special characters (a story title containing "&", say)
    correctly on its own; string concatenation would need to reimplement
    that escaping and could get it wrong."""
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = feed_config["title"]
    ET.SubElement(channel, "link").text = feed_config["link"]
    ET.SubElement(channel, "description").text = feed_config["description"]
    ET.SubElement(channel, "language").text = feed_config.get("language", DEFAULT_LANGUAGE)
    if feed_config.get("author"):
        ET.SubElement(channel, "author").text = feed_config["author"]

    for ep in sorted(episodes, key=lambda e: e["run_date"], reverse=True):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "description").text = ep["description"]
        ET.SubElement(item, "pubDate").text = ep["pub_date_rfc822"]
        ET.SubElement(item, "enclosure", {"url": ep["audio_url"], "length": str(ep["audio_byte_length"]), "type": ep["mime_type"]})
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = ep["guid"]

    ET.indent(rss, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(rss, encoding="unicode") + "\n"


def build_vault_note(script: dict, episode_metadata: dict) -> str:
    """Markdown matching wiki-operator's "type: source" note schema (see
    this module's docstring for why this doesn't write to a vault
    directly). Key takeaways are the same story segment texts
    build_episode_metadata() used for the feed description — one
    real list of what the episode actually said, reused rather than
    redescribed a second way."""
    story_lines = "\n".join(
        f"- {s['text']}" for s in script["segments"] if s["segment_type"] in qa_gate.STORY_SEGMENT_TYPES
    )
    return f"""---
type: source
status: draft
confidence: high
updated: {script['run_date']}
---

# {episode_metadata['title']}

- **Type:** podcast episode
- **Author:** {script['show_name']}
- **Link / DOI:** {episode_metadata['audio_url']}
- **Read on:** {script['run_date']}

## Core argument

{episode_metadata['description']}

## Key takeaways

{story_lines}

## Concepts referenced

<!-- Link to concept pages in Knowledge/ that these stories map to -->
- [[]] —

## Quotes worth keeping

<!-- Verbatim quotes with a timestamp reference, if any are worth keeping -->
"""


# ── CLI driver — the one place in this module that touches disk ─────────

def publish_episode(data_dir: str, run_date: str, publish_dir: str, base_url: str, feed_config: dict) -> dict:
    """Reads orchestrate.py's output for one already-run episode
    (data_dir/episodes/run_date/{script.json,episode.wav}) and writes
    publish_dir/episodes/<run_date>.wav, publish_dir/feed.xml, and
    publish_dir/vault_notes/<run_date>.md.

    feed-episodes.json (a JSON array of every previously-published
    episode's build_episode_metadata() dict) is this function's own
    persisted index of "what's in the feed" — kept as structured JSON
    rather than re-parsed back out of feed.xml on each run, the same
    "persist your own state as JSON, regenerate presentation formats
    from it" convention dedup_store.json already establishes. Republishing
    the same run_date replaces that episode's entry rather than
    duplicating it.

    Returns {"audio_path", "feed_path", "vault_note_path", "episode_metadata"}."""
    episode_dir = os.path.join(data_dir, "episodes", run_date)
    with open(os.path.join(episode_dir, "script.json"), "r", encoding="utf-8") as f:
        script = json.load(f)
    source_wav_path = os.path.join(episode_dir, "episode.wav")

    audio_filename = f"{run_date}.wav"
    episodes_dir = os.path.join(publish_dir, "episodes")
    os.makedirs(episodes_dir, exist_ok=True)
    dest_wav_path = os.path.join(episodes_dir, audio_filename)
    shutil.copy2(source_wav_path, dest_wav_path)

    audio_url = f"{base_url.rstrip('/')}/episodes/{audio_filename}"
    episode_metadata = build_episode_metadata(script, os.path.getsize(dest_wav_path), audio_url)

    index_path = os.path.join(publish_dir, "feed-episodes.json")
    existing = []
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing = [e for e in existing if e["run_date"] != run_date] + [episode_metadata]
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    feed_path = os.path.join(publish_dir, "feed.xml")
    with open(feed_path, "w", encoding="utf-8") as f:
        f.write(build_feed_xml(existing, feed_config))

    vault_notes_dir = os.path.join(publish_dir, "vault_notes")
    os.makedirs(vault_notes_dir, exist_ok=True)
    vault_note_path = os.path.join(vault_notes_dir, f"{run_date}.md")
    with open(vault_note_path, "w", encoding="utf-8") as f:
        f.write(build_vault_note(script, episode_metadata))

    return {
        "audio_path": dest_wav_path,
        "feed_path": feed_path,
        "vault_note_path": vault_note_path,
        "episode_metadata": episode_metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", required=True, help="The --data-dir orchestrate.py was run with (holds episodes/<date>/{script.json,episode.wav}).")
    parser.add_argument("--date", required=True, help="Run date of the already-produced episode to publish, ISO format.")
    parser.add_argument("--publish-dir", required=True, help="Output directory for GitHub-Pages-ready files. Not committed/pushed by this script — that's a deliberate, separate, human-driven step.")
    parser.add_argument("--base-url", required=True, help="Public base URL episodes will be served from (e.g. https://user.github.io/repo).")
    parser.add_argument("--feed-title", default="Healthcare AI Briefing")
    parser.add_argument("--feed-link", required=True, help="The feed's channel <link> — typically --base-url or a landing page.")
    parser.add_argument("--feed-description", default="A daily healthcare AI briefing.")
    args = parser.parse_args()

    feed_config = {"title": args.feed_title, "link": args.feed_link, "description": args.feed_description}
    result = publish_episode(args.data_dir, args.date, args.publish_dir, args.base_url, feed_config)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
