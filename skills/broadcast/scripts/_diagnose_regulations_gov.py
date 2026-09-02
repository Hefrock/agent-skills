#!/usr/bin/env python3
"""TEMP diagnostic — regulations.gov's v4 /documents API is documented
(open.gsa.gov/api/regulationsgov/), unlike FDA guidance, but documented
behavior has been wrong before (medRxiv's "Nd" shorthand). Confirms
DEMO_KEY actually works, the real response shape, and real field names
before writing the parser. Deleted before the real PR is finalized."""
import json
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (compatible; healthcare-ai-briefing/0.1; +https://github.com/Hefrock/agent-skills)"


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}".encode()


params = {
    "filter[searchTerm]": "artificial intelligence",
    "filter[postedDate][ge]": "2026-08-01",
    "sort": "-postedDate",
    "page[size]": "5",
}
url = f"https://api.regulations.gov/v4/documents?{urllib.parse.urlencode(params)}"
status, headers, body = _get(url, headers={"User-Agent": _UA, "X-Api-Key": "DEMO_KEY"})
print(f"status={status} length={len(body)}")
if headers:
    for k in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "Content-Type"):
        print(f"  header {k}: {headers.get(k)}")
if status == 200:
    data = json.loads(body)
    print(f"top-level keys: {list(data.keys())}")
    docs = data.get("data", [])
    print(f"count={len(docs)}")
    for d in docs[:3]:
        print(json.dumps(d, indent=2)[:1500])
        print("---")
    print("meta:", json.dumps(data.get("meta", {}), indent=2)[:500])
else:
    print(body[:800])
