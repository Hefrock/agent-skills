#!/usr/bin/env python3
"""TEMP diagnostic round 3 — round 2 found fetch_rss/parse_rss_xml
returns ZERO items against the real CMS feed despite it being valid RSS
XML (15815 bytes). Dumps the raw feed body to find out why: different
<item> tag naming, a namespace prefix, or a genuinely empty channel.
Deleted before the real PR is finalized."""
import urllib.request

_UA = "Mozilla/5.0 (compatible; healthcare-ai-briefing/0.1; +https://github.com/Hefrock/agent-skills)"

req = urllib.request.Request("https://www.cms.gov/newsroom/rss-feeds", headers={"User-Agent": _UA})
with urllib.request.urlopen(req, timeout=15) as resp:
    body = resp.read().decode("utf-8", errors="replace")

print(f"length={len(body)}")
print("--- first 3000 chars ---")
print(body[:3000])
print()
print(f"occurrences of '<item': {body.count('<item')}")
print(f"occurrences of '<entry': {body.count('<entry')}")
print(f"occurrences of '<title>': {body.count('<title>')}")
