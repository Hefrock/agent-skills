#!/usr/bin/env python3
"""Tests for gemini_retry.py — pure logic, no network. This is the
canonical test coverage for is_retryable()/retry_delay_seconds(); see
audio_synth.py's own docstring for why this policy exists and how it was
derived from two real live GitHub Actions runs, not designed
speculatively.

Run: python test_gemini_retry.py"""

import importlib.util
import os
import unittest
import urllib.error
from email.message import Message

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "gemini_retry.py")

spec = importlib.util.spec_from_file_location("gemini_retry", SCRIPT)
gemini_retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gemini_retry)


def make_http_error(code, retry_after=None):
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(url="x", code=code, msg="err", hdrs=hdrs, fp=None)


class IsRetryable(unittest.TestCase):
    def test_http_429_is_retryable(self):
        exc = urllib.error.HTTPError(url="x", code=429, msg="Too Many Requests", hdrs=None, fp=None)
        self.assertTrue(gemini_retry.is_retryable(exc))

    def test_http_500_is_not_retryable(self):
        exc = urllib.error.HTTPError(url="x", code=500, msg="Internal Server Error", hdrs=None, fp=None)
        self.assertFalse(gemini_retry.is_retryable(exc))

    def test_http_400_is_not_retryable(self):
        # A malformed request retrying won't fix itself by waiting.
        exc = urllib.error.HTTPError(url="x", code=400, msg="Bad Request", hdrs=None, fp=None)
        self.assertFalse(gemini_retry.is_retryable(exc))

    def test_timeout_error_is_retryable(self):
        # The exact exception type real synthesize_text() calls hit live
        # (confirmed via orchestrate.py's first real end-to-end run).
        self.assertTrue(gemini_retry.is_retryable(TimeoutError("The read operation timed out")))

    def test_unrelated_exception_is_not_retryable(self):
        self.assertFalse(gemini_retry.is_retryable(KeyError("candidates")))
        self.assertFalse(gemini_retry.is_retryable(ValueError("bad json")))


class RetryDelaySeconds(unittest.TestCase):
    def test_honors_a_numeric_retry_after_header(self):
        exc = make_http_error(429, retry_after=12)
        self.assertEqual(gemini_retry.retry_delay_seconds(exc, attempt=0, backoff_base_seconds=5.0), 12.0)

    def test_retry_after_wins_regardless_of_attempt_number(self):
        # Not an exponential schedule — the server's stated wait doesn't
        # grow just because this is a later attempt.
        exc = make_http_error(429, retry_after=7)
        self.assertEqual(gemini_retry.retry_delay_seconds(exc, attempt=2, backoff_base_seconds=5.0), 7.0)

    def test_falls_back_to_exponential_backoff_when_no_retry_after_header(self):
        exc = make_http_error(429, retry_after=None)
        self.assertEqual(gemini_retry.retry_delay_seconds(exc, attempt=1, backoff_base_seconds=5.0), 10.0)

    def test_falls_back_to_exponential_backoff_on_a_malformed_retry_after_value(self):
        exc = make_http_error(429, retry_after="not-a-number")
        self.assertEqual(gemini_retry.retry_delay_seconds(exc, attempt=0, backoff_base_seconds=5.0), 5.0)

    def test_falls_back_to_exponential_backoff_for_a_non_http_error(self):
        exc = TimeoutError("The read operation timed out")
        self.assertEqual(gemini_retry.retry_delay_seconds(exc, attempt=2, backoff_base_seconds=5.0), 20.0)


if __name__ == "__main__":
    unittest.main()
