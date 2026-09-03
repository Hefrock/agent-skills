#!/usr/bin/env python3
"""Tests for distribute.py.

Two suites, matching the module's own split:
  - pure-logic tests (_rfc822_date, build_episode_metadata, build_feed_xml,
    build_vault_note) — no disk I/O, no network.
  - PublishEpisode — drives the real publish_episode() I/O function
    against a real temp directory (genuine file reads/writes, no
    network) — the one place in this module that isn't pure.

Run: python test_distribute.py"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
import json

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    # See test_evidence.py's load() docstring for why sys.modules
    # registration matters here: distribute.py does its own plain
    # `import qa_gate` internally.
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


qa_gate = load("qa_gate")
distribute = load("distribute")


def make_segment(segment_type, text="Some text.", canonical_id=None, claim_id=None, source_id=None):
    return {"segment_type": segment_type, "text": text, "canonical_id": canonical_id, "claim_id": claim_id, "source_id": source_id}


def make_story_segment(segment_type, canonical_id, text):
    return make_segment(segment_type, text=text, canonical_id=canonical_id, claim_id=f"claim-{canonical_id}", source_id=f"src-{canonical_id}")


def make_script(segments, run_date="2026-09-02", show_name="Healthcare AI Briefing", excluded_no_evidence=None):
    return {"run_date": run_date, "show_name": show_name, "segments": segments, "excluded_no_evidence": excluded_no_evidence or []}


def write_fake_wav(path, num_frames=24000, sample_rate=24000, channels=1, sample_width=2):
    """A real, minimal, parseable WAV file — not just placeholder bytes.
    PublishEpisode now actually opens episode.wav with the `wave` module
    (to measure duration for itunes:duration), so the fixture has to be
    real audio, even if silent. Default num_frames=24000 at 24000Hz is
    exactly 1.0 second, a round number for assertions."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * (num_frames * channels * sample_width))


class Rfc822Date(unittest.TestCase):
    def test_formats_as_rfc822_gmt(self):
        result = distribute._rfc822_date("2026-09-02")
        self.assertEqual(result, "Wed, 02 Sep 2026 00:00:00 GMT")

    def test_different_date_formats_correctly(self):
        result = distribute._rfc822_date("2026-01-15")
        self.assertEqual(result, "Thu, 15 Jan 2026 00:00:00 GMT")


class FormatItunesDuration(unittest.TestCase):
    def test_zero_seconds(self):
        self.assertEqual(distribute._format_itunes_duration(0), "00:00:00")

    def test_seconds_only(self):
        self.assertEqual(distribute._format_itunes_duration(59), "00:00:59")

    def test_rolls_over_into_minutes(self):
        self.assertEqual(distribute._format_itunes_duration(60), "00:01:00")

    def test_hours_minutes_seconds(self):
        self.assertEqual(distribute._format_itunes_duration(3725), "01:02:05")  # 1h 2m 5s

    def test_fractional_seconds_are_rounded(self):
        self.assertEqual(distribute._format_itunes_duration(59.6), "00:01:00")


class WavDurationSeconds(unittest.TestCase):
    def test_matches_frame_count_over_frame_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.wav")
            write_fake_wav(path, num_frames=24000, sample_rate=24000)
            self.assertAlmostEqual(distribute._wav_duration_seconds(path), 1.0)

    def test_a_shorter_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.wav")
            write_fake_wav(path, num_frames=12000, sample_rate=24000)
            self.assertAlmostEqual(distribute._wav_duration_seconds(path), 0.5)


class BuildEpisodeMetadata(unittest.TestCase):
    def test_title_includes_show_name_and_run_date(self):
        script = make_script([make_segment("intro")], run_date="2026-09-02", show_name="My Show")
        meta = distribute.build_episode_metadata(script, 12345, "https://example.com/2026-09-02.wav")
        self.assertEqual(meta["title"], "My Show — 2026-09-02")

    def test_description_joins_only_story_segment_texts(self):
        segments = [
            make_segment("intro", text="Welcome."),
            make_story_segment("top_three_item", "c1", "Story one text."),
            make_segment("quick_hits_transition", text="Now quick hits."),
            make_story_segment("quick_hits_item", "c2", "Story two text."),
            make_segment("outro", text="Goodbye."),
        ]
        script = make_script(segments)
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["description"], "Story one text. Story two text.")

    def test_empty_story_segments_produces_empty_description(self):
        script = make_script([make_segment("intro"), make_segment("outro")])
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["description"], "")

    def test_guid_is_deterministic_from_show_name_and_run_date(self):
        script = make_script([], run_date="2026-09-02", show_name="My Show")
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["guid"], "My Show-2026-09-02")

    def test_carries_through_audio_url_length_and_mime_type(self):
        script = make_script([])
        meta = distribute.build_episode_metadata(script, 98765, "https://x/ep.wav", mime_type="audio/wav")
        self.assertEqual(meta["audio_url"], "https://x/ep.wav")
        self.assertEqual(meta["audio_byte_length"], 98765)
        self.assertEqual(meta["mime_type"], "audio/wav")

    def test_carries_through_run_date_for_sorting(self):
        script = make_script([], run_date="2026-09-02")
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["run_date"], "2026-09-02")

    def test_pub_date_matches_rfc822_date_helper(self):
        script = make_script([], run_date="2026-09-02")
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["pub_date_rfc822"], distribute._rfc822_date("2026-09-02"))

    def test_disclosure_field_reads_the_scripts_own_disclosure_segment(self):
        segments = [make_segment("intro"), make_segment("disclosure", text="This is AI-generated."), make_segment("outro")]
        script = make_script(segments)
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["disclosure"], "This is AI-generated.")

    def test_disclosure_defaults_to_empty_string_when_no_disclosure_segment(self):
        script = make_script([make_segment("intro"), make_segment("outro")])
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["disclosure"], "")

    def test_description_appends_disclosure_after_story_texts(self):
        segments = [
            make_segment("intro"),
            make_segment("disclosure", text="This is AI-generated."),
            make_story_segment("top_three_item", "c1", "Story one text."),
            make_segment("outro"),
        ]
        script = make_script(segments)
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["description"], "Story one text. This is AI-generated.")

    def test_description_is_just_disclosure_when_there_are_no_stories(self):
        segments = [make_segment("intro"), make_segment("disclosure", text="This is AI-generated."), make_segment("outro")]
        script = make_script(segments)
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertEqual(meta["description"], "This is AI-generated.")

    def test_duration_seconds_defaults_to_none_and_omits_itunes_duration(self):
        script = make_script([])
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav")
        self.assertIsNone(meta["itunes_duration"])

    def test_duration_seconds_given_produces_formatted_itunes_duration(self):
        script = make_script([])
        meta = distribute.build_episode_metadata(script, 100, "https://x/1.wav", duration_seconds=872)
        self.assertEqual(meta["itunes_duration"], distribute._format_itunes_duration(872))


FEED_CONFIG = {"title": "Test Feed", "link": "https://example.com", "description": "A test feed."}


def make_episode(run_date, title="Ep", guid="guid-1", audio_url="https://x/ep.wav"):
    return {
        "title": title,
        "description": "A description.",
        "pub_date_rfc822": distribute._rfc822_date(run_date),
        "guid": guid,
        "audio_url": audio_url,
        "audio_byte_length": 1000,
        "mime_type": "audio/wav",
        "run_date": run_date,
    }


class BuildFeedXml(unittest.TestCase):
    def test_produces_parseable_xml(self):
        xml_str = distribute.build_feed_xml([make_episode("2026-09-02")], FEED_CONFIG)
        root = ET.fromstring(xml_str)  # raises if malformed
        self.assertEqual(root.tag, "rss")

    def test_channel_metadata_present(self):
        xml_str = distribute.build_feed_xml([], FEED_CONFIG)
        root = ET.fromstring(xml_str)
        channel = root.find("channel")
        self.assertEqual(channel.find("title").text, "Test Feed")
        self.assertEqual(channel.find("link").text, "https://example.com")
        self.assertEqual(channel.find("description").text, "A test feed.")
        self.assertEqual(channel.find("language").text, distribute.DEFAULT_LANGUAGE)

    def test_zero_episodes_produces_a_valid_empty_channel(self):
        xml_str = distribute.build_feed_xml([], FEED_CONFIG)
        root = ET.fromstring(xml_str)
        items = root.find("channel").findall("item")
        self.assertEqual(items, [])

    def test_item_count_matches_episode_count(self):
        episodes = [make_episode("2026-09-01"), make_episode("2026-09-02"), make_episode("2026-08-30")]
        xml_str = distribute.build_feed_xml(episodes, FEED_CONFIG)
        root = ET.fromstring(xml_str)
        self.assertEqual(len(root.find("channel").findall("item")), 3)

    def test_items_sorted_newest_first_regardless_of_input_order(self):
        episodes = [make_episode("2026-08-30", guid="oldest"), make_episode("2026-09-02", guid="newest"), make_episode("2026-09-01", guid="middle")]
        xml_str = distribute.build_feed_xml(episodes, FEED_CONFIG)
        root = ET.fromstring(xml_str)
        guids = [item.find("guid").text for item in root.find("channel").findall("item")]
        self.assertEqual(guids, ["newest", "middle", "oldest"])

    def test_enclosure_attributes_correct(self):
        episode = make_episode("2026-09-02", audio_url="https://x/ep.wav")
        episode["audio_byte_length"] = 55555
        xml_str = distribute.build_feed_xml([episode], FEED_CONFIG)
        root = ET.fromstring(xml_str)
        enclosure = root.find("channel").find("item").find("enclosure")
        self.assertEqual(enclosure.get("url"), "https://x/ep.wav")
        self.assertEqual(enclosure.get("length"), "55555")
        self.assertEqual(enclosure.get("type"), "audio/wav")

    def test_guid_is_not_a_permalink(self):
        xml_str = distribute.build_feed_xml([make_episode("2026-09-02")], FEED_CONFIG)
        root = ET.fromstring(xml_str)
        guid_el = root.find("channel").find("item").find("guid")
        self.assertEqual(guid_el.get("isPermaLink"), "false")

    def test_special_characters_in_title_are_escaped_correctly(self):
        episode = make_episode("2026-09-02", title="FDA & CMS: A \"Update\"")
        xml_str = distribute.build_feed_xml([episode], FEED_CONFIG)
        root = ET.fromstring(xml_str)  # would raise on bad escaping
        self.assertEqual(root.find("channel").find("item").find("title").text, "FDA & CMS: A \"Update\"")

    def test_author_included_when_present_in_feed_config(self):
        config = dict(FEED_CONFIG, author="Jane Doe")
        xml_str = distribute.build_feed_xml([], config)
        root = ET.fromstring(xml_str)
        self.assertEqual(root.find("channel").find("author").text, "Jane Doe")

    def test_author_omitted_when_absent_from_feed_config(self):
        xml_str = distribute.build_feed_xml([], FEED_CONFIG)
        root = ET.fromstring(xml_str)
        self.assertIsNone(root.find("channel").find("author"))

    def test_custom_language_overrides_default(self):
        config = dict(FEED_CONFIG, language="en-gb")
        xml_str = distribute.build_feed_xml([], config)
        root = ET.fromstring(xml_str)
        self.assertEqual(root.find("channel").find("language").text, "en-gb")

    # ── itunes: namespace tags. NOTE: ET.fromstring() genuinely resolves
    # the xmlns:itunes declaration as a real XML namespace when PARSING
    # (unlike the literal-string tags used when BUILDING the tree via
    # ET.SubElement, which is what makes the two-way round trip work at
    # all) — so every query here needs the namespaces= dict, not a plain
    # "itunes:foo" string, or .find() silently returns None. Confirmed
    # live before writing these — see distribute.py's own module
    # docstring for the same account. ─────────────────────────────────

    ITUNES_NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}

    def test_root_declares_the_itunes_namespace(self):
        # Checked on the RAW string, not the re-parsed tree: ET.fromstring()
        # consumes a genuine xmlns:* declaration during parsing (standard
        # XML namespace processing) rather than exposing it as an ordinary
        # queryable attribute afterward — the declaration is still really
        # there on the wire, which is what every other test in this class
        # implicitly relies on by successfully using the namespaces= dict
        # to find itunes: children at all.
        xml_str = distribute.build_feed_xml([], FEED_CONFIG)
        self.assertIn('xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"', xml_str)

    def test_channel_itunes_tags_use_defaults_when_not_configured(self):
        xml_str = distribute.build_feed_xml([], FEED_CONFIG)
        channel = ET.fromstring(xml_str).find("channel")
        self.assertEqual(channel.find("itunes:category", self.ITUNES_NS).get("text"), distribute.DEFAULT_ITUNES_CATEGORY)
        self.assertEqual(channel.find("itunes:explicit", self.ITUNES_NS).text, distribute.DEFAULT_ITUNES_EXPLICIT)
        self.assertEqual(channel.find("itunes:type", self.ITUNES_NS).text, distribute.DEFAULT_ITUNES_TYPE)

    def test_channel_itunes_tags_are_overridable(self):
        config = dict(FEED_CONFIG, itunes_category="Health & Fitness", itunes_explicit="true", itunes_type="serial")
        xml_str = distribute.build_feed_xml([], config)
        channel = ET.fromstring(xml_str).find("channel")
        self.assertEqual(channel.find("itunes:category", self.ITUNES_NS).get("text"), "Health & Fitness")
        self.assertEqual(channel.find("itunes:explicit", self.ITUNES_NS).text, "true")
        self.assertEqual(channel.find("itunes:type", self.ITUNES_NS).text, "serial")

    def test_itunes_author_falls_back_to_plain_author(self):
        config = dict(FEED_CONFIG, author="Jane Doe")
        channel = ET.fromstring(distribute.build_feed_xml([], config)).find("channel")
        self.assertEqual(channel.find("itunes:author", self.ITUNES_NS).text, "Jane Doe")

    def test_itunes_author_overrides_plain_author_when_both_given(self):
        config = dict(FEED_CONFIG, author="Jane Doe", itunes_author="J. Doe (Podcast)")
        channel = ET.fromstring(distribute.build_feed_xml([], config)).find("channel")
        self.assertEqual(channel.find("itunes:author", self.ITUNES_NS).text, "J. Doe (Podcast)")

    def test_itunes_author_omitted_when_neither_given(self):
        channel = ET.fromstring(distribute.build_feed_xml([], FEED_CONFIG)).find("channel")
        self.assertIsNone(channel.find("itunes:author", self.ITUNES_NS))

    def test_itunes_subtitle_omitted_by_default_present_when_given(self):
        channel = ET.fromstring(distribute.build_feed_xml([], FEED_CONFIG)).find("channel")
        self.assertIsNone(channel.find("itunes:subtitle", self.ITUNES_NS))
        config = dict(FEED_CONFIG, itunes_subtitle="Daily healthcare AI news.")
        channel2 = ET.fromstring(distribute.build_feed_xml([], config)).find("channel")
        self.assertEqual(channel2.find("itunes:subtitle", self.ITUNES_NS).text, "Daily healthcare AI news.")

    def test_itunes_image_omitted_without_cover_art_present_when_given(self):
        # The deliberate "no cover art yet" default — see this module's
        # docstring for why itunes:image is never defaulted to a
        # placeholder.
        channel = ET.fromstring(distribute.build_feed_xml([], FEED_CONFIG)).find("channel")
        self.assertIsNone(channel.find("itunes:image", self.ITUNES_NS))
        config = dict(FEED_CONFIG, itunes_image_url="https://example.com/art.jpg")
        channel2 = ET.fromstring(distribute.build_feed_xml([], config)).find("channel")
        self.assertEqual(channel2.find("itunes:image", self.ITUNES_NS).get("href"), "https://example.com/art.jpg")

    def test_itunes_owner_only_emitted_when_both_name_and_email_given(self):
        channel = ET.fromstring(distribute.build_feed_xml([], FEED_CONFIG)).find("channel")
        self.assertIsNone(channel.find("itunes:owner", self.ITUNES_NS))

        name_only = dict(FEED_CONFIG, itunes_owner_name="Jane Doe")
        channel2 = ET.fromstring(distribute.build_feed_xml([], name_only)).find("channel")
        self.assertIsNone(channel2.find("itunes:owner", self.ITUNES_NS))

        both = dict(FEED_CONFIG, itunes_owner_name="Jane Doe", itunes_owner_email="jane@example.com")
        channel3 = ET.fromstring(distribute.build_feed_xml([], both)).find("channel")
        owner = channel3.find("itunes:owner", self.ITUNES_NS)
        self.assertEqual(owner.find("itunes:name", self.ITUNES_NS).text, "Jane Doe")
        self.assertEqual(owner.find("itunes:email", self.ITUNES_NS).text, "jane@example.com")

    def test_item_itunes_duration_present_when_episode_has_one(self):
        episode = make_episode("2026-09-02")
        episode["itunes_duration"] = "00:14:32"
        xml_str = distribute.build_feed_xml([episode], FEED_CONFIG)
        item = ET.fromstring(xml_str).find("channel").find("item")
        self.assertEqual(item.find("itunes:duration", self.ITUNES_NS).text, "00:14:32")

    def test_item_itunes_duration_omitted_when_episode_has_none(self):
        episode = make_episode("2026-09-02")
        episode["itunes_duration"] = None
        xml_str = distribute.build_feed_xml([episode], FEED_CONFIG)
        item = ET.fromstring(xml_str).find("channel").find("item")
        self.assertIsNone(item.find("itunes:duration", self.ITUNES_NS))


class BuildVaultNote(unittest.TestCase):
    def setUp(self):
        segments = [
            make_segment("intro", text="Welcome."),
            make_story_segment("top_three_item", "c1", "Story one text."),
            make_segment("outro", text="Goodbye."),
        ]
        self.script = make_script(segments, run_date="2026-09-02", show_name="My Show")
        self.meta = distribute.build_episode_metadata(self.script, 100, "https://x/ep.wav")

    def test_frontmatter_fields_present(self):
        note = distribute.build_vault_note(self.script, self.meta)
        self.assertIn("type: source", note)
        self.assertIn("status: draft", note)
        self.assertIn("confidence: high", note)
        self.assertIn("updated: 2026-09-02", note)

    def test_title_heading_matches_episode_title(self):
        note = distribute.build_vault_note(self.script, self.meta)
        self.assertIn(f"# {self.meta['title']}", note)

    def test_author_and_link_fields_present(self):
        note = distribute.build_vault_note(self.script, self.meta)
        self.assertIn("**Author:** My Show", note)
        self.assertIn("**Link / DOI:** https://x/ep.wav", note)

    def test_story_text_appears_as_a_key_takeaway_bullet(self):
        note = distribute.build_vault_note(self.script, self.meta)
        self.assertIn("- Story one text.", note)

    def test_connective_segment_text_does_not_appear_as_a_takeaway(self):
        note = distribute.build_vault_note(self.script, self.meta)
        self.assertNotIn("- Welcome.", note)
        self.assertNotIn("- Goodbye.", note)

    def test_placeholder_sections_present_for_a_human_or_wiki_operator_to_fill(self):
        note = distribute.build_vault_note(self.script, self.meta)
        self.assertIn("## Concepts referenced", note)
        self.assertIn("## Quotes worth keeping", note)


class PublishEpisode(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp(prefix="distribute-data-")
        self.publish_dir = tempfile.mkdtemp(prefix="distribute-publish-")
        self.run_date = "2026-09-02"
        episode_dir = os.path.join(self.data_dir, "episodes", self.run_date)
        os.makedirs(episode_dir)
        self.script = make_script(
            [
                make_segment("intro"),
                make_segment("disclosure", text="This is AI-generated."),
                make_story_segment("top_three_item", "c1", "Story one."),
                make_segment("outro"),
            ],
            run_date=self.run_date,
        )
        with open(os.path.join(episode_dir, "script.json"), "w", encoding="utf-8") as f:
            json.dump(self.script, f)
        write_fake_wav(os.path.join(episode_dir, "episode.wav"))

    def tearDown(self):
        shutil.rmtree(self.data_dir, ignore_errors=True)
        shutil.rmtree(self.publish_dir, ignore_errors=True)

    def _publish(self):
        return distribute.publish_episode(self.data_dir, self.run_date, self.publish_dir, "https://example.com/", FEED_CONFIG)

    def test_copies_the_audio_file_into_publish_dir(self):
        source_path = os.path.join(self.data_dir, "episodes", self.run_date, "episode.wav")
        with open(source_path, "rb") as f:
            source_bytes = f.read()
        result = self._publish()
        self.assertTrue(os.path.exists(result["audio_path"]))
        with open(result["audio_path"], "rb") as f:
            self.assertEqual(f.read(), source_bytes)

    def test_published_feed_item_description_includes_the_disclosure_text(self):
        self._publish()
        with open(os.path.join(self.publish_dir, "feed.xml"), "r", encoding="utf-8") as f:
            root = ET.fromstring(f.read())
        item_description = root.find("channel").find("item").find("description").text
        self.assertIn("This is AI-generated.", item_description)

    def test_writes_a_parseable_feed_xml(self):
        result = self._publish()
        with open(result["feed_path"], "r", encoding="utf-8") as f:
            ET.fromstring(f.read())  # raises if malformed

    def test_published_feed_item_carries_the_real_measured_duration(self):
        # End-to-end: the fixture's episode.wav (write_fake_wav(), default
        # 24000 frames @ 24000Hz = exactly 1.0s) should reach the published
        # feed.xml as a real itunes:duration, not a placeholder.
        result = self._publish()
        with open(result["feed_path"], "r", encoding="utf-8") as f:
            root = ET.fromstring(f.read())
        ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
        item = root.find("channel").find("item")
        self.assertEqual(item.find("itunes:duration", ns).text, "00:00:01")
        self.assertEqual(result["episode_metadata"]["itunes_duration"], "00:00:01")

    def test_writes_a_vault_note_file(self):
        result = self._publish()
        self.assertTrue(os.path.exists(result["vault_note_path"]))
        with open(result["vault_note_path"], "r", encoding="utf-8") as f:
            self.assertIn("type: source", f.read())

    def test_audio_url_strips_trailing_slash_from_base_url(self):
        result = self._publish()
        self.assertEqual(result["episode_metadata"]["audio_url"], "https://example.com/episodes/2026-09-02.wav")

    def test_republishing_the_same_date_replaces_not_duplicates_the_feed_entry(self):
        self._publish()
        self._publish()
        index_path = os.path.join(self.publish_dir, "feed-episodes.json")
        with open(index_path, "r", encoding="utf-8") as f:
            episodes = json.load(f)
        self.assertEqual(len(episodes), 1)

    def test_publishing_a_second_date_keeps_both_episodes_in_the_index(self):
        self._publish()
        second_dir = os.path.join(self.data_dir, "episodes", "2026-09-03")
        os.makedirs(second_dir)
        script2 = make_script([make_segment("intro"), make_segment("outro")], run_date="2026-09-03")
        with open(os.path.join(second_dir, "script.json"), "w", encoding="utf-8") as f:
            json.dump(script2, f)
        write_fake_wav(os.path.join(second_dir, "episode.wav"))
        distribute.publish_episode(self.data_dir, "2026-09-03", self.publish_dir, "https://example.com/", FEED_CONFIG)
        index_path = os.path.join(self.publish_dir, "feed-episodes.json")
        with open(index_path, "r", encoding="utf-8") as f:
            episodes = json.load(f)
        self.assertEqual({e["run_date"] for e in episodes}, {"2026-09-02", "2026-09-03"})

    def test_audio_byte_length_matches_the_actual_written_file_size(self):
        result = self._publish()
        self.assertEqual(result["episode_metadata"]["audio_byte_length"], os.path.getsize(result["audio_path"]))


if __name__ == "__main__":
    unittest.main()
