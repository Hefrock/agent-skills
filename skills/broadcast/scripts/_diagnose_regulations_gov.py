#!/usr/bin/env python3
"""TEMP diagnostic round 2 — round 1 confirmed DEMO_KEY works and the
real /documents response shape (JSON:API: data[].id/type/attributes, no
abstract/summary field). This confirms the human-facing document URL
pattern (regulations.gov/document/{id}) actually resolves, and checks
meta.totalElements/pagination fields without truncation. Deleted before
the real PR is finalized."""
import json
import urllib.error
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (compatible; healthcare-ai-briefing/0.1; +https://github.com/Hefrock/agent-skills)"


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, f"{type(e).__name__}: {e}".encode()


# A query closer to what this adapter will actually use in production.
params = {
    "filter[searchTerm]": "clinical decision support software",
    "filter[postedDate][ge]": "2025-01-01",
    "sort": "-postedDate",
    "page[size]": "5",
}
url = f"https://api.regulations.gov/v4/documents?{urllib.parse.urlencode(params)}"
status, body = _get(url, headers={"User-Agent": _UA, "X-Api-Key": "DEMO_KEY"})
print(f"status={status} length={len(body)}")
data = json.loads(body) if status == 200 else None
if data:
    docs = data.get("data", [])
    print(f"count={len(docs)}")
    print("meta (full):", json.dumps(data.get("meta", {}), indent=2))
    if docs:
        doc_id = docs[0]["id"]
        print(f"\nfirst doc id: {doc_id}")
        doc_url = f"https://www.regulations.gov/document/{doc_id}"
        s2, b2 = _get(doc_url)
        print(f"document page {doc_url} -> status={s2} length={len(b2)}")
else:
    print(body[:500])
