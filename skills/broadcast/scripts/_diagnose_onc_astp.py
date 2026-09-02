#!/usr/bin/env python3
"""TEMP diagnostic — ASTP/ONC (healthit.gov) publishes via a WordPress-style
blog. WebSearch surfaced "healthit.gov/buzz-blog" (Health IT Buzz Blog) and
the main "healthit.gov/blog" — neither confirmed with an exact feed URL.
Guessed RSS slugs have been wrong before (FDA guidance's four 404s), so
this probes real candidates from a runner with open egress before writing
any parser/fetch code. Deleted before the real PR is finalized."""
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


candidates = [
    "https://www.healthit.gov/buzz-blog/feed",
    "https://www.healthit.gov/buzz-blog/feed/",
    "https://www.healthit.gov/buzz-blog/rss.xml",
    "https://healthit.gov/buzz-blog/feed",
    "https://www.healthit.gov/blog/feed",
    "https://www.healthit.gov/blog/feed/",
    "https://www.healthit.gov/feed",
    "https://www.healthit.gov/feed/",
]
for url in candidates:
    status, ctype, body = _get(url)
    print(f"=== {url} ===")
    print(f"status={status} content-type={ctype!r} length={len(body)}")
    print(body[:300])
    print()
