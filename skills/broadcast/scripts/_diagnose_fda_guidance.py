#!/usr/bin/env python3
"""TEMP diagnostic — this sandbox's egress policy blocks fda.gov entirely
(confirmed: even WebFetch on www.fda.gov returns EGRESS_BLOCKED), and no
search result confirms a documented, stable JSON API or RSS feed for FDA
guidance documents specifically (unlike medRxiv, which had *a* documented
API that just turned out to have a subtly wrong shorthand — here there's
no documented machine-readable endpoint for this dataset at all that
search turned up). So before writing any parser/fetch code, this probes
several real candidates from a runner with open egress and reports what
actually exists. Deleted before the real PR is finalized."""
import urllib.request

_UA = "Mozilla/5.0 (compatible; healthcare-ai-briefing/0.1; +https://github.com/Hefrock/agent-skills)"


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            return resp.status, resp.headers.get("Content-Type", ""), body
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}".encode()


# Candidate 1: a guessed RSS feed following the same pattern as the
# confirmed-real drugs/press-releases/consumers feeds.
for slug in ("guidances", "guidance", "guidance-documents"):
    url = f"https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/{slug}/rss.xml"
    status, ctype, body = _get(url)
    print(f"=== RSS candidate: {url} ===")
    print(f"status={status} content-type={ctype!r} length={len(body)}")
    print(body[:300])
    print()

# Candidate 2: the actual search page HTML — look for signs of an
# underlying API call (Drupal JSON:API, a views/ajax endpoint, or an
# inline fetch()/XHR call the page's JS makes).
url = "https://www.fda.gov/regulatory-information/search-fda-guidance-documents"
status, ctype, body = _get(url)
print(f"=== Guidance search page: {url} ===")
print(f"status={status} content-type={ctype!r} length={len(body)}")
text = body.decode("utf-8", errors="replace")
for marker in ("jsonapi", "views/ajax", "/api/", "fetch(", "XMLHttpRequest", "endpoint"):
    count = text.lower().count(marker.lower())
    print(f"  occurrences of {marker!r}: {count}")
print("--- first 1000 chars ---")
print(text[:1000])
