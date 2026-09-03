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


RSS_FIXTURE_ONC_ASTP_SHAPE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>ONC Blog</title>
    <item>
      <title>Nine Teams, One Mission: Meet the EHIgnite Phase 1 Winners</title>
      <link>https://healthit.gov/blog/interoperability/nine-teams-one-mission-meet-the-ehignite-phase-1-winners/</link>
      <pubDate>Tue, 21 Jul 2026 13:00:00 +0000</pubDate>
      <dc:creator><![CDATA[ASTP Staff]]></dc:creator>
      <description><![CDATA[What started as a challenge to tame unwieldy single patient EHI exports&#8230; The post Nine Teams, One Mission appeared first on ONC Blog.]]></description>
      <guid isPermaLink="false">https://healthit.gov/?p=12345</guid>
    </item>
  </channel>
</rss>
"""


RSS_FIXTURE_HIT_CONSULTANT_SHAPE = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
	xmlns:content="http://purl.org/rss/1.0/modules/content/"
	xmlns:dc="http://purl.org/dc/elements/1.1/"
	xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
	<title></title>
	<link>https://hitconsultant.net/</link>
	<item>
		<title>M&#038;A: Cureety Acquires Reimagine Care to Expand Precision Oncology Platform</title>
		<link>https://hitconsultant.net/2026/09/02/cureety-acquires-reimagine-care/</link>
		<dc:creator><![CDATA[Fred Pennic]]></dc:creator>
		<pubDate>Wed, 02 Sep 2026 15:11:00 +0000</pubDate>
		<category><![CDATA[Digital Health]]></category>
		<guid isPermaLink="false">https://hitconsultant.net/?p=97760</guid>
		<description><![CDATA[What You Should Know Paris-based precision oncology company Cureety has acquired Nashville-based Reimagine Care. <a class="more-posts-link" href="https://hitconsultant.net/2026/09/02/cureety-acquires-reimagine-care/">... Read More</a>]]></description>
	</item>
</channel>
</rss>
"""


RSS_FIXTURE_CMS_SHAPE = """<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0" xml:base="https://www.cms.gov/">
  <channel>
    <title>Newsroom Feeds</title>
    <link>https://www.cms.gov/</link>
    <description/>
    <language>en</language>
    <item>
  <title><a href="/newsroom/press-releases/example-cms-release" hreflang="en">CMS Prevents $1.6 Billion in Fraudulent Medicare Laboratory Payments</a></title>
  <link>https://www.cms.gov/%3Ca%20href%3D%22/newsroom/press-releases/example-cms-release%22%20hreflang%3D%22en%22%3ECMS%20Prevents%20%241.6%20Billion%20in%20Fraudulent%20Medicare%20Laboratory%20Payments%3C/a%3E</link>
  <description>&lt;p class="text-align-center"&gt;&lt;strong&gt;CMS Prevents $1.6 Billion&lt;/strong&gt;&lt;/p&gt;&lt;p&gt;157 fraudulent lab providers revoked from Medicare program&lt;/p&gt;</description>
  <pubDate>Fri, 08/28/2026 - 10:15</pubDate>
    <dc:creator><time datetime="2026-08-28T10:15:00-04:00">Fri, 08/28/2026 - 10:15</time>
</dc:creator>
    <guid isPermaLink="true">https://www.cms.gov/2120872</guid>
    </item>
  </channel>
</rss>
"""

RSS_FIXTURE_CMS_UNRECOVERABLE_LINK = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><item>
  <title>A title with no anchor tag at all</title>
  <link>https://www.cms.gov/%3Cbroken%3E</link>
  <description>x</description>
  <pubDate>Fri, 08/28/2026 - 10:15</pubDate>
</item></channel></rss>
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

    def test_onc_astp_shaped_feed_produces_an_item(self):
        # Regression coverage for the real WordPress shape confirmed live
        # on 2026-09-02 (see config/sources.json's feed_url_verified_note
        # for onc_astp) — unlike fierce_healthcare, this needed zero
        # parser changes: plain (non-<a>-wrapped) title, standard RFC 822
        # pubDate, CDATA description with a WordPress "appeared first on"
        # footer that's left as-is (this parser cleans HTML/CDATA/entities,
        # not source-specific boilerplate).
        items = ingest.parse_rss_xml(RSS_FIXTURE_ONC_ASTP_SHAPE, source_key="onc_astp")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_date"], "2026-07-21")
        self.assertIn("EHIgnite", items[0]["title"])
        self.assertEqual(
            items[0]["url"],
            "https://healthit.gov/blog/interoperability/nine-teams-one-mission-meet-the-ehignite-phase-1-winners/",
        )
        self.assertIn("appeared first on ONC Blog", items[0]["summary"])

    def test_hit_consultant_shaped_feed_produces_an_item(self):
        # Regression coverage for the real WordPress shape confirmed on
        # 2026-09-03 (see config/sources.json's feed_url_verified_note for
        # hit_consultant) — verified against the actual live feed response
        # (supplied by the user, this pipeline's own network egress being
        # blocked), not a guess: standard RFC 822 pubDate, HTML-entity-
        # encoded title ("M&#038;A" -> "M&A"), CDATA description with a
        # trailing "... Read More" anchor that this parser correctly
        # reduces to plain text rather than leaking markup. No parser
        # changes needed — as clean as onc_astp, unlike fierce_healthcare
        # and cms.
        items = ingest.parse_rss_xml(RSS_FIXTURE_HIT_CONSULTANT_SHAPE, source_key="hit_consultant")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_date"], "2026-09-02")
        self.assertEqual(items[0]["title"], "M&A: Cureety Acquires Reimagine Care to Expand Precision Oncology Platform")
        self.assertEqual(items[0]["url"], "https://hitconsultant.net/2026/09/02/cureety-acquires-reimagine-care/")
        self.assertIn("Cureety has acquired Nashville-based Reimagine Care", items[0]["summary"])
        self.assertIn("Read More", items[0]["summary"])
        self.assertNotIn("<a ", items[0]["summary"])

    def test_cms_shaped_feed_produces_an_item(self):
        # Regression coverage for the real, messy CMS shape confirmed live
        # on 2026-09-02 (see config/sources.json's feed_url_verified_note
        # for cms): a corrupted <link> (URL-encoded copy of the title's own
        # markup, recovered from the title's <a href> instead) and a third
        # pubDate format ("Fri, 08/28/2026 - 10:15") beyond RFC 822 and
        # fierce_healthcare's.
        items = ingest.parse_rss_xml(RSS_FIXTURE_CMS_SHAPE, source_key="cms")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published_date"], "2026-08-28")
        self.assertIn("Fraudulent Medicare", items[0]["title"])
        self.assertEqual(
            items[0]["url"],
            "https://www.cms.gov/newsroom/press-releases/example-cms-release",
        )

    def test_cms_shaped_description_paragraphs_are_space_separated(self):
        # Regression test: cms.gov's <description> HTML-entity-encodes its
        # markup ("&lt;p&gt;...&lt;/p&gt;", not literal tags), and stripping
        # those tags outright used to glue adjacent paragraphs together
        # with no space in between.
        items = ingest.parse_rss_xml(RSS_FIXTURE_CMS_SHAPE, source_key="cms")
        summary = items[0]["summary"]
        self.assertIn("Billion 157 fraudulent", summary)  # space between paragraphs
        self.assertNotIn("Billion157", summary)
        self.assertNotIn("<p", summary)
        self.assertNotIn("&lt;", summary)

    def test_cms_item_with_no_recoverable_title_href_is_skipped(self):
        # If the <link> is corrupted AND the title has no <a href> to
        # recover from, the item is unusable — skip rather than crash or
        # emit a garbage URL.
        items = ingest.parse_rss_xml(RSS_FIXTURE_CMS_UNRECOVERABLE_LINK, source_key="cms")
        self.assertEqual(items, [])

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
        for key in ("stat_news", "fierce_healthcare", "onc_astp"):
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

    def test_adjacent_block_tags_are_space_separated_not_glued(self):
        # Regression test: confirmed live against cms.gov's real feed
        # (2026-09-02) — deleting tags outright (rather than replacing with
        # a space) glued adjacent paragraphs together with no space.
        self.assertEqual(ingest._clean_html_text("<p>Hello</p><p>world</p>"), "Hello world")

    def test_entity_encoded_tags_are_stripped_after_unescaping(self):
        # Regression test: cms.gov's real <description> double-encodes its
        # markup ("&lt;p&gt;...&lt;/p&gt;", not literal "<p>...</p>") — the
        # tag-strip pass has to run again after unescaping reveals them.
        raw = "&lt;p&gt;Cancer &amp;amp; AI&lt;/p&gt;&lt;p&gt;research&lt;/p&gt;"
        self.assertEqual(ingest._clean_html_text(raw), "Cancer &amp; AI research")


class RecoverLinkFromCorruptedField(unittest.TestCase):
    def test_recovers_relative_href_with_origin_from_corrupted_link(self):
        link_raw = 'https://www.cms.gov/%3Ca%20href%3D%22/newsroom/press-releases/x%22%3ETitle%3C/a%3E'
        title_raw = '<a href="/newsroom/press-releases/x" hreflang="en">Title</a>'
        result = ingest._recover_link_from_corrupted_field(link_raw, title_raw)
        self.assertEqual(result, "https://www.cms.gov/newsroom/press-releases/x")

    def test_absolute_href_in_title_is_used_as_is(self):
        link_raw = "https://www.cms.gov/%3Cbroken%3E"
        title_raw = '<a href="https://example.com/elsewhere">Title</a>'
        result = ingest._recover_link_from_corrupted_field(link_raw, title_raw)
        self.assertEqual(result, "https://example.com/elsewhere")

    def test_returns_none_when_title_has_no_href(self):
        result = ingest._recover_link_from_corrupted_field("https://www.cms.gov/%3Cx%3E", "Plain title, no anchor")
        self.assertIsNone(result)

    def test_returns_none_when_link_has_no_recognizable_origin(self):
        result = ingest._recover_link_from_corrupted_field("not a url at all", '<a href="/x">Title</a>')
        self.assertIsNone(result)


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


MEDRXIV_FIXTURE = {
    "collection": [
        {
            "doi": "10.1101/2026.08.15.26000001",
            "title": "Agentic clinical decision support for FHIR-based EHR workflows: a prospective cohort study",
            "authors": "Doe, J.; Smith, A.",
            "author_corresponding": "Doe, J.",
            "date": "2026-08-15",
            "category": "health informatics",
            "abstract": "We evaluate an agentic CDS system integrated via FHIR against a matched cohort.",
            "published": "NA",
            "jatsxml": "https://api.medrxiv.org/content/10.1101/2026.08.15.26000001.full.xml",
        },
        {
            "doi": "10.1101/2026.08.14.26000002",
            "title": "A second unrelated preprint",
            "authors": "Lee, K.",
            "author_corresponding": "Lee, K.",
            "date": "2026-08-14",
            "category": "epidemiology",
            "abstract": "An abstract about something else entirely.",
            "published": "10.1056/example.2026",
            "jatsxml": "https://api.medrxiv.org/content/10.1101/2026.08.14.26000002.full.xml",
        },
    ],
    "messages": [{"status": "ok", "count": "2"}],
}

MEDRXIV_FIXTURE_MISSING_FIELDS = {
    "collection": [
        {"doi": "10.1101/2026.08.01.26000003", "title": "", "date": "2026-08-01", "abstract": "x"},  # empty title
        {"doi": "", "title": "No DOI here", "date": "2026-08-01", "abstract": "x"},  # empty doi
        {"doi": "10.1101/2026.08.01.26000004", "title": "No date here", "date": "", "abstract": "x"},  # empty date
        {"doi": "10.1101/2026.08.01.26000005", "title": "Malformed date", "date": "08/01/2026", "abstract": "x"},  # not ISO
    ],
}

MEDRXIV_FIXTURE_NO_ABSTRACT = {
    "collection": [
        {"doi": "10.1101/2026.07.01.26000006", "title": "No abstract field at all", "date": "2026-07-01"},
    ],
}


class ParseMedrxivJson(unittest.TestCase):
    def test_extracts_two_items(self):
        items = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)
        self.assertEqual(len(items), 2)

    def test_source_key_is_medrxiv(self):
        items = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)
        self.assertTrue(all(i["source_key"] == "medrxiv" for i in items))

    def test_url_is_built_from_doi(self):
        items = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)
        self.assertEqual(items[0]["url"], "https://doi.org/10.1101/2026.08.15.26000001")

    def test_id_hint_is_doi_prefixed(self):
        items = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)
        self.assertEqual(items[0]["id_hint"], "doi:10.1101/2026.08.15.26000001")

    def test_published_date_passes_through(self):
        items = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)
        self.assertEqual(items[0]["published_date"], "2026-08-15")

    def test_abstract_becomes_summary(self):
        items = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)
        self.assertIn("agentic CDS system", items[0]["summary"])

    def test_missing_abstract_field_becomes_empty_summary(self):
        items = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE_NO_ABSTRACT)
        self.assertEqual(items[0]["summary"], "")

    def test_records_missing_required_fields_are_skipped(self):
        items = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE_MISSING_FIELDS)
        self.assertEqual(items, [])

    def test_empty_collection_returns_empty_list(self):
        self.assertEqual(ingest.parse_medrxiv_json({"collection": []}), [])

    def test_missing_collection_key_returns_empty_list(self):
        self.assertEqual(ingest.parse_medrxiv_json({}), [])


FDA_GUIDANCE_FIXTURE = [
    {
        # Real record shape, captured live from
        # fda.gov/files/api/datatables/static/search-for-guidance.json
        # (2026-09-02) — see the module docstring for how this data source
        # was traced.
        "title": '<a href="/regulatory-information/search-fda-guidance-documents/small-entity-compliance-guide-safe-handling-statements-labeling-shell-eggs-and-refrigeration-shell">Small Entity Compliance Guide: Safe Handling Statements on Labeling of Shell Eggs and the Refrigeration of Shell Eggs Held for Retail Distribution</a>',
        "field_associated_media_2": "",
        "field_issue_datetime": "07/01/2001",
        "field_issuing_office_taxonomy": "Human Foods Program",
        "field_health_topics": "",
        "term_node_tid": "Egg/Egg Product, Retail Food Protection",
        "field_topics": "",
        "topics-product": "Egg/Egg Product, Retail Food Protection",
        "field_final_guidance_1": "Final",
        "open-comment": "  No ",
        "field_comment_close_date": "",
        "field_docket_number": '<a href="https://www.regulations.gov/docket/FDA-2020-D-1954">FDA-2020-D-1954</a>',
        "field_communication_type": "Small Entity Compliance Guide",
        "field_center": "Human Foods Program",
        "field_regulated_product_field": "Food &amp; Beverages",
        "changed": '<time datetime="2024-10-01T07:00:51-04:00">2024-10-01 07:00</time>\n',
    },
    {
        "title": '<a href="/regulatory-information/search-fda-guidance-documents/draft-guidance-industry-and-fda-staff-clinical-decision-support-software">Draft Guidance for Industry: Clinical Decision Support Software Using Artificial Intelligence</a>',
        "field_associated_media_2": "",
        "field_issue_datetime": "08/20/2026",
        "field_issuing_office_taxonomy": "Center for Devices and Radiological Health",
        "field_health_topics": "",
        "term_node_tid": "Software",
        "field_topics": "",
        "topics-product": "Software",
        "field_final_guidance_1": "Draft",
        "open-comment": "  Yes ",
        "field_comment_close_date": "10/20/2026",
        "field_docket_number": '<a href="https://www.regulations.gov/docket/FDA-2026-D-0042">FDA-2026-D-0042</a>',
        "field_communication_type": "Guidance Document",
        "field_center": "Center for Devices and Radiological Health",
        "field_regulated_product_field": "Medical Devices",
        "changed": '<time datetime="2026-08-20T09:00:00-04:00">2026-08-20 09:00</time>\n',
    },
]

FDA_GUIDANCE_FIXTURE_MISSING_FIELDS = [
    {"title": "no link here, just text", "field_issue_datetime": "07/01/2001"},  # title has no <a href>
    {
        "title": '<a href="/x">Missing date record</a>',
        "field_issue_datetime": "",
    },  # empty issue date
    {
        "title": '<a href="/x">Malformed date record</a>',
        "field_issue_datetime": "2026-08-20",
    },  # ISO, not the documented MM/DD/YYYY
]

FDA_GUIDANCE_FIXTURE_NO_DOCKET_NO_SUMMARY_FIELDS = [
    {
        "title": '<a href="/x">Bare record</a>',
        "field_issue_datetime": "01/15/2026",
        "field_docket_number": "",
        "field_communication_type": "",
        "field_issuing_office_taxonomy": "",
        "field_regulated_product_field": "",
    },
]


class ParseFdaGuidanceJson(unittest.TestCase):
    def test_extracts_two_records(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)
        self.assertEqual(len(items), 2)

    def test_source_key_is_fda_guidance(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)
        self.assertTrue(all(i["source_key"] == "fda_guidance" for i in items))

    def test_title_is_extracted_from_the_anchor_text(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)
        self.assertIn("Clinical Decision Support Software", items[1]["title"])
        self.assertNotIn("<a", items[1]["title"])

    def test_url_is_built_from_the_relative_href(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)
        self.assertEqual(
            items[0]["url"],
            "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/small-entity-compliance-guide-safe-handling-statements-labeling-shell-eggs-and-refrigeration-shell",
        )

    def test_us_date_format_is_converted_to_iso(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)
        self.assertEqual(items[0]["published_date"], "2001-07-01")
        self.assertEqual(items[1]["published_date"], "2026-08-20")

    def test_id_hint_is_the_docket_number(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)
        self.assertEqual(items[0]["id_hint"], "docket:FDA-2020-D-1954")
        self.assertEqual(items[1]["id_hint"], "docket:FDA-2026-D-0042")

    def test_summary_is_assembled_from_structured_fields(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)
        summary = items[1]["summary"]
        self.assertIn("Guidance Document", summary)
        self.assertIn("Center for Devices and Radiological Health", summary)
        self.assertIn("Medical Devices", summary)

    def test_html_entities_in_summary_fields_are_unescaped(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)
        self.assertIn("Food & Beverages", items[0]["summary"])
        self.assertNotIn("&amp;", items[0]["summary"])

    def test_missing_title_link_missing_date_and_malformed_date_are_skipped(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE_MISSING_FIELDS)
        self.assertEqual(items, [])

    def test_missing_docket_and_empty_summary_fields_degrade_gracefully(self):
        items = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE_NO_DOCKET_NO_SUMMARY_FIELDS)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["id_hint"])
        self.assertEqual(items[0]["summary"], "")

    def test_empty_records_list_returns_empty_list(self):
        self.assertEqual(ingest.parse_fda_guidance_json([]), [])


REGULATIONS_GOV_FIXTURE = {
    # Real record shape, captured live from api.regulations.gov/v4/documents
    # (2026-09-02) via the shared DEMO_KEY — see the module docstring.
    "data": [
        {
            "type": "documents",
            "id": "FDA-2026-N-10100-0001",
            "attributes": {
                "agencyId": "FDA",
                "objectId": "09000064b94b538f",
                "frDocNum": None,
                "documentType": "Notice",
                "withdrawn": False,
                "highlightedContent": "",
                "commentEndDate": "2026-11-23T04:59:59Z",
                "commentStartDate": "2026-09-23T04:00:00Z",
                "lastModifiedDate": "2026-08-31T22:10:19Z",
                "openForComment": True,
                "withinCommentPeriod": False,
                "postedDate": "2026-08-31T04:00:00Z",
                "title": "FDA Scientific Public Workshop: Clinical Decision Support Software Using Artificial Intelligence",
                "docketId": "FDA-2026-N-10100",
                "subtype": "Meeting",
                "allowLateComments": False,
            },
        },
        {
            "type": "documents",
            "id": "EPA-R05-OW-2026-1618-0106",
            "attributes": {
                "agencyId": "EPA",
                "objectId": "09000064b94b66be",
                "frDocNum": None,
                "documentType": "Supporting & Related Material",
                "withdrawn": False,
                "highlightedContent": "",
                "commentEndDate": None,
                "commentStartDate": None,
                "lastModifiedDate": "2026-09-01T00:25:38Z",
                "openForComment": False,
                "withinCommentPeriod": False,
                "postedDate": "2026-08-31T04:00:00Z",
                "title": "1.0 MPC Permit Class I Final 1-15-2015",
                "docketId": "EPA-R05-OW-2026-1618",
                "subtype": None,
                "allowLateComments": False,
            },
        },
    ],
    "meta": {"hasNextPage": True, "totalElements": 47},
}

REGULATIONS_GOV_FIXTURE_MISSING_FIELDS = {
    "data": [
        {"id": "X-1", "attributes": {"title": "", "postedDate": "2026-08-31T04:00:00Z"}},  # empty title
        {"id": "", "attributes": {"title": "No id", "postedDate": "2026-08-31T04:00:00Z"}},  # empty id
        {"id": "X-2", "attributes": {"title": "No date"}},  # missing postedDate
        {"id": "X-3", "attributes": {"title": "Bad date", "postedDate": "not-a-date"}},  # unparseable
    ]
}

REGULATIONS_GOV_FIXTURE_NO_COMMENT_PERIOD = {
    "data": [
        {
            "id": "X-4",
            "attributes": {
                "title": "Bare record",
                "postedDate": "2026-01-15T00:00:00Z",
                "documentType": "",
                "agencyId": "",
                "docketId": "",
                "openForComment": False,
                "commentEndDate": None,
            },
        },
    ]
}


class ParseRegulationsGovJson(unittest.TestCase):
    def test_extracts_two_records(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)
        self.assertEqual(len(items), 2)

    def test_source_key_is_regulations_gov(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)
        self.assertTrue(all(i["source_key"] == "regulations_gov" for i in items))

    def test_url_is_built_from_the_document_id(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)
        self.assertEqual(items[0]["url"], "https://www.regulations.gov/document/FDA-2026-N-10100-0001")

    def test_posted_date_datetime_is_truncated_to_date(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)
        self.assertEqual(items[0]["published_date"], "2026-08-31")

    def test_id_hint_is_docket_prefixed_document_id(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)
        self.assertEqual(items[0]["id_hint"], "docket:FDA-2026-N-10100-0001")

    def test_summary_is_assembled_from_structured_attributes(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)
        summary = items[0]["summary"]
        self.assertIn("Notice", summary)
        self.assertIn("Agency: FDA", summary)
        self.assertIn("Docket: FDA-2026-N-10100", summary)

    def test_open_comment_period_is_included_in_summary_when_present(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)
        self.assertIn("Open for comment until 2026-11-23", items[0]["summary"])

    def test_closed_comment_period_is_not_mentioned_in_summary(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)
        self.assertNotIn("Open for comment", items[1]["summary"])

    def test_records_missing_required_fields_are_skipped(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE_MISSING_FIELDS)
        self.assertEqual(items, [])

    def test_empty_structured_attributes_degrade_to_empty_summary(self):
        items = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE_NO_COMMENT_PERIOD)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["summary"], "")

    def test_missing_data_key_returns_empty_list(self):
        self.assertEqual(ingest.parse_regulations_gov_json({}), [])

    def test_empty_data_list_returns_empty_list(self):
        self.assertEqual(ingest.parse_regulations_gov_json({"data": []}), [])


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

    def test_industry_press_rss_source_keys_are_registered_in_the_real_config(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        for key in ("stat_news", "fierce_healthcare"):
            source = self.source_registry.get_source(registry, key)
            self.assertEqual(source["category"], "industry_press")
            self.assertIn("feed_url", source)

    def test_medrxiv_item_canonicalizes_via_its_doi_id_hint(self):
        item = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)[0]
        canonical = self.dedup_store.canonicalize_id(item["url"], item["id_hint"])
        self.assertEqual(canonical, "doi:10.1101/2026.08.15.26000001")

    def test_medrxiv_item_scores_against_the_real_registry_preprint_category(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)[0]
        score = self.source_registry.score_source_item(registry, item["source_key"], age_days=3)
        # preprint: floor 0.4, half_life 3 days -> same curve as arxiv/industry_press at age=3.
        self.assertAlmostEqual(score, 0.4 + 0.6 * 0.5)

    def test_medrxiv_throughline_classification_on_a_real_parsed_title(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_medrxiv_json(MEDRXIV_FIXTURE)[0]
        scope = self.source_registry.classify_topic_scope(item["title"], registry["throughline_keywords"])
        self.assertEqual(scope, "throughline")  # title contains "agentic" and "FHIR-based"

    def test_medrxiv_source_key_is_registered_in_the_real_config_as_preprint(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        source = self.source_registry.get_source(registry, "medrxiv")
        self.assertEqual(source["category"], "preprint")

    def test_fda_guidance_item_canonicalizes_via_its_docket_id_hint(self):
        item = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)[1]
        canonical = self.dedup_store.canonicalize_id(item["url"], item["id_hint"])
        self.assertEqual(canonical, "docket:FDA-2026-D-0042")

    def test_fda_guidance_item_scores_against_the_real_registry_regulatory_category(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)[0]
        score = self.source_registry.score_source_item(registry, item["source_key"], age_days=0)
        self.assertAlmostEqual(score, 1.0)  # regulatory: floor 0.9, half_life 30d -> 1.0 at age 0

    def test_fda_guidance_throughline_classification_on_a_real_parsed_title(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_fda_guidance_json(FDA_GUIDANCE_FIXTURE)[1]
        scope = self.source_registry.classify_topic_scope(item["title"], registry["throughline_keywords"])
        self.assertEqual(scope, "throughline")  # title contains "Clinical Decision Support" and "Artificial Intelligence"

    def test_fda_guidance_source_key_is_registered_in_the_real_config_as_regulatory(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        source = self.source_registry.get_source(registry, "fda_guidance")
        self.assertEqual(source["category"], "regulatory")

    def test_regulations_gov_item_canonicalizes_via_its_docket_id_hint(self):
        item = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)[0]
        canonical = self.dedup_store.canonicalize_id(item["url"], item["id_hint"])
        self.assertEqual(canonical, "docket:FDA-2026-N-10100-0001")

    def test_regulations_gov_item_scores_against_the_real_registry_regulatory_category(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)[0]
        score = self.source_registry.score_source_item(registry, item["source_key"], age_days=0)
        self.assertAlmostEqual(score, 1.0)  # regulatory: floor 0.9, half_life 30d -> 1.0 at age 0

    def test_regulations_gov_throughline_classification_on_a_real_parsed_title(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_regulations_gov_json(REGULATIONS_GOV_FIXTURE)[0]
        scope = self.source_registry.classify_topic_scope(item["title"], registry["throughline_keywords"])
        self.assertEqual(scope, "throughline")  # title contains "Clinical Decision Support" and "Artificial Intelligence"

    def test_regulations_gov_source_key_is_registered_in_the_real_config_as_regulatory(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        source = self.source_registry.get_source(registry, "regulations_gov")
        self.assertEqual(source["category"], "regulatory")

    def test_onc_astp_source_key_is_registered_in_the_real_config_as_regulatory_with_a_feed_url(self):
        # onc_astp is unlike the other three RSS sources: same parser, but
        # category "regulatory" (not "industry_press"), since it's an
        # agency's own blog, not third-party press coverage.
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        source = self.source_registry.get_source(registry, "onc_astp")
        self.assertEqual(source["category"], "regulatory")
        self.assertIn("feed_url", source)

    def test_onc_astp_item_scores_against_the_real_registry_regulatory_category(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_rss_xml(RSS_FIXTURE_ONC_ASTP_SHAPE, source_key="onc_astp")[0]
        score = self.source_registry.score_source_item(registry, item["source_key"], age_days=0)
        self.assertAlmostEqual(score, 1.0)  # regulatory: floor 0.9, half_life 30d -> 1.0 at age 0

    def test_onc_astp_throughline_classification_on_a_real_parsed_title(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_rss_xml(RSS_FIXTURE_ONC_ASTP_SHAPE, source_key="onc_astp")[0]
        scope = self.source_registry.classify_topic_scope(item["title"], registry["throughline_keywords"])
        self.assertEqual(scope, "broad_industry")  # title contains none of the throughline keywords

    def test_onc_astp_item_canonicalizes_via_url_fallback(self):
        item = ingest.parse_rss_xml(RSS_FIXTURE_ONC_ASTP_SHAPE, source_key="onc_astp")[0]
        canonical = self.dedup_store.canonicalize_id(item["url"], item["id_hint"])
        self.assertTrue(canonical.startswith("url:"))  # no id_hint for blog posts, same as the other RSS sources

    def test_cms_source_key_is_registered_in_the_real_config_as_regulatory_with_a_feed_url(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        source = self.source_registry.get_source(registry, "cms")
        self.assertEqual(source["category"], "regulatory")
        self.assertIn("feed_url", source)

    def test_cms_item_scores_against_the_real_registry_regulatory_category(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        item = ingest.parse_rss_xml(RSS_FIXTURE_CMS_SHAPE, source_key="cms")[0]
        score = self.source_registry.score_source_item(registry, item["source_key"], age_days=0)
        self.assertAlmostEqual(score, 1.0)  # regulatory: floor 0.9, half_life 30d -> 1.0 at age 0

    def test_cms_item_canonicalizes_via_the_recovered_url(self):
        item = ingest.parse_rss_xml(RSS_FIXTURE_CMS_SHAPE, source_key="cms")[0]
        canonical = self.dedup_store.canonicalize_id(item["url"], item["id_hint"])
        self.assertTrue(canonical.startswith("url:"))  # no id_hint, but the recovered (not corrupted) URL is hashed

    def test_all_four_rss_source_keys_are_registered_in_the_real_config(self):
        registry = self.source_registry.load_registry(os.path.join(HERE, "..", "config", "sources.json"))
        for key in ("stat_news", "fierce_healthcare", "onc_astp", "cms"):
            source = self.source_registry.get_source(registry, key)
            self.assertIn("feed_url", source)


if __name__ == "__main__":
    unittest.main()
