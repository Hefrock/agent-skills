#!/usr/bin/env python3
"""
Unit tests for ingest.py's pure parsers (parse_pubmed_efetch_xml,
parse_arxiv_atom_xml, parse_rss_xml) and normalize_item(), against fixture
XML built from NCBI E-utilities', arXiv's, and RSS 2.0's documented
response shapes — not captured from a live API call, same caveat as
mcp/evidence-pinning's PubMed classifier tests. The network-calling
wrappers (fetch_pubmed, fetch_arxiv, fetch_rss) are deliberately not
covered here — see the module docstring for why.

Stdlib only (unittest). Run: python test_ingest.py
"""

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "ingest.py")

spec = importlib.util.spec_from_file_location("ingest", SCRIPT)
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


class NormalizeItem(unittest.TestCase):
    def test_produces_expected_shape(self):
        item = ingest.normalize_item("pubmed", "  A Title  ", "https://pubmed.ncbi.nlm.nih.gov/123", "2026-08-15", "  A summary.  ", id_hint="pmid:123")
        self.assertEqual(item, {
            "source_key": "pubmed",
            "title": "A Title",
            "url": "https://pubmed.ncbi.nlm.nih.gov/123",
            "id_hint": "pmid:123",
            "published_date": "2026-08-15",
            "summary": "A summary.",
        })

    def test_id_hint_defaults_to_none(self):
        item = ingest.normalize_item("arxiv", "T", "https://arxiv.org/abs/1", "2026-08-15", "S")
        self.assertIsNone(item["id_hint"])

    def test_malformed_date_raises(self):
        with self.assertRaises(ValueError):
            ingest.normalize_item("pubmed", "T", "https://x", "08/15/2026", "S")

    def test_non_iso_date_raises(self):
        with self.assertRaises(ValueError):
            ingest.normalize_item("pubmed", "T", "https://x", "2026-8-15", "S")  # not zero-padded


PUBMED_FIXTURE_NUMERIC_MONTH = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle>
<MedlineCitation>
<PMID Version="1">12345678</PMID>
<Article>
<ArticleTitle>FHIR-based interoperability framework for clinical decision support</ArticleTitle>
<Abstract>
<AbstractText>This study evaluates a FHIR-based framework for real-time CDS integration.</AbstractText>
</Abstract>
<ArticleDate DateType="Electronic">
<Year>2026</Year>
<Month>08</Month>
<Day>15</Day>
</ArticleDate>
</Article>
</MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""

PUBMED_FIXTURE_NAMED_MONTH_JOURNAL_DATE = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle>
<MedlineCitation>
<PMID Version="1">87654321</PMID>
<Article>
<Journal>
<JournalIssue>
<PubDate>
<Year>2026</Year>
<Month>Aug</Month>
<Day>3</Day>
</PubDate>
</JournalIssue>
</Journal>
<ArticleTitle>Agentic clinical workflow orchestration: a pilot study</ArticleTitle>
<Abstract>
<AbstractText Label="BACKGROUND">Agentic systems are increasingly deployed in clinical settings.</AbstractText>
<AbstractText Label="RESULTS">We found a 12% reduction in task completion time.</AbstractText>
</Abstract>
</Article>
</MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""

PUBMED_FIXTURE_YEAR_ONLY = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle>
<MedlineCitation>
<PMID Version="1">11111111</PMID>
<Article>
<ArticleTitle>A year-only-dated record</ArticleTitle>
<Abstract><AbstractText>No month or day given.</AbstractText></Abstract>
<ArticleDate><Year>2025</Year></ArticleDate>
</Article>
</MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""

PUBMED_FIXTURE_NO_DATE = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle>
<MedlineCitation>
<PMID Version="1">22222222</PMID>
<Article>
<ArticleTitle>A record with no date at all</ArticleTitle>
<Abstract><AbstractText>Should be skipped.</AbstractText></Abstract>
</Article>
</MedlineCitation>
</PubmedArticle>
</PubmedArticleSet>
"""


class ParsePubmedEfetchXml(unittest.TestCase):
    def test_extracts_pmid_title_url(self):
        items = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NUMERIC_MONTH)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id_hint"], "pmid:12345678")
        self.assertEqual(item["url"], "https://pubmed.ncbi.nlm.nih.gov/12345678")
        self.assertIn("FHIR-based interoperability", item["title"])

    def test_numeric_month_parses(self):
        items = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NUMERIC_MONTH)
        self.assertEqual(items[0]["published_date"], "2026-08-15")

    def test_three_letter_month_name_parses(self):
        items = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NAMED_MONTH_JOURNAL_DATE)
        self.assertEqual(items[0]["published_date"], "2026-08-03")

    def test_falls_back_to_journal_pubdate_when_no_article_date(self):
        items = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NAMED_MONTH_JOURNAL_DATE)
        self.assertEqual(len(items), 1)  # would be 0/skipped if the fallback didn't work

    def test_multiple_labeled_abstract_sections_are_joined(self):
        items = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NAMED_MONTH_JOURNAL_DATE)
        self.assertIn("Agentic systems are increasingly deployed", items[0]["summary"])
        self.assertIn("12% reduction", items[0]["summary"])

    def test_year_only_date_defaults_to_january_first(self):
        items = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_YEAR_ONLY)
        self.assertEqual(items[0]["published_date"], "2025-01-01")

    def test_record_with_no_date_at_all_is_skipped_not_crashed(self):
        items = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NO_DATE)
        self.assertEqual(items, [])

    def test_empty_response_returns_empty_list(self):
        items = ingest.parse_pubmed_efetch_xml("<PubmedArticleSet></PubmedArticleSet>")
        self.assertEqual(items, [])

    def test_multiple_articles_in_one_response(self):
        combined = PUBMED_FIXTURE_NUMERIC_MONTH.replace("</PubmedArticleSet>", "") + \
            PUBMED_FIXTURE_NAMED_MONTH_JOURNAL_DATE.replace('<?xml version="1.0"?>\n<PubmedArticleSet>', "")
        items = ingest.parse_pubmed_efetch_xml(combined)
        self.assertEqual(len(items), 2)
        self.assertEqual({i["id_hint"] for i in items}, {"pmid:12345678", "pmid:87654321"})

    def test_source_key_is_always_pubmed(self):
        items = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NUMERIC_MONTH)
        self.assertEqual(items[0]["source_key"], "pubmed")


ARXIV_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<id>http://arxiv.org/abs/2608.12345v1</id>
<title>
  Agentic AI Systems for Clinical Workflow Automation
</title>
<summary>
  We present a framework for agentic AI systems operating within
  clinical decision support pipelines, evaluated against a synthetic
  FHIR-backed benchmark.
</summary>
<published>2026-08-20T00:00:00Z</published>
</entry>
<entry>
<id>http://arxiv.org/abs/2608.54321v2</id>
<title>Unrelated Robotics Paper</title>
<summary>A study of robotic grasping.</summary>
<published>2026-08-18T12:30:00Z</published>
</entry>
</feed>
"""

ARXIV_FIXTURE_MALFORMED_ENTRY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<title>Missing id and published date</title>
<summary>Should be skipped.</summary>
</entry>
</feed>
"""


class ParseArxivAtomXml(unittest.TestCase):
    def test_extracts_two_entries(self):
        items = ingest.parse_arxiv_atom_xml(ARXIV_FIXTURE)
        self.assertEqual(len(items), 2)

    def test_title_and_summary_whitespace_normalized(self):
        items = ingest.parse_arxiv_atom_xml(ARXIV_FIXTURE)
        self.assertEqual(items[0]["title"], "Agentic AI Systems for Clinical Workflow Automation")
        self.assertIn("FHIR-backed benchmark", items[0]["summary"])
        self.assertNotIn("\n", items[0]["summary"])

    def test_url_is_the_entry_id(self):
        items = ingest.parse_arxiv_atom_xml(ARXIV_FIXTURE)
        self.assertEqual(items[0]["url"], "http://arxiv.org/abs/2608.12345v1")

    def test_published_date_truncated_to_date_only(self):
        items = ingest.parse_arxiv_atom_xml(ARXIV_FIXTURE)
        self.assertEqual(items[0]["published_date"], "2026-08-20")
        self.assertEqual(items[1]["published_date"], "2026-08-18")

    def test_id_hint_is_always_none_for_arxiv(self):
        items = ingest.parse_arxiv_atom_xml(ARXIV_FIXTURE)
        self.assertIsNone(items[0]["id_hint"])

    def test_source_key_is_always_arxiv(self):
        items = ingest.parse_arxiv_atom_xml(ARXIV_FIXTURE)
        self.assertTrue(all(i["source_key"] == "arxiv" for i in items))

    def test_malformed_entry_missing_required_fields_is_skipped(self):
        items = ingest.parse_arxiv_atom_xml(ARXIV_FIXTURE_MALFORMED_ENTRY)
        self.assertEqual(items, [])

    def test_empty_feed_returns_empty_list(self):
        items = ingest.parse_arxiv_atom_xml('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        self.assertEqual(items, [])


RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Example Health News</title>
<item>
<title>FDA Clears New Clinical Decision Support Tool</title>
<link>https://example.com/articles/fda-clears-cds-tool</link>
<pubDate>Mon, 15 Aug 2026 12:30:00 GMT</pubDate>
<description><![CDATA[The FDA has <b>cleared</b> a new AI-based clinical decision support tool for use in emergency departments &amp; urgent care.]]></description>
<guid isPermaLink="false">urn:example:12345</guid>
</item>
<item>
<title>Hospital System Reports Q2 Earnings</title>
<link>https://example.com/articles/q2-earnings</link>
<pubDate>Sat, 13 Aug 2026 09:00:00 -0400</pubDate>
<description>Plain-text description, no CDATA or HTML.</description>
</item>
</channel>
</rss>
"""

RSS_FIXTURE_MISSING_FIELDS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
<title>No link or pubDate</title>
<description>Should be skipped — missing required fields.</description>
</item>
<item>
<title>Has link, no pubDate</title>
<link>https://example.com/no-date</link>
<description>Should also be skipped.</description>
</item>
</channel>
</rss>
"""

# The actual shape of fiercehealthcare.com's real feed (2026-09-01): title
# wrapped in an <a> tag, a real <link>, and a non-RFC-822 <pubDate>. This
# fixture is what would have caught the "zero items" regression before it
# ever reached the live smoke test — reduced from the real captured item.
RSS_FIXTURE_FIERCEHEALTHCARE_SHAPE = """<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0" xml:base="https://www.fiercehealthcare.com/">
  <channel>
    <title>Fierce Healthcare</title>
    <item>
      <title><a href="/providers/example-story" hreflang="en">Hospital groups rail against CMS' proposed changes</a></title>
      <link>https://www.fiercehealthcare.com/providers/example-story</link>
      <description>Public comments saw several major hospital groups pushing back.</description>
      <pubDate>Sep 1, 2026 2:08pm</pubDate>
      <dc:creator><a href="/person/example" hreflang="en">A Reporter</a></dc:creator>
      <guid isPermaLink="true">https://www.fiercehealthcare.com/d48b46a9</guid>
    </item>
  </channel>
</rss>
"""


class ParseRssXml(unittest.TestCase):
    def test_extracts_two_items(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")
        self.assertEqual(len(items), 2)

    def test_source_key_is_passed_through(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")
        self.assertTrue(all(i["source_key"] == "stat_news" for i in items))

    def test_fiercehealthcare_shaped_feed_produces_an_item(self):
        # Regression test: this exact shape (title wrapped in <a>, non-RFC-822
        # pubDate) produced ZERO items before the pubDate fallback was added —
        # confirmed live on 2026-09-01, see config/sources.json's
        # feed_url_verified_note for fierce_healthcare.
        items = ingest.parse_rss_xml(RSS_FIXTURE_FIERCEHEALTHCARE_SHAPE, source_key="fierce_healthcare")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_date"], "2026-09-01")
        self.assertIn("Hospital groups", items[0]["title"])
        self.assertEqual(items[0]["url"], "https://www.fiercehealthcare.com/providers/example-story")

    def test_cdata_wrapped_html_description_is_cleaned(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")
        summary = items[0]["summary"]
        self.assertNotIn("<b>", summary)
        self.assertNotIn("CDATA", summary)
        self.assertIn("cleared", summary)

    def test_html_entities_are_unescaped(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")
        self.assertIn("&", items[0]["summary"])  # "&amp;" -> "&"
        self.assertNotIn("&amp;", items[0]["summary"])

    def test_plain_text_description_without_cdata_still_works(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")
        self.assertEqual(items[1]["summary"], "Plain-text description, no CDATA or HTML.")

    def test_pubdate_with_gmt_name_parses(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")
        self.assertEqual(items[0]["published_date"], "2026-08-15")

    def test_pubdate_with_numeric_offset_parses(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")
        self.assertEqual(items[1]["published_date"], "2026-08-13")

    def test_id_hint_is_always_none(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")
        self.assertTrue(all(i["id_hint"] is None for i in items))

    def test_items_missing_link_or_pubdate_are_skipped(self):
        items = ingest.parse_rss_xml(RSS_FIXTURE_MISSING_FIELDS, source_key="stat_news")
        self.assertEqual(items, [])

    def test_empty_feed_returns_empty_list(self):
        items = ingest.parse_rss_xml('<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>', source_key="stat_news")
        self.assertEqual(items, [])

    def test_generic_parser_works_for_any_source_key(self):
        # Same parser, three different outlets — only source_key changes.
        for key in ("stat_news", "fierce_healthcare", "healthcare_it_news"):
            items = ingest.parse_rss_xml(RSS_FIXTURE, source_key=key)
            self.assertTrue(all(i["source_key"] == key for i in items))


class CleanHtmlText(unittest.TestCase):
    def test_strips_cdata_wrapper(self):
        self.assertEqual(ingest._clean_html_text("<![CDATA[Hello]]>"), "Hello")

    def test_strips_html_tags(self):
        self.assertEqual(ingest._clean_html_text("<p>Hello <b>world</b></p>"), "Hello world")

    def test_unescapes_entities(self):
        self.assertEqual(ingest._clean_html_text("Tom &amp; Jerry"), "Tom & Jerry")

    def test_collapses_whitespace(self):
        self.assertEqual(ingest._clean_html_text("  Hello   \n\n  world  "), "Hello world")

    def test_combined_cdata_html_and_entities(self):
        raw = "<![CDATA[<p>Cancer &amp; <b>AI</b> research</p>]]>"
        self.assertEqual(ingest._clean_html_text(raw), "Cancer & AI research")


class ParseRssPubdate(unittest.TestCase):
    def test_rfc822_with_day_name_and_gmt(self):
        self.assertEqual(ingest._parse_rss_pubdate("Mon, 15 Aug 2026 12:30:00 GMT"), "2026-08-15")

    def test_rfc822_with_numeric_offset(self):
        self.assertEqual(ingest._parse_rss_pubdate("Sat, 13 Aug 2026 09:00:00 -0400"), "2026-08-13")

    def test_unparseable_string_returns_none(self):
        self.assertIsNone(ingest._parse_rss_pubdate("not a date"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(ingest._parse_rss_pubdate(""))

    def test_fiercehealthcare_non_rfc822_format(self):
        # Confirmed live against the real feed (2026-09-01): "Sep 1, 2026
        # 2:08pm" — no day-of-week, 12hr clock with am/pm, no timezone.
        # Not RFC 822 despite being an otherwise-valid RSS 2.0 feed.
        self.assertEqual(ingest._parse_rss_pubdate("Sep 1, 2026 2:08pm"), "2026-09-01")

    def test_fiercehealthcare_format_single_digit_day(self):
        self.assertEqual(ingest._parse_rss_pubdate("Jan 5, 2026 11:59am"), "2026-01-05")

    def test_fiercehealthcare_format_double_digit_day(self):
        self.assertEqual(ingest._parse_rss_pubdate("Dec 25, 2026 12:00pm"), "2026-12-25")

    def test_rfc822_is_tried_before_the_fallback_format(self):
        # A string that happens to be valid RFC 822 should never fall
        # through to the fiercehealthcare-specific parser.
        self.assertEqual(ingest._parse_rss_pubdate("Wed, 01 Jul 2026 00:00:00 GMT"), "2026-07-01")


class IngestFeedsIntoDownstreamModules(unittest.TestCase):
    """Integration-shaped tests, still no network: confirms parsed items are
    actually consumable by source_registry.py and dedup_store.py without
    reshaping, since those are the two modules ingest.py exists to feed."""

    def setUp(self):
        source_registry_spec = importlib.util.spec_from_file_location("source_registry", os.path.join(HERE, "source_registry.py"))
        self.source_registry = importlib.util.module_from_spec(source_registry_spec)
        source_registry_spec.loader.exec_module(self.source_registry)

        dedup_store_spec = importlib.util.spec_from_file_location("dedup_store", os.path.join(HERE, "dedup_store.py"))
        self.dedup_store = importlib.util.module_from_spec(dedup_store_spec)
        dedup_store_spec.loader.exec_module(self.dedup_store)

    def test_pubmed_item_scores_against_the_real_registry(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NUMERIC_MONTH)[0]
        score = self.source_registry.score_source_item(registry, item["source_key"], age_days=0)
        self.assertAlmostEqual(score, 1.0)

    def test_pubmed_item_canonicalizes_via_its_id_hint(self):
        item = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NUMERIC_MONTH)[0]
        canonical = self.dedup_store.canonicalize_id(item["url"], item["id_hint"])
        self.assertEqual(canonical, "pmid:12345678")

    def test_arxiv_item_canonicalizes_via_url_fallback(self):
        item = ingest.parse_arxiv_atom_xml(ARXIV_FIXTURE)[0]
        canonical = self.dedup_store.canonicalize_id(item["url"], item["id_hint"])
        self.assertTrue(canonical.startswith("url:"))

    def test_throughline_classification_on_a_real_parsed_title(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_pubmed_efetch_xml(PUBMED_FIXTURE_NUMERIC_MONTH)[0]
        scope = self.source_registry.classify_topic_scope(item["title"], registry["throughline_keywords"])
        self.assertEqual(scope, "throughline")  # title contains "FHIR"

    def test_rss_item_scores_against_the_real_registry_industry_press_category(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_rss_xml(RSS_FIXTURE, source_key="stat_news")[0]
        score = self.source_registry.score_source_item(registry, item["source_key"], age_days=3)
        # industry_press: floor 0.4, half_life 3 days -> exactly the floor + half the remaining range at age=3.
        self.assertAlmostEqual(score, 0.4 + 0.6 * 0.5)

    def test_all_three_rss_source_keys_are_registered_in_the_real_config(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        for key in ("stat_news", "fierce_healthcare", "healthcare_it_news"):
            source = self.source_registry.get_source(registry, key)
            self.assertEqual(source["category"], "industry_press")
            self.assertIn("feed_url", source)


if __name__ == "__main__":
    unittest.main()
