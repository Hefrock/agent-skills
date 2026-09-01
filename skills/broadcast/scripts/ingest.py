#!/usr/bin/env python3
"""Ingest adapters for the daily healthcare AI briefing (Phase 3b).

Same split as decay.ts (mcp/evidence-pinning) and dedup_store.py's
embed_text(): every source adapter is a pure XML-parsing function (fixture-
testable, no network) plus a thin network-calling wrapper that fetches live
data and hands it to the parser. Only the parsers are unit-tested in depth;
the fetch wrappers are simple enough (build a URL, GET it, hand the body to
the parser) that they don't need their own logic tests beyond what a real
pipeline run exercises.

Covers PubMed, arXiv, and — as of this pass — the three industry-press RSS
sources (STAT News, Fierce Healthcare, Healthcare IT News), which share one
generic RSS 2.0 parser since it's one format regardless of which outlet.
medRxiv, FDA guidance, regulations.gov, ONC/ASTP, and CMS are still
follow-up adapters, not attempted here — each has a different enough
response shape that bundling them all into one unverified pass would
violate the same incremental-and-tested discipline this project has
followed since Phase 1.

RSS feed verification status (config/sources.json's feed_url_verified,
confirmed live via GitHub Actions on 2026-09-01 — see the smoke test
workflow, .github/workflows/broadcast-live-smoke-test.yml):
  - stat_news: verified working. Needed a browser-like User-Agent — the
    default urllib one ("Python-urllib/3.x") is a common anti-bot
    blocklist entry, distinct from any egress-policy block.
  - fierce_healthcare: verified working. Same User-Agent fix, plus a real
    parsing bug: this feed's <pubDate> isn't RFC 822 ("Sep 1, 2026
    2:08pm" — no day-of-week, 12hr+am/pm, no timezone) despite being an
    otherwise valid RSS 2.0 feed. _parse_rss_pubdate() falls back to that
    exact format when RFC 822 parsing fails.
  - healthcare_it_news: confirmed BLOCKED, not just unverified. HTTP 403
    on this URL and three other guessed variants, even with a full
    Chrome-like header set (User-Agent, Accept, Accept-Language,
    Referer) — looks like WAF/Cloudflare-style bot protection a plain
    HTTP client structurally can't pass. Left broken and documented
    (config/sources.json's feed_url_verified_note) rather than silently
    dropped or faked working; needs a different approach (headless
    browser, an alternate syndication endpoint) as a future follow-up.

Every adapter produces the same normalized item shape, which is what feeds
directly into the rest of the pipeline: dedup_store.classify_story() (via
canonical_id/title), source_registry.classify_topic_scope() (via title +
summary text) and score_source_item() (via source_key + age), and
evidence-pinning-mcp's pin_claim (via url/id_hint + summary as the excerpt
source). See normalize_item()'s docstring for the exact shape.

Regex-based XML parsing, not a real XML parser — same tradeoff
mcp/evidence-pinning/src/decay.ts documents for PubMed's efetch XML: no XML
dependency in this repo yet, works for the typical flat tag structure both
NCBI and arXiv return, would miss a namespaced or attribute-heavy variant.

stdlib only, matching this repo's other reference tooling.
"""

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime
from email.utils import parsedate_to_datetime

FETCH_TIMEOUT_S = 15.0

# Used by fetch_rss() — see its docstring/comment for why.
_USER_AGENT = "Mozilla/5.0 (compatible; healthcare-ai-briefing/0.1; +https://github.com/Hefrock/agent-skills)"

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def normalize_item(source_key: str, title: str, url: str, published_date: str, summary: str, id_hint: str | None = None) -> dict:
    """The common shape every adapter produces. published_date must be an
    ISO YYYY-MM-DD string — every downstream consumer (source_registry's
    age-based scoring, dedup_store's date comparisons) assumes that format
    and will raise on anything else, deliberately, rather than silently
    mis-parsing a locale-specific date string."""
    date.fromisoformat(published_date)  # raises ValueError if malformed — fail here, not three stages downstream
    return {
        "source_key": source_key,
        "title": title.strip(),
        "url": url,
        "id_hint": id_hint,
        "published_date": published_date,
        "summary": summary.strip(),
    }


# ── PubMed (NCBI E-utilities) ────────────────────────────────────────────
#
# Two-step: esearch.fcgi (query -> PMIDs) then efetch.fcgi (PMIDs -> full
# records). Uses efetch rather than esummary because esummary's JSON doesn't
# include the abstract text, and the abstract is what becomes the excerpt
# evidence-pinning-mcp's pin_claim needs.

def _parse_pubmed_date(block: str) -> str | None:
    """Extract YYYY-MM-DD from a <Year>/<Month>/<Day> block. Month may be
    numeric ("08") or a 3-letter abbreviation ("Aug") — PubMed uses both
    depending on the record. Day is sometimes absent; defaults to 1 rather
    than dropping the whole date, since "some date this month" beats "no
    date at all" for age-based scoring. Year-only records (Day and Month both
    absent) are more uncertain but still usable — return YYYY-01-01."""
    year_match = re.search(r"<Year>(\d{4})</Year>", block)
    if not year_match:
        return None
    year = int(year_match.group(1))

    month_match = re.search(r"<Month>(\w+)</Month>", block)
    month = 1
    if month_match:
        raw = month_match.group(1)
        month = int(raw) if raw.isdigit() else _MONTH_NAMES.get(raw.lower()[:3], 1)

    day_match = re.search(r"<Day>(\d{1,2})</Day>", block)
    day = int(day_match.group(1)) if day_match else 1

    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return date(year, month, 1).isoformat()  # e.g. a nonexistent day for that month


def parse_pubmed_efetch_xml(xml: str) -> list[dict]:
    articles = re.findall(r"<PubmedArticle>.*?</PubmedArticle>", xml, re.DOTALL)
    items = []
    for article in articles:
        pmid_match = re.search(r"<PMID[^>]*>(\d+)</PMID>", article)
        title_match = re.search(r"<ArticleTitle>(.*?)</ArticleTitle>", article, re.DOTALL)
        if not pmid_match or not title_match:
            continue  # malformed record — skip it rather than crash the whole batch

        pmid = pmid_match.group(1)
        title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

        abstract_parts = re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", article, re.DOTALL)
        summary = " ".join(re.sub(r"<[^>]+>", "", p).strip() for p in abstract_parts)

        # Prefer the electronic ArticleDate (when a preprint-style dated
        # e-pub exists); fall back to the Journal/JournalIssue PubDate.
        article_date_match = re.search(r"<ArticleDate[^>]*>(.*?)</ArticleDate>", article, re.DOTALL)
        pub_date_match = re.search(r"<PubDate>(.*?)</PubDate>", article, re.DOTALL)
        date_block = article_date_match.group(1) if article_date_match else (pub_date_match.group(1) if pub_date_match else "")
        published = _parse_pubmed_date(date_block)
        if published is None:
            continue  # no usable date — can't be scored downstream, skip rather than fabricate one

        items.append(normalize_item(
            source_key="pubmed",
            title=title,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}",
            published_date=published,
            summary=summary,
            id_hint=f"pmid:{pmid}",
        ))
    return items


def fetch_pubmed(query: str, max_results: int = 20, api_key: str | None = None) -> list[dict]:
    search_params = {"db": "pubmed", "term": query, "retmax": str(max_results), "retmode": "json", "sort": "date"}
    if api_key:
        search_params["api_key"] = api_key
    search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{urllib.parse.urlencode(search_params)}"
    with urllib.request.urlopen(search_url, timeout=FETCH_TIMEOUT_S) as resp:
        search_result = json.loads(resp.read().decode("utf-8"))
    pmids = search_result.get("esearchresult", {}).get("idlist", [])
    if not pmids:
        return []

    fetch_params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
    if api_key:
        fetch_params["api_key"] = api_key
    fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{urllib.parse.urlencode(fetch_params)}"
    with urllib.request.urlopen(fetch_url, timeout=FETCH_TIMEOUT_S) as resp:
        xml = resp.read().decode("utf-8")
    return parse_pubmed_efetch_xml(xml)


# ── arXiv ─────────────────────────────────────────────────────────────────

def parse_arxiv_atom_xml(xml: str) -> list[dict]:
    entries = re.findall(r"<entry>.*?</entry>", xml, re.DOTALL)
    items = []
    for entry in entries:
        id_match = re.search(r"<id>(.*?)</id>", entry)
        title_match = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
        summary_match = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
        published_match = re.search(r"<published>(\d{4}-\d{2}-\d{2})T", entry)
        if not id_match or not title_match or not published_match:
            continue  # malformed entry — skip it rather than crash the whole batch

        arxiv_url = id_match.group(1).strip()
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", summary_match.group(1))).strip() if summary_match else ""

        items.append(normalize_item(
            source_key="arxiv",
            title=title,
            url=arxiv_url,
            published_date=published_match.group(1),
            summary=summary,
            # arXiv IDs aren't DOIs or PMIDs — canonicalize_id() falls back
            # to hashing the arXiv URL itself, which is exactly right here.
            id_hint=None,
        ))
    return items


def fetch_arxiv(query: str, max_results: int = 20) -> list[dict]:
    params = {"search_query": query, "start": "0", "max_results": str(max_results), "sortBy": "submittedDate", "sortOrder": "descending"}
    url = f"http://export.arxiv.org/api/query?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:
        xml = resp.read().decode("utf-8")
    return parse_arxiv_atom_xml(xml)


# ── Industry-press RSS (STAT News, Fierce Healthcare, Healthcare IT News) ──
#
# One generic RSS 2.0 parser shared by all three, since it's a single
# standard format — the only per-outlet difference is which feed_url is
# passed in, which lives in config/sources.json, not in code.

def _clean_html_text(raw: str) -> str:
    """RSS <description> content is routinely CDATA-wrapped, carries inline
    HTML tags, and uses HTML entities — strip all three down to plain text."""
    text = raw.strip()
    cdata_match = re.match(r"^<!\[CDATA\[(.*)\]\]>$", text, re.DOTALL)
    if cdata_match:
        text = cdata_match.group(1)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss_pubdate(raw: str) -> str | None:
    """RSS <pubDate> is supposed to be RFC 822 (e.g. "Mon, 15 Aug 2026
    12:00:00 GMT" or with a numeric offset like "+0000") — most feeds do
    this correctly, so email.utils.parsedate_to_datetime (the stdlib's
    actual RFC 822 parser) is tried first. Confirmed live against
    fiercehealthcare.com's real feed: it ships a spec-violating custom
    format instead ("Sep 1, 2026 2:08pm" — no day-of-week, 12-hour clock
    with am/pm, no timezone), so that's tried as a fallback rather than
    dropping every item from a feed that just isn't RFC 822 compliant."""
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.strptime(raw, "%b %d, %Y %I:%M%p").date().isoformat()
    except ValueError:
        return None


def parse_rss_xml(xml: str, source_key: str) -> list[dict]:
    items_xml = re.findall(r"<item>.*?</item>", xml, re.DOTALL)
    items = []
    for item_xml in items_xml:
        title_match = re.search(r"<title>(.*?)</title>", item_xml, re.DOTALL)
        link_match = re.search(r"<link>(.*?)</link>", item_xml, re.DOTALL)
        pubdate_match = re.search(r"<pubDate>(.*?)</pubDate>", item_xml, re.DOTALL)
        if not title_match or not link_match or not pubdate_match:
            continue  # malformed item — skip it rather than crash the whole feed

        published = _parse_rss_pubdate(pubdate_match.group(1))
        if published is None:
            continue  # unparseable date — can't be scored downstream

        description_match = re.search(r"<description>(.*?)</description>", item_xml, re.DOTALL)
        summary = _clean_html_text(description_match.group(1)) if description_match else ""

        items.append(normalize_item(
            source_key=source_key,
            title=_clean_html_text(title_match.group(1)),
            url=_clean_html_text(link_match.group(1)),
            published_date=published,
            summary=summary,
            id_hint=None,  # industry press articles have no DOI/PMID; canonicalize_id() falls back to a URL hash
        ))
    return items


def fetch_rss(feed_url: str, source_key: str) -> list[dict]:
    # Confirmed live (2026-09-01 GitHub Actions run): stat_news and
    # healthcare_it_news returned HTTP 403 without a browser-like
    # User-Agent — urllib's default ("Python-urllib/3.x") is a common
    # anti-bot blocklist entry. PubMed/arXiv's E-utilities/API don't need
    # this (they're built for programmatic access), but outlet RSS feeds
    # sitting behind the same CDN/WAF as the outlet's main site often do.
    request = urllib.request.Request(feed_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as resp:
        xml = resp.read().decode("utf-8")
    return parse_rss_xml(xml, source_key)
