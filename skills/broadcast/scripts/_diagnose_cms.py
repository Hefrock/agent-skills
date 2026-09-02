#!/usr/bin/env python3
"""TEMP diagnostic round 4 — rounds 2-3 found two real bugs in the
existing generic RSS parser against CMS's real feed (corrupted <link>
field containing a URL-encoded copy of the title's markup, and a third
undocumented pubDate format). Both are now fixed in ingest.py. This
re-runs fetch_rss live against the real feed to confirm the fix actually
works end to end, not just against a local fixture. Deleted before the
real PR is finalized."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest  # noqa: E402

items = ingest.fetch_rss("https://www.cms.gov/newsroom/rss-feeds", "cms")
print(f"parsed {len(items)} items via the now-fixed generic RSS parser")
for item in items[:3]:
    print(json.dumps(item, indent=2))
