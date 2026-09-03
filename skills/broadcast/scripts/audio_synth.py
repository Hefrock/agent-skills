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
TTS. synthesize_text() now retries transient failures via gemini_retry.py
(429s and timeouts specifically, honoring the server's own Retry-After
header when present) rather than assuming a single call is always
representative of the API's real behavior under a realistic call volume
— see gemini_retry.py's own docstring for the full live-testing
narrative, including why blind retries made things WORSE on a second
run before that module got its Retry-After handling.

stdlib only, matching this repo's other reference tooling."""

import array
import base64
import io
import json
import os
import sys
import time
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gemini_retry  # noqa: E402

DEFAULT_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_VOICE = "Kore"
PCM_SAMPLE_RATE = 24000
PCM_SAMPLE_WIDTH = 2  # bytes per sample (16-bit)
PCM_CHANNELS = 1  # mono — see module docstring; not directly observed in the live response

# Audio quality (concatenation-time) defaults — see concatenate_wav_clips()'s
# own docstring for why these are opt-in there but orchestrate.py's real
# pipeline turns them on by default.
DEFAULT_INTER_SEGMENT_SILENCE_MS = 400.0
DEFAULT_NORMALIZE_TARGET_PEAK_RATIO = 0.9  # fraction of int16 full scale (32767)


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


def _silence_frames(duration_ms: float, sample_rate: int, channels: int, sample_width: int) -> bytes:
    """Raw zero-valued PCM frames spanning duration_ms — true silence
    (PCM 0 is silence for signed samples regardless of sample_width), not
    just low volume. Pure, no I/O. Returns b"" for duration_ms <= 0."""
    if duration_ms <= 0:
        return b""
    n_frames = round(sample_rate * duration_ms / 1000.0)
    return b"\x00" * (n_frames * channels * sample_width)


def _normalize_pcm_peak_int16(frames: bytes, target_peak_ratio: float = DEFAULT_NORMALIZE_TARGET_PEAK_RATIO) -> bytes:
    """Scales 16-bit signed PCM samples so the clip's peak amplitude
    reaches target_peak_ratio of full scale (32767), without clipping.
    Segments are synthesized by separate Gemini TTS calls and can come
    back at inconsistent loudness; normalizing each clip's peak before
    concatenation keeps segment-to-segment volume roughly consistent
    instead of leaving that to chance across independent API calls.

    A silent/near-silent clip (peak 0) is returned byte-for-byte
    unchanged rather than divided by zero or amplified into noise —
    there's nothing meaningful to normalize toward. Every scaled sample
    is clamped to the valid int16 range as a safety margin against
    floating-point rounding pushing a sample one step past full scale.

    Uses the stdlib `array` module (not the deprecated, Python 3.13-
    removed `audioop`) so this stays usable on whatever Python version
    this project moves to next, not just the 3.12 it's pinned to today."""
    if not frames:
        return frames
    samples = array.array("h")
    samples.frombytes(frames)
    if sys.byteorder == "big":
        samples.byteswap()  # WAV PCM is little-endian; array uses native order

    peak = max((abs(s) for s in samples), default=0)
    if peak == 0:
        return frames

    target_peak = int(32767 * target_peak_ratio)
    scale = target_peak / peak
    for i in range(len(samples)):
        samples[i] = max(-32768, min(32767, round(samples[i] * scale)))

    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def concatenate_wav_clips(wav_clips: list[bytes], normalize: bool = False, inter_segment_silence_ms: float = 0.0) -> bytes:
    """Joins several WAV clips (each a full, self-contained WAV file, e.g.
    from synthesize_text()) into one WAV file with the audio
    back-to-back. Plain byte concatenation of WAV files does NOT work —
    each file's own RIFF header would end up interspersed mid-stream —
    so this reads real PCM frames out of each clip via the stdlib `wave`
    module and re-wraps them once. Raises ValueError if given zero clips,
    or if the clips don't all share the same channel count/sample width/
    frame rate (concatenating audio with different formats silently
    produces garbage, so this fails loudly instead).

    normalize and inter_segment_silence_ms both default to their neutral,
    off values (False / 0.0) — this function stays a raw, unopinionated
    concatenation primitive so its own tests keep describing exactly what
    "concatenate" means at the byte level. orchestrate.py's real pipeline
    (assemble_episode_audio() called from run_episode()) turns both on by
    default for actual episodes — the same "primitive stays neutral,
    orchestration layer opts in" split this codebase already uses for
    synth_delay_seconds.

    normalize peak-normalizes each clip independently (see
    _normalize_pcm_peak_int16()) before joining — only defined for 16-bit
    PCM; raises ValueError if sample_width isn't 2 and normalize=True,
    rather than silently doing nothing to unsupported audio.

    inter_segment_silence_ms inserts that many milliseconds of true
    silence between each pair of clips — never before the first clip or
    after the last — via _silence_frames()."""
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

    if normalize:
        if sample_width != 2:
            raise ValueError(f"normalize=True only supports 16-bit PCM (sample_width=2), got sample_width={sample_width}")
        all_frames = [_normalize_pcm_peak_int16(f) for f in all_frames]

    if inter_segment_silence_ms > 0:
        silence = _silence_frames(inter_segment_silence_ms, frame_rate, channels, sample_width)
        joined_parts = [all_frames[0]]
        for f in all_frames[1:]:
            joined_parts.append(silence)
            joined_parts.append(f)
        joined = b"".join(joined_parts)
    else:
        joined = b"".join(all_frames)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(frame_rate)
        wf.writeframes(joined)
    return buf.getvalue()


def assemble_episode_audio(script: dict, segment_audio: list[bytes], normalize: bool = False, inter_segment_silence_ms: float = 0.0) -> dict:
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

    normalize/inter_segment_silence_ms default to the same neutral (False
    / 0.0) values concatenate_wav_clips() itself defaults to, and are
    passed straight through to it for full_episode_wav — see that
    function's docstring for what they do and why they default off here.
    Each segment's own "audio_wav" entry in the returned "segments" list
    is always the untouched clip exactly as synth_fn produced it,
    regardless of these params — only the assembled full_episode_wav is
    ever normalized or silence-padded, so per-segment audio is never
    silently altered underneath a caller that wants the original clips.

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

    full_episode_wav = concatenate_wav_clips(segment_audio, normalize=normalize, inter_segment_silence_ms=inter_segment_silence_ms)
    return {"full_episode_wav": full_episode_wav, "segments": paired}


# ── Network wrapper (not used by anything above) ────────────────────────

def synthesize_text(
    text: str,
    api_key: str,
    voice_name: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
    timeout: float = 30.0,
    max_attempts: int = 3,
    backoff_base_seconds: float = 5.0,
) -> bytes:
    """Calls the Gemini API's generateContent endpoint with
    responseModalities: ["AUDIO"] and returns a complete WAV file's
    bytes. The one function in this module that touches the network —
    kept separate so everything above can be unit-tested without it, same
    split as dedup_store.embed_text(). Requires outbound access to
    generativelanguage.googleapis.com.

    Retries up to max_attempts times, for errors gemini_retry.is_retryable()
    recognizes as transient only (see its docstring for why those two
    specific errors and not "any exception"). Each retry's wait comes
    from gemini_retry.retry_delay_seconds() — the server's own Retry-After
    header when a 429 response provides one, else backoff_base_seconds *
    2**attempt. max_attempts/backoff_base_seconds are deliberately
    conservative (kept low/high respectively, not tuned for speed): a
    live run of orchestrate.py that retried more aggressively on a fixed
    schedule made a real rate-limit situation WORSE, not better — every
    blind retry is itself one more request competing for the same
    exhausted quota. A caller synthesizing many segments in a loop (see
    orchestrate.py's run_episode()) still gets a real exception on the
    last attempt if every retry also fails — this doesn't hide failures,
    it just stops treating a transient rate limit as a permanent one on
    the first try."""
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
            if attempt == max_attempts - 1 or not gemini_retry.is_retryable(e):
                raise
            time.sleep(gemini_retry.retry_delay_seconds(e, attempt, backoff_base_seconds))
