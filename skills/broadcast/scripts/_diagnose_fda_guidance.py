#!/usr/bin/env python3
"""TEMP diagnostic, round 4 — round 3 confirmed only the generic
DataTables.js library files are loaded (no custom AJAX-init script found
among external <script src> files). This means the DataTable is likely
initialized inline with either a JS data array/object passed directly, or
reads a hidden JSON blob elsewhere on the page. Searches inline <script>
content and any application/json blocks for the real data source.
Deleted before the real PR is finalized."""
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


url = "https://www.fda.gov/regulatory-information/search-fda-guidance-documents"
status, ctype, body = _get(url)
text = body.decode("utf-8", errors="replace")
print(f"page length={len(text)}")

idx = text.find("DataTable(")
print(f"'DataTable(' index: {idx}")
if idx >= 0:
    print(text[max(0, idx - 500):idx + 2500])
else:
    idx2 = text.find(".dataTable")
    print(f"'.dataTable' index: {idx2}")
    if idx2 >= 0:
        print(text[max(0, idx2 - 500):idx2 + 2500])

print()
print("=== application/json script blocks ===")
for m in re.finditer(r'<script[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>', text, re.DOTALL):
    print(m.group(0)[:500])
    print("---")

print()
print("=== all inline <script>...</script> blocks (no src), lengths ===")
inline_scripts = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', text, re.DOTALL)
for i, s in enumerate(inline_scripts):
    print(f"  [{i}] length={len(s)} preview={s[:120]!r}")
