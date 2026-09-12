#!/usr/bin/env python3
"""Shared retry/backoff policy for this pipeline's sequential Gemini API
calls.

Originally built inside audio_synth.py's synthesize_text(), after a real
GitHub Actions run of orchestrate.py showed two things the hard way, not
hypothetically: (1) Gemini's TTS endpoint has a real rate limit that a
tight sequential loop of ~13 calls can trip (3 HTTP 429s, 4 read
timeouts, in the first live run that ever exercised the full pipeline
end to end), and (2) retrying blindly on a fixed backoff schedule can
make that WORSE, not better — a second live run, now retrying more
aggressively, saw MORE segments fail than the first, because every
retry is itself another request competing for the same already-tight
quota. What actually works: honoring the server's own Retry-After
header on a 429 when it provides one (it knows its real rate-limit
window; a client-side guess doesn't), falling back to a deliberately
conservative exponential backoff only when there's no Retry-After to
read.

Promoted out of audio_synth.py into its own module once narrate.py
(Phase 5.5) needed the exact same policy for its own sequential
per-segment Gemini calls — this project has hit real bugs before from
duplicating logic that then drifts apart (see
evidence_pinning_client.py's docstring for the precedent), so this is
the one place either caller imports from, not two independent copies.

stdlib only."""

import urllib.error


def is_retryable(exc: Exception) -> bool:
    """True for a 429 (rate limited) or a read timeout — both confirmed
    live against Gemini's API, not hypothetical (see this module's
    docstring). Anything else (malformed request, auth failure,
    unexpected response shape) is NOT retried — retrying those just
    wastes time before failing the same way again, since the problem
    isn't transient."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return True
    if isinstance(exc, TimeoutError):
        return True
    return False


def retry_delay_seconds(exc: Exception, attempt: int, backoff_base_seconds: float) -> float:
    """Prefers the server's own Retry-After header (seconds) on a 429
    response, when present, over a guessed backoff schedule. Falls back
    to exponential backoff (backoff_base_seconds * 2**attempt) when
    there's no Retry-After header (or the exception isn't an HTTPError
    at all — e.g. a timeout). See this module's docstring for why
    blindly retrying without this cost more than it helped on a real
    run."""
    if isinstance(exc, urllib.error.HTTPError) and exc.headers is not None:
        retry_after = exc.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
    return backoff_base_seconds * (2 ** attempt)
