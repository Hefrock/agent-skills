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

RSS format note: RSS 2.0 (title/link/description/language per channel;
title/description/pubDate/enclosure/guid per item) plus the itunes:
namespace (http://www.itunes.com/dtds/podcast-1.0.dtd) some podcast apps
(Apple Podcasts foremost) read for category/explicit/artwork/duration —
verified live that Python's stdlib ElementTree serializes a literal
"itunes:tagname" string tag correctly (with the xmlns:itunes declaration
added as a plain attribute on the root <rss> element) without needing
its separate namespace-registration machinery, which expects Clark
notation ({uri}localname) instead and isn't what this needs here.
itunes:image (cover art) is deliberately left as an OPTIONAL, caller-
supplied field — this pipeline has no way to generate real artwork, and
Apple Podcasts won't list a show without one, but every other itunes:
tag is still valid and useful without it, so this doesn't block on
having art. Enclosure type is "audio/wav", matching what audio_synth.py
actually produces — no format conversion (e.g. to MP3) happens anywhere
in this pipeline.

stdlib only, matching this repo's other reference tooling."""

import argparse
import json
import os
import shutil
import sys
import wave
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import format_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qa_gate  # noqa: E402

DEFAULT_LANGUAGE = "en-us"
DEFAULT_AUDIO_MIME_TYPE = "audio/wav"
DEFAULT_ITUNES_CATEGORY = "Technology"
DEFAULT_ITUNES_EXPLICIT = "false"
DEFAULT_ITUNES_TYPE = "episodic"


def _format_itunes_duration(seconds: float) -> str:
    """HH:MM:SS — the format Apple's own docs recommend (plain total
    seconds is also spec-valid, but HH:MM:SS is what actually renders in
    most podcast app UIs without a client-side reformat)."""
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _wav_duration_seconds(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _rfc822_date(iso_date: str) -> str:
    """run_date is a bare ISO date (no time-of-day) — treated as midnight
    UTC for the feed's pubDate, since this pipeline doesn't track a real
    publish time, only a run date."""
    d = date.fromisoformat(iso_date)
    dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return format_datetime(dt, usegmt=True)


def build_episode_metadata(
    script: dict, audio_byte_length: int, audio_url: str, mime_type: str = DEFAULT_AUDIO_MIME_TYPE, duration_seconds: float | None = None,
) -> dict:
    """script is script_gen.generate_script()'s return value.
    audio_byte_length is a plain int (e.g. os.path.getsize() on the
    written episode.wav) rather than the raw audio bytes themselves —
    keeps this decoupled from audio_synth's in-memory output shape and
    testable with a made-up number. audio_url is the public URL this
    episode's audio will be hosted at; this module has no way to know
    that itself. duration_seconds is the same story: a plain float the
    caller measures (e.g. via _wav_duration_seconds() on the real written
    file), not derived here from raw audio. Defaults to None (itunes:
    duration is simply omitted from the feed for that episode) so every
    existing caller that doesn't know or care about duration keeps
    working unchanged.

    description is built mechanically from the script's own story
    segments' already-composed narration text (title + summary + source,
    per script_gen.py) — the same "distill, don't invent" principle
    every stage before this one already follows; nothing here drafts new
    prose. It's followed by the script's own "disclosure" segment text —
    read back out of the script rather than redeclared here, so the
    disclosure a listener hears in the audio (script_gen.py's
    DEFAULT_DISCLOSURE_TEXT) is word-for-word the same one that lands in
    the episode's public metadata, one source of truth either way. This
    is what carries Apple Podcasts' (and similar platforms') AI-use
    disclosure requirement into the metadata half of their "audio AND
    metadata" rule — the audio half is the disclosure segment itself,
    already spoken in every episode.

    Returns one feed <item>'s worth of data: {"title", "description",
    "disclosure", "pub_date_rfc822", "guid", "audio_url",
    "audio_byte_length", "mime_type", "run_date", "itunes_duration"} —
    run_date is carried through so build_feed_xml() can sort
    chronologically without re-parsing pub_date_rfc822 (RFC 822 strings
    don't sort lexically the way ISO dates do). itunes_duration is the
    HH:MM:SS-formatted string (via _format_itunes_duration()), or None."""
    story_texts = [s["text"] for s in script["segments"] if s["segment_type"] in qa_gate.STORY_SEGMENT_TYPES]
    disclosure_segment = next((s for s in script["segments"] if s["segment_type"] == "disclosure"), None)
    disclosure = disclosure_segment["text"] if disclosure_segment else ""
    description = " ".join(story_texts)
    if disclosure:
        description = f"{description} {disclosure}".strip()
    return {
        "title": f"{script['show_name']} — {script['run_date']}",
        "description": description,
        "itunes_duration": _format_itunes_duration(duration_seconds) if duration_seconds is not None else None,
        "disclosure": disclosure,
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
    order.

    feed_config: {"title", "link", "description", "language" (optional,
    defaults to DEFAULT_LANGUAGE), "author" (optional, also used as the
    itunes:author fallback if itunes_author isn't set separately),
    "itunes_category" (optional, defaults to DEFAULT_ITUNES_CATEGORY),
    "itunes_explicit" (optional, defaults to DEFAULT_ITUNES_EXPLICIT —
    "true"/"false"), "itunes_type" (optional, defaults to
    DEFAULT_ITUNES_TYPE), "itunes_author" (optional), "itunes_subtitle"
    (optional), "itunes_image_url" (optional — see this module's
    docstring for why this specifically is left for the caller to supply
    whenever real cover art exists, not defaulted), "itunes_owner_name"
    + "itunes_owner_email" (optional — <itunes:owner> is only emitted
    when BOTH are present; a half-filled owner block isn't meaningful)}.

    Returns a complete RSS 2.0 + itunes-namespace podcast feed as an XML
    string. Built via xml.etree.ElementTree, not hand-rolled string
    concatenation — ET escapes special characters (a story title
    containing "&", say) correctly on its own; string concatenation
    would need to reimplement that escaping and could get it wrong."""
    rss = ET.Element("rss", {"version": "2.0", "xmlns:itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = feed_config["title"]
    ET.SubElement(channel, "link").text = feed_config["link"]
    ET.SubElement(channel, "description").text = feed_config["description"]
    ET.SubElement(channel, "language").text = feed_config.get("language", DEFAULT_LANGUAGE)
    if feed_config.get("author"):
        ET.SubElement(channel, "author").text = feed_config["author"]

    ET.SubElement(channel, "itunes:category", {"text": feed_config.get("itunes_category", DEFAULT_ITUNES_CATEGORY)})
    ET.SubElement(channel, "itunes:explicit").text = feed_config.get("itunes_explicit", DEFAULT_ITUNES_EXPLICIT)
    ET.SubElement(channel, "itunes:type").text = feed_config.get("itunes_type", DEFAULT_ITUNES_TYPE)
    itunes_author = feed_config.get("itunes_author") or feed_config.get("author")
    if itunes_author:
        ET.SubElement(channel, "itunes:author").text = itunes_author
    if feed_config.get("itunes_subtitle"):
        ET.SubElement(channel, "itunes:subtitle").text = feed_config["itunes_subtitle"]
    if feed_config.get("itunes_image_url"):
        ET.SubElement(channel, "itunes:image", {"href": feed_config["itunes_image_url"]})
    if feed_config.get("itunes_owner_name") and feed_config.get("itunes_owner_email"):
        owner = ET.SubElement(channel, "itunes:owner")
        ET.SubElement(owner, "itunes:name").text = feed_config["itunes_owner_name"]
        ET.SubElement(owner, "itunes:email").text = feed_config["itunes_owner_email"]

    for ep in sorted(episodes, key=lambda e: e["run_date"], reverse=True):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "description").text = ep["description"]
        ET.SubElement(item, "pubDate").text = ep["pub_date_rfc822"]
        ET.SubElement(item, "enclosure", {"url": ep["audio_url"], "length": str(ep["audio_byte_length"]), "type": ep["mime_type"]})
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = ep["guid"]
        if ep.get("itunes_duration"):
            ET.SubElement(item, "itunes:duration").text = ep["itunes_duration"]

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
    episode_metadata = build_episode_metadata(
        script, os.path.getsize(dest_wav_path), audio_url, duration_seconds=_wav_duration_seconds(dest_wav_path),
    )

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
    parser.add_argument("--feed-author", help="Plain <author> and the itunes:author fallback, if --itunes-author isn't set separately.")
    parser.add_argument("--itunes-category", default=DEFAULT_ITUNES_CATEGORY, help="Apple Podcasts category (default: %(default)s).")
    parser.add_argument("--itunes-explicit", default=DEFAULT_ITUNES_EXPLICIT, choices=["true", "false"], help="Default: %(default)s.")
    parser.add_argument("--itunes-type", default=DEFAULT_ITUNES_TYPE, choices=["episodic", "serial"], help="Default: %(default)s.")
    parser.add_argument("--itunes-author", help="Overrides --feed-author for itunes:author specifically, if they should differ.")
    parser.add_argument("--itunes-subtitle", help="Short itunes:subtitle tagline. Optional.")
    parser.add_argument(
        "--itunes-image-url", help="Cover art URL (itunes:image) — square, 1400-3000px, JPEG/PNG, no transparency. "
        "Apple Podcasts won't list a show without one; every other itunes: tag works fine if this is left unset.",
    )
    parser.add_argument("--itunes-owner-name", help="<itunes:owner> is only emitted when both --itunes-owner-name and --itunes-owner-email are given.")
    parser.add_argument("--itunes-owner-email", help="See --itunes-owner-name.")
    args = parser.parse_args()

    feed_config = {
        "title": args.feed_title, "link": args.feed_link, "description": args.feed_description,
        "author": args.feed_author, "itunes_category": args.itunes_category, "itunes_explicit": args.itunes_explicit,
        "itunes_type": args.itunes_type, "itunes_author": args.itunes_author, "itunes_subtitle": args.itunes_subtitle,
        "itunes_image_url": args.itunes_image_url, "itunes_owner_name": args.itunes_owner_name, "itunes_owner_email": args.itunes_owner_email,
    }
    result = publish_episode(args.data_dir, args.date, args.publish_dir, args.base_url, feed_config)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
