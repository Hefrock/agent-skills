#!/usr/bin/env python3
"""TEMP diagnostic — prints the raw medRxiv API response shape. Deleted
before this PR is finalized, same as the earlier fierce_healthcare
diagnostic script."""
import json
import urllib.request

for interval in ("7d", "14d", "5", "2026-08-15/2026-09-01"):
    url = f"https://api.medrxiv.org/details/medrxiv/{interval}/0/json"
    print(f"=== interval={interval!r} url={url} ===")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
        print(f"HTTP {status}, body length {len(body)}")
        print(body[:1500])
    except Exception as e:
        print(f"{type(e).__name__}: {e}")
    print()
