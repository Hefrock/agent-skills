#!/usr/bin/env python3
"""TEMP diagnostic script — live reconnaissance for Gemini structured
text generation (narrate.py / Phase 5.5). NOT part of the real pipeline;
deleted before the real PR lands, same pattern as every other adapter's
reconnaissance script this project has used (_diagnose_tts.py etc.).

Purpose: this pipeline has only ever called Gemini's embedContent
(dedup_store.embed_text) and generateContent with responseModalities:
["AUDIO"] (audio_synth.synthesize_text). narrate.py needs plain TEXT
generateContent with structured JSON output (responseMimeType:
"application/json" + a responseSchema) so a caller gets back
{"narration": ..., "supporting_spans": [...]} without needing to parse
free-form prose out of a model response. Confirming that request/
response shape live — and that responseSchema is honored, not just
requested — before writing the real function, same discipline as every
other endpoint this pipeline has ever called.

Run manually (workflow_dispatch on the smoke-test workflow) with
GEMINI_API_KEY set. Deliberately ONE generateContent call only — this
project's last live-reconnaissance round for a new Gemini capability
(TTS) went through several rounds of retriggering and ended up
exhausting a shared quota; this script is written to get everything
needed from a single generation request.

Round 3 addition: gemini-3.6-flash returned two consecutive HTTP 503s
("high demand") on rounds 1-2, after gemini-2.5-flash's round-1 404
pointed to it as the replacement. Before spending a third generation
attempt against a possibly-overloaded model, this round first calls
ListModels — a cheap, non-generative metadata read that doesn't compete
for the same generation capacity — to get authoritative confirmation of
which model IDs actually exist and are usable right now, rather than
guessing from error-message text alone. ListModels confirmed
gemini-3.6-flash is real, but the generateContent call against it then
hit a 30s client-side read timeout — consistent with sustained overload,
not a wrong model id.

Round 4: rather than keep retrying the specific dated model that's
struggling, switch to gemini-flash-latest — an alias ListModels also
returned, which Google documents as always pointing at their current
recommended flash model. This sidesteps the deprecation-churn problem
this whole reconnaissance has been chasing (2.5-flash deprecated,
3.6-flash's specific dated id possibly still ramping up capacity) by
never hard-coding a dated id in the first place. If this pans out,
narrate.py's DEFAULT_TEXT_MODEL should use this alias too."""

import json
import os
import sys
import urllib.error
import urllib.request

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("GEMINI_API_KEY not set — skipping narration diagnostic")
    sys.exit(0)

list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
try:
    with urllib.request.urlopen(list_url, timeout=30) as resp:
        models_body = json.loads(resp.read().decode("utf-8"))
    flash_like = [
        m["name"] for m in models_body.get("models", [])
        if "flash" in m.get("name", "").lower()
        and "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    print("Models supporting generateContent with 'flash' in the name:")
    for name in flash_like:
        print(f"  {name}")
except urllib.error.HTTPError as e:
    print(f"ListModels HTTPError {e.code}: {e.read().decode('utf-8', errors='replace')[:1000]}")

MODEL = "gemini-flash-latest"
url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={api_key}"

source_text = (
    "FDA Approves New AI Diagnostic Tool. The guidance may apply to premarket "
    "notification requirements for AI-based diagnostic software. Source: FDA guidance."
)

prompt = (
    "Rewrite the following healthcare AI news item as 1-2 sentences of natural, "
    "conversational spoken narration for a podcast host introducing this story. "
    "Use ONLY the facts stated in the source text below — do not add any information, "
    "names, numbers, or claims not present in it. Preserve any hedging or attribution "
    "language (e.g. 'may', 'could', 'according to') if present — do not state a hedged "
    "claim as certain fact.\n\n"
    f"Source text: {source_text}"
)

payload = {
    "contents": [{"parts": [{"text": prompt}]}],
    "generationConfig": {
        "responseMimeType": "application/json",
        "responseSchema": {
            "type": "OBJECT",
            "properties": {
                "narration": {"type": "STRING"},
                "supporting_spans": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": ["narration", "supporting_spans"],
        },
    },
}

req = urllib.request.Request(
    url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print(f"HTTPError {e.code}: {e.read().decode('utf-8', errors='replace')[:2000]}")
    sys.exit(1)

print("Top-level keys:", list(body.keys()))
candidates = body.get("candidates", [])
print(f"candidates: {len(candidates)}")
if not candidates:
    print("Full body:", json.dumps(body, indent=2)[:3000])
    sys.exit(1)

parts = candidates[0].get("content", {}).get("parts", [])
print(f"parts: {len(parts)}")
for i, part in enumerate(parts):
    print(f"  part[{i}] keys: {list(part.keys())}")
    text = part.get("text")
    if text is not None:
        print(f"  raw text: {text!r}")
        try:
            parsed = json.loads(text)
            print(f"  parsed JSON: {json.dumps(parsed, indent=2)}")
            print(f"  has 'narration' key: {'narration' in parsed}")
            print(f"  has 'supporting_spans' key: {'supporting_spans' in parsed}")
        except json.JSONDecodeError as e:
            print(f"  FAILED to parse as JSON: {e}")

print("finishReason:", candidates[0].get("finishReason"))
print("usageMetadata:", body.get("usageMetadata"))
