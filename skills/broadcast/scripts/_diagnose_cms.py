#!/usr/bin/env python3
"""TEMP diagnostic — CMS.gov has an official "Newsroom Feeds" page
(cms.gov/newsroom/rss-feeds) listing RSS feed URLs, per WebSearch. Fetches
that page to extract the real feed URL(s) rather than guessing at a slug
(FDA guidance's four 404s taught that lesson). Deleted before the real PR
is finalized."""
import re
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


url = "https://www.cms.gov/newsroom/rss-feeds"
status, ctype, body = _get(url)
print(f"=== {url} ===")
print(f"status={status} content-type={ctype!r} length={len(body)}")
text = body.decode("utf-8", errors="replace") if status == 200 else ""
if text:
    # Look for any href containing "rss", "feed", or ending .xml
    hrefs = re.findall(r'href="([^"]+)"', text)
    candidates = [h for h in hrefs if any(k in h.lower() for k in ("rss", "feed", ".xml"))]
    print(f"candidate hrefs ({len(candidates)}):")
    for c in sorted(set(candidates)):
        print(f"  {c}")
else:
    print(body[:500])
