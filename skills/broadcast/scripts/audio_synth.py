#!/usr/bin/env python3
"""Audio synthesis (Phase 7) — turns a QA-gated script_gen.py script into
spoken audio via the Gemini API's text-to-speech capability.

Same split as dedup_store.py's embed_text(): synthesize_text() is the one
function here that touches the network, kept deliberately thin so
everything else (WAV framing, clip concatenation, pairing synthesized
audio back up with its script segment) is pure and unit-testable without
it. assemble_episode_audio() takes already-synthesized audio as a plain
argument, one clip per script segment in order — the same
items/embeddings-are-computed-elsewhere-and-passed-in convention
rank.py's rank_stories() already uses for embeddings, not a coincidence:
it's this pipeline's standing pattern for "batch orchestration over a
per-item network call nobody has actually made yet." Same reasoning as
rank_stories: a driver that hasn't been built yet (Phase 8's
orchestration) is expected to call synthesize_text() once per segment,
collect the results, and hand them to assemble_episode_audio().

Endpoint shape confirmed live before writing this (never trust a
"documented" shape without checking — see ingest.py's medRxiv "Nd"
shorthand for why): models/gemini-2.5-flash-preview-tts's generateContent,
called with generationConfig.responseModalities: ["AUDIO"] and a
speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName, returns
candidates[0].content.parts[0].inlineData as {"mimeType":
"audio/L16;codec=pcm;rate=24000", "data": <base64>} — raw headerless PCM,
not a self-describing container, so this module wraps it in a real WAV
header (via the stdlib `wave` module, not hand-rolled RIFF math) before
handing bytes back to any caller. Channel count (mono, PCM_CHANNELS
below) is NOT present in that mimeType string — it isn't part of what was
live-confirmed, only inferred from Google's consistently-documented
Gemini TTS output format (repeated across every official code sample,
including the reference google-genai SDK's own WAV-wrapping helper,
which also assumes mono). Flagged here as a documented-but-not-directly-
observed assumption, worth revisiting if a real listen-through ever
sounds pitch- or speed-wrong.

Requires outbound access to generativelanguage.googleapis.com, same
constraint as embed_text(); will fail under a default-deny egress policy.

Real rate limit confirmed live (not hypothetical) via orchestrate.py's
first real end-to-end GitHub Actions run: synthesizing 13 segments in a
tight sequential loop got 3 HTTP 429s and 4 read timeouts from Gemini
TTS. synthesize_text() now retries transient failures (429s and
timeouts specifically — see _is_retryable()) with exponential backoff
rather than assuming a single call is always representative of the
API's real behavior under a realistic call volume.

stdlib only, matching this repo's other reference tooling."""

import base64
import io
import json
import time
import urllib.error
import urllib.request
import wave

DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_VOICE = "Kore"
PCM_SAMPLE_RATE = 24000
PCM_SAMPLE_WIDTH = 2  # bytes per sample (16-bit)
PCM_CHANNELS = 1  # mono — see module docstring; not directly observed in the live response


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = PCM_SAMPLE_RATE, channels: int = PCM_CHANNELS, sample_width: int = PCM_SAMPLE_WIDTH) -> bytes:
    """Wraps raw headerless PCM samples in a standard WAV/RIFF container.
    Pure — no network, no I/O beyond the in-memory buffer."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def concatenate_wav_clips(wav_clips: list[bytes]) -> bytes:
    """Joins several WAV clips (each a full, self-contained WAV file, e.g.
    from synthesize_text()) into one WAV file with the audio
    back-to-back. Plain byte concatenation of WAV files does NOT work —
    each file's own RIFF header would end up interspersed mid-stream —
    so this reads real PCM frames out of each clip via the stdlib `wave`
    module and re-wraps them once. Raises ValueError if given zero clips,
    or if the clips don't all share the same channel count/sample width/
    frame rate (concatenating audio with different formats silently
    produces garbage, so this fails loudly instead)."""
    if not wav_clips:
        raise ValueError("concatenate_wav_clips requires at least one clip")

    params = None
    all_frames = []
    for clip in wav_clips:
        with wave.open(io.BytesIO(clip), "rb") as wf:
            p = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
            if params is None:
                params = p
            elif p != params:
                raise ValueError(f"all clips must share the same (channels, sample_width, frame_rate); got {params} and {p}")
            all_frames.append(wf.readframes(wf.getnframes()))

    channels, sample_width, frame_rate = params
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(frame_rate)
        wf.writeframes(b"".join(all_frames))
    return buf.getvalue()


def assemble_episode_audio(script: dict, segment_audio: list[bytes]) -> dict:
    """script is script_gen.generate_script()'s return value.
    segment_audio[i] must be the already-synthesized WAV clip (e.g. from
    synthesize_text()) for script["segments"][i] — this function never
    calls synthesize_text() itself, same convention as rank_stories()
    never calling embed_text() itself.

    Returns:
      {
        "full_episode_wav": bytes,   # every segment's audio, concatenated in order
        "segments": [
          {"segment_type", "canonical_id", "claim_id", "source_id", "audio_wav"},
          ...
        ],
      }

    Raises ValueError if script["segments"] and segment_audio aren't the
    same length — fail loudly here rather than silently pairing segment N
    with clip N+1 three lines later, same discipline as rank_stories()'s
    items/embeddings length check."""
    segments = script["segments"]
    if len(segments) != len(segment_audio):
        raise ValueError(f"script segments and segment_audio must be the same length, got {len(segments)} and {len(segment_audio)}")

    paired = [
        {
            "segment_type": segment["segment_type"],
            "canonical_id": segment["canonical_id"],
            "claim_id": segment["claim_id"],
            "source_id": segment["source_id"],
            "audio_wav": audio,
        }
        for segment, audio in zip(segments, segment_audio)
    ]

    return {"full_episode_wav": concatenate_wav_clips(segment_audio), "segments": paired}


def _is_retryable(exc: Exception) -> bool:
    """True for a 429 (rate limited) or a read timeout — both confirmed
    live, not hypothetical: a real GitHub Actions run of orchestrate.py
    (13 sequential synthesize_text() calls, one per script segment) hit
    Gemini TTS's real rate limit under that burst — 3 calls got HTTP 429,
    4 more timed out (almost certainly the same underlying throttling,
    not a separate problem) — and both are worth one retry rather than
    losing that segment's audio outright. Anything else (malformed
    request, auth failure, unexpected response shape) is NOT retried:
    retrying those would just waste time before failing the same way
    again, since the problem isn't transient."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return True
    if isinstance(exc, TimeoutError):
        return True
    return False


# ── Network wrapper (not used by anything above) ────────────────────────

def synthesize_text(
    text: str,
    api_key: str,
    voice_name: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    timeout: float = 30.0,
    max_attempts: int = 4,
    backoff_base_seconds: float = 2.0,
) -> bytes:
    """Calls the Gemini API's generateContent endpoint with
    responseModalities: ["AUDIO"] and returns a complete WAV file's
    bytes. The one function in this module that touches the network —
    kept separate so everything above can be unit-tested without it, same
    split as dedup_store.embed_text(). Requires outbound access to
    generativelanguage.googleapis.com.

    Retries up to max_attempts times, with exponential backoff
    (backoff_base_seconds * 2**attempt), but ONLY for errors
    _is_retryable() recognizes as transient (see its docstring for why
    those two specific errors and not "any exception" — this was added
    after a real rate-limit was hit live, not speculatively). A caller
    synthesizing many segments in a loop (see orchestrate.py's
    run_episode()) still gets a real exception on the last attempt if
    every retry also fails — this doesn't hide failures, it just stops
    treating a transient rate limit as a permanent one on the first try."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}},
        },
    }).encode("utf-8")

    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            inline_data = body["candidates"][0]["content"]["parts"][0]["inlineData"]
            pcm_bytes = base64.b64decode(inline_data["data"])
            return _pcm_to_wav(pcm_bytes)
        except Exception as e:
            if attempt == max_attempts - 1 or not _is_retryable(e):
                raise
            time.sleep(backoff_base_seconds * (2 ** attempt))
