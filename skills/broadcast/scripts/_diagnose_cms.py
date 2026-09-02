#!/usr/bin/env python3
"""TEMP diagnostic round 2 — round 1 found that
cms.gov/newsroom/rss-feeds ITSELF serves RSS XML directly
(content-type application/rss+xml, not an HTML links page). This runs
the existing fetch_rss/parse_rss_xml against it live to check whether it
needs zero new code, like onc_astp, or a pubDate-format fix like
fierce_healthcare. Deleted before the real PR is finalized."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest  # noqa: E402

items = ingest.fetch_rss("https://www.cms.gov/newsroom/rss-feeds", "cms")
print(f"parsed {len(items)} items via the existing generic RSS parser")
for item in items[:3]:
    print(json.dumps(item, indent=2))
