#!/usr/bin/env python3
"""TEMP diagnostic, round 3 — round 2 found the guidance table
(class="lcds-datatable--sfgd", Drupal view "fda_guidance_documents",
display "block_11") ships with an EMPTY <tbody> in the raw HTML: it's
populated client-side after page load. No inline AJAX URL was found in
the page's own markup. This lists every <script src=...> on the page to
find the JS bundle that drives the datatable, then fetches it looking for
the actual AJAX endpoint URL/data-table config. Deleted before the real
PR is finalized."""
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

srcs = re.findall(r'<script[^>]+src=["\']?([^"\'\s>]+)', text)
print(f"total <script src> tags: {len(srcs)}")
candidates = [s for s in srcs if any(k in s.lower() for k in ("guidance", "sfgd", "datatable", "views", "search"))]
print("candidates matching guidance/sfgd/datatable/views/search:")
for c in candidates:
    print(f"  {c}")
print()
print("all script srcs (first 40):")
for s in srcs[:40]:
    print(f"  {s}")

# Also: does the page reference a data-table-ajax attribute or similar on
# the table/view div itself (sometimes config is in a data-* attribute)?
table_idx = text.find('lcds-datatable--sfgd')
print()
print("--- 400 chars before the table class (to catch a data-* config attr) ---")
print(text[max(0, table_idx - 800):table_idx + 200])
