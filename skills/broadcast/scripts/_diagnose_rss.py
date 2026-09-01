#!/usr/bin/env python3
"""Throwaway diagnostic — NOT part of the module, will be deleted before
this branch merges. Prints raw response info for the two still-failing RSS
feed_url values so the actual fix can be based on real data instead of
another guess."""
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingest  # noqa: E402

FULLER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.healthcareitnews.com/",
}

for url in [
    "https://www.healthcareitnews.com/home/feed",
]:
    print(f"=== {url} (fuller browser-like headers) ===")
    try:
        req = urllib.request.Request(url, headers=FULLER_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type")
            final_url = resp.url
            body = resp.read().decode("utf-8", errors="replace")
        print(f"status={status} content-type={content_type} final_url={final_url}")
        print(f"body length={len(body)}")
        first_item = re.search(r"<item>.*?</item>", body, re.DOTALL)
        if first_item:
            print("FULL first <item> block:")
            print(first_item.group(0))
        else:
            print("no <item> block found; first 500 chars:")
            print(body[:500])
        print(f"contains '<item': {'<item' in body}  contains '<entry': {'<entry' in body}  contains '<rss': {'<rss' in body}")
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")
    print()
