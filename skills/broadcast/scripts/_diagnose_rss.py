#!/usr/bin/env python3
"""Throwaway diagnostic — NOT part of the module, will be deleted before
this branch merges. Prints raw response info for the two still-failing RSS
feed_url values so the actual fix can be based on real data instead of
another guess."""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingest  # noqa: E402

for url in [
    "https://www.fiercehealthcare.com/rss/xml",
    "https://www.healthcareitnews.com/home/feed",
]:
    print(f"=== {url} ===")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ingest._USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type")
            final_url = resp.url
            body = resp.read().decode("utf-8", errors="replace")
        print(f"status={status} content-type={content_type} final_url={final_url}")
        print(f"body length={len(body)}")
        print("first 500 chars:")
        print(body[:500])
        print("...")
        print(f"contains '<item': {'<item' in body}  contains '<entry': {'<entry' in body}  contains '<rss': {'<rss' in body}")
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")
    print()
