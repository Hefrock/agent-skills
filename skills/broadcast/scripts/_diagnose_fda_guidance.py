#!/usr/bin/env python3
"""TEMP diagnostic, round 2 — round 1 found: no dedicated RSS feed (all
three guessed slugs 404), and the search page is plain server-rendered
HTML with no JS API markers. This round inspects the actual table/data
structure of that HTML page, and tests whether Drupal's Views JSON export
format works via ?_format=json, before deciding scrape-vs-API. Deleted
before the real PR is finalized."""
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


# Round 2a: does a Views JSON export exist?
for suffix in ("?_format=json", "?_format=hal_json"):
    url = f"https://www.fda.gov/regulatory-information/search-fda-guidance-documents{suffix}"
    status, ctype, body = _get(url)
    print(f"=== {url} ===")
    print(f"status={status} content-type={ctype!r} length={len(body)}")
    print(body[:400])
    print()

# Round 2b: inspect the real HTML table structure.
url = "https://www.fda.gov/regulatory-information/search-fda-guidance-documents"
status, ctype, body = _get(url)
text = body.decode("utf-8", errors="replace")
print(f"=== full page length={len(text)} ===")
for marker in ("<table", "views-table", "drupalSettings", "csv", "download", "<form", "action=\""):
    idx = text.lower().find(marker.lower())
    print(f"  first index of {marker!r}: {idx}")

table_idx = text.lower().find("<table")
if table_idx >= 0:
    print("--- 3000 chars starting at first <table ---")
    print(text[table_idx:table_idx + 3000])
else:
    print("--- no <table> found. Searching for 'guidance' near a list/grid structure ---")
    g_idx = text.lower().find("guidance-documents-search")
    print(f"  'guidance-documents-search' idx: {g_idx}")
    # dump a middle slice for manual inspection
    mid = len(text) // 2
    print(text[mid:mid + 2000])

form_idx = text.lower().find("<form")
if form_idx >= 0:
    print("--- 1500 chars starting at first <form (to see the search form's action/params) ---")
    print(text[form_idx:form_idx + 1500])
