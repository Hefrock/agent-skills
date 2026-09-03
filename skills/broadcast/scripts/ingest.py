#!/usr/bin/env python3
"""Ingest adapters for the daily healthcare AI briefing (Phase 3b).

Same split as decay.ts (mcp/evidence-pinning) and dedup_store.py's
embed_text(): every source adapter is a pure XML-parsing function (fixture-
testable, no network) plus a thin network-calling wrapper that fetches live
data and hands it to the parser. Only the parsers are unit-tested in depth;
the fetch wrappers are simple enough (build a URL, GET it, hand the body to
the parser) that they don't need their own logic tests beyond what a real
pipeline run exercises.

Covers PubMed, arXiv, five industry/agency RSS sources (STAT News,
Fierce Healthcare, HIT Consultant, ONC/ASTP's blog, CMS's newsroom),
which share one generic RSS 2.0 parser since it's one format regardless
of which outlet, medRxiv (api.medrxiv.org's JSON /details endpoint), FDA
guidance documents, regulations.gov, and FDA MAUDE device adverse events
(api.fda.gov/device/event.json). That's all 11 registered sources in
config/sources.json.

RSS feed verification status (config/sources.json's feed_url_verified,
confirmed live via GitHub Actions — see the smoke test workflow,
.github/workflows/broadcast-live-smoke-test.yml):
  - stat_news: verified working (2026-09-01). Needed a browser-like
    User-Agent — the default urllib one ("Python-urllib/3.x") is a
    common anti-bot blocklist entry, distinct from any egress-policy
    block.
  - fierce_healthcare: verified working (2026-09-01). Same User-Agent
    fix, plus a real parsing bug: this feed's <pubDate> isn't RFC 822
    ("Sep 1, 2026 2:08pm" — no day-of-week, 12hr+am/pm, no timezone)
    despite being an otherwise valid RSS 2.0 feed. _parse_rss_pubdate()
    falls back to that exact format when RFC 822 parsing fails.
  - healthcare_it_news: confirmed BLOCKED, not just unverified. HTTP 403
    on this URL and three other guessed variants, even with a full
    Chrome-like header set (User-Agent, Accept, Accept-Language,
    Referer) — looks like WAF/Cloudflare-style bot protection a plain
    HTTP client structurally can't pass. Left broken and documented
    (config/sources.json's feed_url_verified_note) rather than silently
    dropped or faked working; needs a different approach (headless
    browser, an alternate syndication endpoint) as a future follow-up.
  - onc_astp: verified working (2026-09-02), the simplest of the five —
    a standard WordPress RSS feed (healthit.gov/buzz-blog/feed) that
    this generic parser already handled correctly with zero code
    changes: real RFC-822 pubDates, no fierce_healthcare-style fallback
    needed. Found by probing real candidates rather than trusting the
    first URL a search turned up — one guessed slug (/buzz-blog/rss.xml)
    404'd, and the bare site-root /feed exists but is a different,
    much shorter, non-blog feed.
  - cms: verified working (2026-09-02), but the messiest feed so far —
    two real parser bugs found and fixed, not just a feed_url wired up.
    (1) <link> is corrupted: it's a URL-percent-encoded copy of the
    <title> field's own "<a href=...>...</a>" markup pasted in verbatim,
    not a clean URL. _recover_link_from_corrupted_field() detects this
    (a "%3c" marker) and rebuilds the real URL from the title's own href
    plus the still-intact scheme+host prefix at the start of the
    corrupted <link> value. (2) <pubDate> uses a third undocumented
    format ("Tue, 09/01/2026 - 09:02" — day name, US MM/DD/YYYY, " - ",
    24hr time, no timezone), beyond RFC 822 and fierce_healthcare's
    format; _parse_rss_pubdate() gets a second fallback. Diagnosing this
    also surfaced a real, general bug in _clean_html_text() unrelated to
    either of those: cms.gov's <description> is HTML-entity-double-
    encoded ("&lt;p&gt;...&lt;/p&gt;", not literal "<p>" like the other
    sources' CDATA blocks), and stripping those (now-revealed) tags
    outright glued adjacent paragraphs together with no space
    ("...Hawaii" + "This federal..." -> "HawaiiThis"). Tags are now
    replaced with a space (collapsed by the existing whitespace pass)
    instead of deleted, and stripped both before and after unescaping.

medRxiv (fetch_medrxiv) verification status: verified working, but only
after a real bug was found and fixed via the same live-smoke-test loop.
api.medrxiv.org's documented "Nd" interval shorthand ("7d" = 7 most recent
days) and a bare numeric count both return HTTP 200 with an EMPTY
collection and {"status": "Both dates must be in yyyy-mm-dd format"} —
silently zero results, not an error. Confirmed live (2026-09-01) that only
an explicit start/end date range actually returns data; fetch_medrxiv()
computes one from the days lookback rather than using the shorthand.

FDA guidance (fetch_fda_guidance) data source: fda.gov's own "Search for
FDA Guidance Documents" page (fda.gov/regulatory-information/search-fda-
guidance-documents) has no dedicated RSS feed (three guessed slugs all
404'd) and no documented JSON/REST API — openFDA doesn't cover guidance
text. The page itself ships a plain server-rendered HTML shell with an
EMPTY <tbody>: the visible table is a jQuery DataTables instance
populated client-side. Traced the real data source by fetching the
page's aggregated footer JS bundles and finding the DataTables init call
inline: it points at a static, unauthenticated JSON file —
fda.gov/files/api/datatables/static/search-for-guidance.json — that is
the entire dataset (confirmed live: 2786 real records, not paginated).
That file's records use Drupal's raw field shape: title is an HTML
"<a href=...>text</a>" string (not two separate fields), issue date is
US "MM/DD/YYYY" (not ISO), and there is no abstract/summary field at all
— parse_fda_guidance_json() builds a short, entirely-factual summary
from the structured fields that do exist (communication type, issuing
office, regulated product), rather than inventing narrative text for a
field the source doesn't provide.

regulations.gov (fetch_regulations_gov) data source: the official,
documented v4 /documents API (open.gsa.gov/api/regulationsgov/), unlike
FDA guidance — but documented behavior has been wrong before (medRxiv's
"Nd" shorthand), so this was still confirmed live before writing the
parser. Confirmed: the shared DEMO_KEY (sent via the X-Api-Key header)
works with no registration, rate-limited to 10 requests/hour, which is
adequate for one ingest call plus occasional smoke-test runs — a real
key (api.data.gov, free, 1000 req/hr) would be needed if this pipeline's
call volume grows. Response is standard JSON:API
({"data": [...], "meta": {...}}, each item as {"id", "type",
"attributes": {...}}); "id" (e.g. "FDA-2026-N-10100-0001") is the
regulations.gov document id, more specific than the docket-level
"docketId" attribute, and is used as the docket: id_hint (same prefix as
FDA guidance's docket numbers — both are regulations.gov-family
identifiers). The human-facing document URL
(regulations.gov/document/{id}) was confirmed live to resolve. Like FDA
guidance, there is no abstract/summary attribute; the summary is
assembled from real structured attributes (documentType, agencyId,
docketId, open-for-comment status) rather than invented.

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
from datetime import date, datetime, timedelta
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


# ── RSS sources (STAT News, Fierce Healthcare, HIT Consultant, ────────────
#    ONC/ASTP's blog, CMS's newsroom) ──────────────────────────────────────
#
# One generic RSS 2.0 parser shared by all five, regardless of category
# (industry_press vs. regulatory) — since it's still a single format
# underneath, the only per-outlet difference is the feed_url passed in
# (config/sources.json, not code), plus a small set of accumulated
# real-world fallbacks for feeds that don't quite follow spec.

def _clean_html_text(raw: str) -> str:
    """RSS <description> content is routinely CDATA-wrapped, carries inline
    HTML tags, and uses HTML entities — strip all three down to plain text.
    Tags are replaced with a space, not deleted outright, and collapsed by
    the final whitespace pass: cms.gov's real feed (confirmed live,
    2026-09-02) packs multiple <p> paragraphs with no space between them
    ("...Hawaii</p><p>This federal investment..."), and deleting the tags
    outright would glue the words together ("HawaiiThis"). Tags are
    stripped both before AND after unescaping: stat_news/fierce_healthcare's
    CDATA blocks carry literal tags, but cms.gov double-encodes its markup
    instead ("&lt;p&gt;...&lt;/p&gt;" — HTML-entity-escaped tags, not
    literal ones), which the first pass can't see until unescaping reveals
    them."""
    text = raw.strip()
    cdata_match = re.match(r"^<!\[CDATA\[(.*)\]\]>$", text, re.DOTALL)
    if cdata_match:
        text = cdata_match.group(1)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss_pubdate(raw: str) -> str | None:
    """RSS <pubDate> is supposed to be RFC 822 (e.g. "Mon, 15 Aug 2026
    12:00:00 GMT" or with a numeric offset like "+0000") — most feeds do
    this correctly, so email.utils.parsedate_to_datetime (the stdlib's
    actual RFC 822 parser) is tried first. Confirmed live against
    fiercehealthcare.com's real feed: it ships a spec-violating custom
    format instead ("Sep 1, 2026 2:08pm" — no day-of-week, 12-hour clock
    with am/pm, no timezone), so that's tried as a fallback rather than
    dropping every item from a feed that just isn't RFC 822 compliant.
    Confirmed live against cms.gov's real newsroom feed: yet a third
    custom format ("Tue, 09/01/2026 - 09:02" — day name, then US
    MM/DD/YYYY, then " - ", then 24hr time, no seconds/timezone), so
    that's a second fallback."""
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.strptime(raw, "%b %d, %Y %I:%M%p").date().isoformat()
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, "%a, %m/%d/%Y - %H:%M").date().isoformat()
    except ValueError:
        return None


def _recover_link_from_corrupted_field(link_raw: str, title_raw: str) -> str | None:
    """Confirmed live against cms.gov's real newsroom feed (2026-09-02):
    its <link> field isn't a URL at all — it's a URL-percent-encoded copy
    of the <title> field's own "<a href=...>...</a>" markup pasted in
    verbatim (e.g. "https://www.cms.gov/%3Ca%20href%3D%22/newsroom/
    press-releases/...%22..."), not a clean link. The real destination
    path is still recoverable from the raw <title> field's own
    <a href="..."> (same title-wrapped-in-<a> shape fierce_healthcare
    uses, just with a broken <link> alongside it here); the scheme+host
    prefix is still intact at the start of the corrupted <link> value, so
    that's reused rather than hardcoding a domain into this otherwise
    source-agnostic parser. Returns None if either half can't be
    recovered, so the caller can skip the item like any other malformed
    one."""
    origin_match = re.match(r"^(https?://[^/%]+)", link_raw)
    href_match = re.search(r'<a[^>]+href="([^"]+)"', title_raw)
    if not origin_match or not href_match:
        return None
    href = href_match.group(1)
    return href if href.startswith("http") else origin_match.group(1) + href


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

        link_raw = link_match.group(1)
        if "%3c" in link_raw.lower():
            # cms.gov-style corrupted <link> (a URL-encoded copy of the
            # title's own markup) — recover the real URL from the title.
            url = _recover_link_from_corrupted_field(link_raw, title_match.group(1))
            if url is None:
                continue  # corrupted link with no recoverable title href — skip
        else:
            url = _clean_html_text(link_raw)

        description_match = re.search(r"<description>(.*?)</description>", item_xml, re.DOTALL)
        summary = _clean_html_text(description_match.group(1)) if description_match else ""

        items.append(normalize_item(
            source_key=source_key,
            title=_clean_html_text(title_match.group(1)),
            url=url,
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


# ── medRxiv ──────────────────────────────────────────────────────────────
#
# api.medrxiv.org's /details endpoint, unauthenticated, JSON — a different
# shape from PubMed/arXiv's XML but the same two-piece split: a pure parser
# over the decoded JSON object, plus a thin wrapper that builds the URL and
# reads it.

def parse_medrxiv_json(payload: dict) -> list[dict]:
    """payload is the parsed JSON body of a medRxiv /details response — a
    dict with a "collection" list of posting records. Each record's "doi" is
    used both as the id_hint (canonicalize_id() prefers a DOI over hashing
    the URL, same as PubMed's pmid) and to build the article URL, since the
    API itself doesn't return one. Records missing a doi, title, or date are
    skipped rather than guessed at — same policy as the PubMed/arXiv/RSS
    parsers above."""
    items = []
    for record in payload.get("collection", []):
        doi = record.get("doi")
        title = record.get("title")
        published_date = record.get("date")
        if not doi or not title or not published_date:
            continue  # incomplete record — skip it rather than crash the whole batch
        try:
            date.fromisoformat(published_date)
        except ValueError:
            continue  # medRxiv dates are documented as YYYY-MM-DD; guard rather than trust blindly

        items.append(normalize_item(
            source_key="medrxiv",
            title=title,
            url=f"https://doi.org/{doi}",
            published_date=published_date,
            summary=record.get("abstract", ""),
            id_hint=f"doi:{doi}",
        ))
    return items


def fetch_medrxiv(days: int = 7, max_results: int = 20) -> list[dict]:
    # The API's docs describe an "Nd" interval shorthand ("7d" = 7 most
    # recent days), but confirmed live (2026-09-01, GitHub Actions): "7d",
    # "14d", and a bare count ("5") all return HTTP 200 with an EMPTY
    # collection and {"status": "Both dates must be in yyyy-mm-dd format"}
    # — silently zero results rather than an error. Only an explicit
    # start/end date range actually works, confirmed live with real
    # postings returned. So the days lookback is computed into one here.
    end = date.today()
    start = end - timedelta(days=days)
    url = f"https://api.medrxiv.org/details/medrxiv/{start.isoformat()}/{end.isoformat()}/0/json"
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_medrxiv_json(payload)[:max_results]


# ── FDA guidance documents ──────────────────────────────────────────────
#
# fda.gov/files/api/datatables/static/search-for-guidance.json — a static,
# unauthenticated JSON file that is the entire guidance-documents dataset
# (thousands of records, not paginated), traced from the search page's own
# jQuery DataTables config. See the module docstring for how it was found.
# Drupal's raw field shape, unlike every other adapter's clean JSON/XML:
# title is an HTML "<a href=...>text</a>" string, dates are US
# "MM/DD/YYYY", and several fields carry HTML entities.

_TITLE_LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_DOCKET_LINK_RE = re.compile(r'<a[^>]*>([^<]+)</a>')


def _clean_fda_text(raw: str) -> str:
    """Same idea as _clean_html_text() for RSS, but simpler: these fields
    carry HTML entities and stray whitespace, not CDATA or nested tags."""
    return html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()


def parse_fda_guidance_json(records: list[dict]) -> list[dict]:
    """records is the parsed JSON body of the static datatables file — a
    flat list, no wrapper object (dataSrc:"" in the site's own DataTables
    config confirms the top-level array IS the data). Records whose title
    has no href, or whose issue date is missing/non-US-format, are skipped
    rather than guessed at — same policy as every other parser here. There
    is no abstract/summary field in this dataset at all, so the summary is
    assembled from the structured fields that DO exist (communication
    type, issuing office, regulated product) rather than inventing
    narrative text the source doesn't provide — "distill, don't invent"
    applies to what's omitted, not just what's included."""
    items = []
    for record in records:
        title_match = _TITLE_LINK_RE.search(record.get("title") or "")
        if not title_match:
            continue  # no link in the title field — can't build a URL, skip
        href, title_html = title_match.groups()
        title = _clean_fda_text(title_html)
        url = href if href.startswith("http") else f"https://www.fda.gov{href}"

        date_raw = (record.get("field_issue_datetime") or "").strip()
        if not date_raw:
            continue  # no issue date — can't be scored downstream
        try:
            published = datetime.strptime(date_raw, "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue  # not the documented MM/DD/YYYY shape — skip rather than mis-parse

        docket_match = _DOCKET_LINK_RE.search(record.get("field_docket_number") or "")
        id_hint = f"docket:{_clean_fda_text(docket_match.group(1))}" if docket_match else None

        summary_parts = []
        communication_type = _clean_fda_text(record.get("field_communication_type", ""))
        issuing_office = _clean_fda_text(record.get("field_issuing_office_taxonomy", ""))
        product = _clean_fda_text(record.get("field_regulated_product_field", ""))
        if communication_type:
            summary_parts.append(communication_type)
        if issuing_office:
            summary_parts.append(f"Issuing office: {issuing_office}")
        if product:
            summary_parts.append(f"Regulated product: {product}")

        items.append(normalize_item(
            source_key="fda_guidance",
            title=title,
            url=url,
            published_date=published,
            summary=". ".join(summary_parts),
            id_hint=id_hint,
        ))
    return items


def fetch_fda_guidance(max_results: int = 20) -> list[dict]:
    # The JSON file is the entire dataset (thousands of records spanning
    # decades), not filtered or paginated by the server — confirmed live,
    # first records observed were from 2001/2006/2008, not recent. So the
    # newest max_results are selected client-side after parsing, same as
    # every other adapter's "most recent N" contract.
    url = "https://www.fda.gov/files/api/datatables/static/search-for-guidance.json"
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:
        records = json.loads(resp.read().decode("utf-8"))
    items = parse_fda_guidance_json(records)
    items.sort(key=lambda item: item["published_date"], reverse=True)
    return items[:max_results]


# ── regulations.gov ──────────────────────────────────────────────────────
#
# api.regulations.gov/v4/documents — a real, documented, standard JSON:API
# response, unlike FDA guidance's traced-by-reverse-engineering static
# file. See the module docstring for the DEMO_KEY / id_hint / URL details
# confirmed live before this was written.

def parse_regulations_gov_json(payload: dict) -> list[dict]:
    """payload is the parsed JSON body of a /v4/documents response — a
    JSON:API document with a top-level "data" list, each item shaped as
    {"id", "type", "attributes": {...}}. "id" (e.g.
    "FDA-2026-N-10100-0001") is the regulations.gov document id — more
    specific than the docket-level "docketId" attribute — and becomes the
    docket: id_hint. Records missing an id, title, or postedDate are
    skipped rather than guessed at, same policy as every other parser
    here. There is no abstract/summary attribute in this API at all, so
    the summary is assembled from real structured attributes
    (documentType, agencyId, docketId, comment-period status) rather than
    inventing narrative text the source doesn't provide."""
    items = []
    for record in payload.get("data", []):
        doc_id = record.get("id")
        attrs = record.get("attributes") or {}
        title = attrs.get("title")
        posted_date_raw = attrs.get("postedDate")
        if not doc_id or not title or not posted_date_raw:
            continue  # incomplete record — skip it rather than crash the whole batch

        published = posted_date_raw[:10]  # "2026-08-31T04:00:00Z" -> "2026-08-31"
        try:
            date.fromisoformat(published)
        except ValueError:
            continue  # not the documented ISO-prefixed shape — skip rather than mis-parse

        summary_parts = []
        document_type = attrs.get("documentType")
        agency_id = attrs.get("agencyId")
        docket_id = attrs.get("docketId")
        if document_type:
            summary_parts.append(document_type)
        if agency_id:
            summary_parts.append(f"Agency: {agency_id}")
        if docket_id:
            summary_parts.append(f"Docket: {docket_id}")
        if attrs.get("openForComment") and attrs.get("commentEndDate"):
            summary_parts.append(f"Open for comment until {attrs['commentEndDate'][:10]}")

        items.append(normalize_item(
            source_key="regulations_gov",
            title=title,
            url=f"https://www.regulations.gov/document/{doc_id}",
            published_date=published,
            summary=". ".join(summary_parts),
            id_hint=f"docket:{doc_id}",
        ))
    return items


def fetch_regulations_gov(query: str, days: int = 30, max_results: int = 20, api_key: str | None = None) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    params = {
        "filter[searchTerm]": query,
        "filter[postedDate][ge]": since,
        "sort": "-postedDate",
        "page[size]": str(max_results),
    }
    url = f"https://api.regulations.gov/v4/documents?{urllib.parse.urlencode(params)}"
    # DEMO_KEY (10 req/hr, no registration) confirmed live to work — see
    # the module docstring. A real api.data.gov key raises that to
    # 1000 req/hr for when this pipeline's call volume needs it.
    request = urllib.request.Request(url, headers={"X-Api-Key": api_key or "DEMO_KEY"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_regulations_gov_json(payload)


# ── FDA MAUDE (device adverse events) ──────────────────────────────────────
#
# api.fda.gov/device/event.json — openFDA's Device Adverse Event API, a
# real, documented, mature government API (in production since ~2014,
# weekly-updated MAUDE data back to ~1992), unlike FDA guidance's
# reverse-engineered static file. UNLIKE every other adapter in this
# module, this one has NOT been confirmed live yet (this sandbox's network
# egress is blocked to every external host, api.fda.gov included — same
# constraint that applied to hit_consultant before the user supplied a
# real fetched response to verify the parser against). Field names below
# (mdr_report_key, date_received, device[].generic_name/brand_name/
# manufacturer_d_name, mdr_text[].text_type_code/text, event_type,
# product_problems) are openFDA's own long-documented, stable schema, not
# a guess — but "documented" isn't "confirmed against this exact parser,"
# the bar every other source here was actually held to. Needs a real
# live_smoke_test.py check (or a user-supplied real response, the same
# path that verified hit_consultant) before this is trusted the way the
# other nine sources are.
#
# Added specifically to close a real, named gap: "clinical ai assurance"
# is a config/sources.json throughline_keyword with no source backing it
# at all until now — this pipeline had regulatory/guidance coverage and
# industry-press coverage, but nothing on device safety/adverse-event
# reporting. MAUDE covers every medical device (hip implants, insulin
# pumps, everything) — scoped down to AI/software-relevant terms via the
# query below, on purpose, or this would be almost entirely off-topic
# noise for a healthcare-AI show.

def parse_fda_maude_json(payload: dict) -> list[dict]:
    """payload is the parsed JSON body of a /device/event.json response —
    {"meta": {...}, "results": [...]}, each result one MAUDE adverse
    event report. Records missing a report key, date, or any usable
    device/narrative text are skipped rather than guessed at, same policy
    as every other parser here.

    No single canonical "title" field exists on a MAUDE report (unlike a
    guidance document or a docket), so the title is assembled from the
    first device's brand_name (falling back to generic_name) plus the
    report's event_type(s) — e.g. "Acme AI Triage Software — Malfunction".
    The summary prefers the narrative "Description of Event or Problem"
    mdr_text entry (the actual free-text account of what happened) over
    the terser product_problems list, falling back to product_problems
    only when no narrative text entry exists.

    There's no stable public per-report URL in openFDA's own response (no
    "link" field, unlike regulations.gov) — the FDA's public MAUDE detail
    page pattern used here is well-known but, like the field names above,
    not live-confirmed by this codebase yet."""
    items = []
    for record in payload.get("results", []):
        report_key = record.get("mdr_report_key")
        date_received_raw = record.get("date_received")
        if not report_key or not date_received_raw:
            continue  # incomplete record — skip it rather than crash the whole batch

        try:
            published = date(int(date_received_raw[:4]), int(date_received_raw[4:6]), int(date_received_raw[6:8])).isoformat()
        except (ValueError, IndexError):
            continue  # not the documented YYYYMMDD shape — skip rather than mis-parse

        devices = record.get("device") or []
        device_name = None
        if devices:
            device_name = devices[0].get("brand_name") or devices[0].get("generic_name")
        event_types = record.get("event_type") or []
        title_parts = [device_name or "Unnamed device", " / ".join(event_types) or "Adverse event"]
        title = " — ".join(title_parts)

        narrative = ""
        for text_entry in record.get("mdr_text") or []:
            if (text_entry.get("text_type_code") or "").strip().lower() == "description of event or problem":
                narrative = text_entry.get("text") or ""
                break
        if not narrative:
            narrative = ", ".join(record.get("product_problems") or [])
        if not narrative:
            continue  # nothing to actually summarize — skip rather than ship an empty story

        items.append(normalize_item(
            source_key="fda_maude",
            title=title,
            url=f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfmaude/detail.cfm?mdrfoi__id={report_key}",
            published_date=published,
            summary=narrative,
            id_hint=f"mdr:{report_key}",
        ))
    return items


def fetch_fda_maude(query: str, days: int = 30, max_results: int = 20) -> list[dict]:
    since = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    today = date.today().strftime("%Y%m%d")
    params = {
        "search": f"({query}) AND date_received:[{since} TO {today}]",
        "sort": "date_received:desc",
        "limit": str(max_results),
    }
    url = f"https://api.fda.gov/device/event.json?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return parse_fda_maude_json(payload)
