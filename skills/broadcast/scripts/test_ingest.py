#!/usr/bin/env python3
"""
Unit tests for ingest.py's pure parsers (parse_pubmed_efetch_xml,
parse_arxiv_atom_xml) and normalize_item(), against fixture XML built from
NCBI E-utilities' and arXiv's documented response shapes — not captured
from a live API call, same caveat as mcp/evidence-pinning's PubMed
classifier tests. The network-calling wrappers (fetch_pubmed, fetch_arxiv)
are deliberately not covered here — see the module docstring for why.

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


if __name__ == "__main__":
    unittest.main()
