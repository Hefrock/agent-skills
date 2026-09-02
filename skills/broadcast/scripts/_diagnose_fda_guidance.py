#!/usr/bin/env python3
"""TEMP diagnostic, round 6 (final) — round 5 found the real data source
inside the aggregated JS bundle's DataTables init call:
ajax:{"url":"/files/api/datatables/static/search-for-guidance.json",
"dataSrc":"","cache":true}. Confirms that URL works and inspects the
real record shape before writing the actual parser. Deleted before the
real PR is finalized."""
import json
import urllib.request

_UA = "Mozilla/5.0 (compatible; healthcare-ai-briefing/0.1; +https://github.com/Hefrock/agent-skills)"


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            return resp.status, resp.headers.get("Content-Type", ""), body
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}".encode()


url = "https://www.fda.gov/files/api/datatables/static/search-for-guidance.json"
status, ctype, body = _get(url)
print(f"status={status} content-type={ctype!r} length={len(body)}")
if status == 200:
    data = json.loads(body)
    print(f"type={type(data)}")
    if isinstance(data, list):
        print(f"count={len(data)}")
        print("first record keys:", list(data[0].keys()) if data else None)
        print("first 3 records:")
        for rec in data[:3]:
            print(json.dumps(rec, indent=2)[:1500])
            print("---")
    else:
        print(json.dumps(data, indent=2)[:2000])
else:
    print(body[:500])
