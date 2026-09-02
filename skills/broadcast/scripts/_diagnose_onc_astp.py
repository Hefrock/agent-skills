#!/usr/bin/env python3
"""TEMP diagnostic round 2 — round 1 confirmed /buzz-blog/feed is a real,
working RSS 2.0 feed (84626 bytes). This dumps the first <item> block and
runs the EXISTING parse_rss_xml/fetch_rss against it live, to confirm the
already-built generic RSS parser (used for STAT News/Fierce Healthcare)
handles this feed correctly with zero new adapter code. Deleted before
the real PR is finalized."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest  # noqa: E402

items = ingest.fetch_rss("https://www.healthit.gov/buzz-blog/feed", "onc_astp")
print(f"parsed {len(items)} items via the existing generic RSS parser")
if items:
    import json
    print(json.dumps(items[0], indent=2))
    print(json.dumps(items[1], indent=2) if len(items) > 1 else "")
