#!/usr/bin/env python3
"""TEMP diagnostic, round 5 — round 4 found the page's Drupal node id
(node/360135) and confirmed the DataTable init call isn't inline; it must
be inside one of the four aggregated footer JS bundles
(/files/js/js_*.js?scope=footer). Fetches each and searches for the
DataTables config (ajax url, column defs) or any reference to
"guidance"/"sfgd"/360135. Deleted before the real PR is finalized."""
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

js_urls = sorted(set(re.findall(r'src="(/files/js/js_[^"]+)"', text)))
print(f"found {len(js_urls)} footer JS bundle URLs")

for rel in js_urls:
    full = "https://www.fda.gov" + rel.replace("&amp;", "&")
    status, ctype, jbody = _get(full)
    jtext = jbody.decode("utf-8", errors="replace")
    print(f"=== {full} ===")
    print(f"status={status} length={len(jtext)}")
    for marker in ("DataTable", "ajax", "sfgd", "360135", "guidance"):
        count = jtext.lower().count(marker.lower())
        print(f"  {marker!r}: {count}")
    idx = jtext.find("sfgd")
    if idx == -1:
        idx = jtext.lower().find("datatable(")
    if idx >= 0:
        print("  --- context ---")
        print("  " + jtext[max(0, idx - 300):idx + 1200].replace("\n", "\\n"))
    print()
