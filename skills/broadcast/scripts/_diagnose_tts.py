#!/usr/bin/env python3
"""TEMP diagnostic script — live reconnaissance for Gemini TTS (Phase 7).
NOT part of the real pipeline; deleted before the real PR lands, same
pattern as every other adapter's reconnaissance script this session
(_diagnose_rss.py etc.).

Purpose: dedup_store.embed_text() is the only Gemini API call this
pipeline has ever actually exercised live. Audio synthesis needs the
generateContent endpoint with responseModalities: ["AUDIO"], which has
never been called — model name, exact request/response shape, and
whether the returned audio is raw PCM or a container format all need
confirming against the real API before writing audio_synth.py, same
discipline as fetch_medrxiv's "Nd" shorthand and FDA guidance's DataTables
JSON were confirmed live before being trusted.

Run manually (workflow_dispatch on the smoke-test workflow) with
GEMINI_API_KEY set."""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("GEMINI_API_KEY not set — skipping TTS diagnostic")
    sys.exit(0)

MODELS_TO_TRY = ["gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"]

for model in MODELS_TO_TRY:
    print(f"\n── Trying model: {model} ──────────────────────────────")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": "Say cheerfully: Have a wonderful day!"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
        },
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"  HTTPError {e.code}: {err_body[:2000]}")
        continue
    except Exception as e:
        print(f"  {type(e).__name__}: {e}")
        continue

    print("  Top-level keys:", list(body.keys()))
    candidates = body.get("candidates", [])
    print(f"  candidates: {len(candidates)}")
    if not candidates:
        print("  Full body:", json.dumps(body, indent=2)[:3000])
        continue

    parts = candidates[0].get("content", {}).get("parts", [])
    print(f"  parts: {len(parts)}")
    for i, part in enumerate(parts):
        print(f"    part[{i}] keys: {list(part.keys())}")
        inline = part.get("inlineData") or part.get("inline_data")
        if inline:
            mime = inline.get("mimeType") or inline.get("mime_type")
            data_b64 = inline.get("data", "")
            raw = base64.b64decode(data_b64) if data_b64 else b""
            print(f"    inlineData mimeType: {mime}")
            print(f"    base64 data length: {len(data_b64)} chars, decoded: {len(raw)} bytes")
            print(f"    first 16 raw bytes (hex): {raw[:16].hex()}")

    print("  usageMetadata:", body.get("usageMetadata"))
    break
else:
    print("\nAll models failed — see errors above")
    sys.exit(1)
