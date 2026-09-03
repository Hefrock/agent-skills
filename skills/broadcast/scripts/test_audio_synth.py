#!/usr/bin/env python3
"""Tests for audio_synth.py's pure logic (_pcm_to_wav, concatenate_wav_clips,
assemble_episode_audio) — no network. synthesize_text() itself is
deliberately NOT unit tested here, same treatment dedup_store.embed_text()
gets: it's a thin network wrapper, verified live via live_smoke_test.py
instead (see that file, and orchestrate.py's real GitHub Actions run, for
the real request/response/failure shapes this module's docstring
documents). Its retry policy (is_retryable()/retry_delay_seconds(), the
real 429/timeout rate-limit handling) now lives in gemini_retry.py and is
tested there — see test_gemini_retry.py.

Run: python test_audio_synth.py"""

import array
import importlib.util
import io
import os
import unittest
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "audio_synth.py")

spec = importlib.util.spec_from_file_location("audio_synth", SCRIPT)
audio_synth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio_synth)


def make_wav(num_frames, sample_rate=24000, channels=1, sample_width=2, fill_byte=b"\x01"):
    """A tiny synthetic WAV clip — real PCM frames of a fixed value, not
    silence, so concatenation order is checkable by inspecting bytes."""
    pcm = fill_byte * (num_frames * channels * sample_width)
    return audio_synth._pcm_to_wav(pcm, sample_rate=sample_rate, channels=channels, sample_width=sample_width)


def read_wav_params_and_frames(wav_bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        params = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
        frames = wf.readframes(wf.getnframes())
    return params, frames


def make_wav_from_int16_samples(samples, sample_rate=24000, channels=1):
    """Builds a real WAV clip from explicit int16 sample values — needed
    for normalization tests, where the actual numeric peak (not just a
    repeated fill byte) is what's being checked."""
    pcm = array.array("h", samples).tobytes()
    return audio_synth._pcm_to_wav(pcm, sample_rate=sample_rate, channels=channels, sample_width=2)


def read_int16_samples(wav_bytes):
    _, frames = read_wav_params_and_frames(wav_bytes)
    samples = array.array("h")
    samples.frombytes(frames)
    return list(samples)


def make_segment(segment_type="top_three_item", canonical_id="c1", claim_id="claim-1", source_id="src-1", text="Some text."):
    return {"segment_type": segment_type, "text": text, "canonical_id": canonical_id, "claim_id": claim_id, "source_id": source_id}


def make_script(segments):
    return {"run_date": "2026-09-02", "show_name": "Healthcare AI Briefing", "segments": segments, "excluded_no_evidence": []}


class PcmToWav(unittest.TestCase):
    def test_produces_correct_channel_sample_width_and_frame_rate(self):
        wav_bytes = audio_synth._pcm_to_wav(b"\x00\x01" * 10, sample_rate=24000, channels=1, sample_width=2)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 24000)

    def test_frame_count_matches_input_pcm_length(self):
        pcm = b"\x00\x01" * 100  # 100 frames at 16-bit mono
        wav_bytes = audio_synth._pcm_to_wav(pcm, sample_rate=24000, channels=1, sample_width=2)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnframes(), 100)

    def test_round_trips_the_exact_pcm_bytes(self):
        pcm = bytes(range(0, 40)) * 3
        wav_bytes = audio_synth._pcm_to_wav(pcm, channels=1, sample_width=2)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.readframes(wf.getnframes()), pcm)

    def test_defaults_match_the_live_confirmed_gemini_tts_format(self):
        wav_bytes = audio_synth._pcm_to_wav(b"\x00\x00")
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 24000)


class ConcatenateWavClips(unittest.TestCase):
    def test_empty_list_raises(self):
        with self.assertRaises(ValueError):
            audio_synth.concatenate_wav_clips([])

    def test_single_clip_round_trips_unchanged(self):
        clip = make_wav(50, fill_byte=b"\x07")
        result = audio_synth.concatenate_wav_clips([clip])
        params, frames = read_wav_params_and_frames(result)
        self.assertEqual(params, (1, 2, 24000))
        self.assertEqual(frames, b"\x07\x07" * 50)

    def test_two_clips_concatenate_in_order(self):
        first = make_wav(10, fill_byte=b"\x01")
        second = make_wav(10, fill_byte=b"\x02")
        result = audio_synth.concatenate_wav_clips([first, second])
        _, frames = read_wav_params_and_frames(result)
        self.assertEqual(frames, (b"\x01\x01" * 10) + (b"\x02\x02" * 10))

    def test_total_frame_count_is_the_sum_of_input_frame_counts(self):
        clips = [make_wav(5), make_wav(7), make_wav(3)]
        result = audio_synth.concatenate_wav_clips(clips)
        with wave.open(io.BytesIO(result), "rb") as wf:
            self.assertEqual(wf.getnframes(), 15)

    def test_mismatched_frame_rate_raises(self):
        clips = [make_wav(5, sample_rate=24000), make_wav(5, sample_rate=16000)]
        with self.assertRaises(ValueError):
            audio_synth.concatenate_wav_clips(clips)

    def test_mismatched_channel_count_raises(self):
        clips = [make_wav(5, channels=1), make_wav(5, channels=2)]
        with self.assertRaises(ValueError):
            audio_synth.concatenate_wav_clips(clips)

    def test_mismatched_sample_width_raises(self):
        clips = [make_wav(5, sample_width=2), make_wav(5, sample_width=1)]
        with self.assertRaises(ValueError):
            audio_synth.concatenate_wav_clips(clips)

    def test_silence_padding_defaults_to_zero_no_gap_inserted(self):
        # Confirms the neutral default explicitly, not just implicitly via
        # the pre-existing exact-equality tests above.
        first, second = make_wav(5, fill_byte=b"\x01"), make_wav(5, fill_byte=b"\x02")
        result = audio_synth.concatenate_wav_clips([first, second])
        with wave.open(io.BytesIO(result), "rb") as wf:
            self.assertEqual(wf.getnframes(), 10)

    def test_inter_segment_silence_inserts_correct_frame_count_between_clips_only(self):
        first, second, third = make_wav(5), make_wav(5), make_wav(5)
        # 24000 Hz, 100ms -> 2400 silent frames per gap, 2 gaps for 3 clips.
        result = audio_synth.concatenate_wav_clips([first, second, third], inter_segment_silence_ms=100.0)
        with wave.open(io.BytesIO(result), "rb") as wf:
            self.assertEqual(wf.getnframes(), 5 + 2400 + 5 + 2400 + 5)

    def test_inter_segment_silence_frames_are_true_zero(self):
        first, second = make_wav(2, fill_byte=b"\x01"), make_wav(2, fill_byte=b"\x02")
        result = audio_synth.concatenate_wav_clips([first, second], inter_segment_silence_ms=10.0)
        _, frames = read_wav_params_and_frames(result)
        # 24000Hz * 10ms = 240 silent frames = 480 bytes, sandwiched between the two real clips.
        expected_silence = b"\x00" * (240 * 2)
        self.assertEqual(frames, (b"\x01\x01" * 2) + expected_silence + (b"\x02\x02" * 2))

    def test_single_clip_gets_no_silence_padding_even_when_requested(self):
        clip = make_wav(5, fill_byte=b"\x09")
        result = audio_synth.concatenate_wav_clips([clip], inter_segment_silence_ms=500.0)
        with wave.open(io.BytesIO(result), "rb") as wf:
            self.assertEqual(wf.getnframes(), 5)  # no gap before/after a lone clip

    def test_normalize_defaults_to_false_leaves_samples_unscaled(self):
        quiet = make_wav_from_int16_samples([100, -100, 100, -100])
        result = audio_synth.concatenate_wav_clips([quiet])
        self.assertEqual(read_int16_samples(result), [100, -100, 100, -100])

    def test_normalize_scales_a_quiet_clip_up_toward_target_peak(self):
        quiet = make_wav_from_int16_samples([100, -100, 100, -100])
        result = audio_synth.concatenate_wav_clips([quiet], normalize=True)
        samples = read_int16_samples(result)
        expected_peak = int(32767 * audio_synth.DEFAULT_NORMALIZE_TARGET_PEAK_RATIO)
        self.assertEqual(max(abs(s) for s in samples), expected_peak)

    def test_normalize_scales_each_clip_independently_by_its_own_peak(self):
        quiet = make_wav_from_int16_samples([100, -100])
        louder = make_wav_from_int16_samples([1000, -1000])
        result = audio_synth.concatenate_wav_clips([quiet, louder], normalize=True)
        samples = read_int16_samples(result)
        expected_peak = int(32767 * audio_synth.DEFAULT_NORMALIZE_TARGET_PEAK_RATIO)
        # Both clips independently normalize to the SAME target peak, even
        # though "louder" started out 10x "quiet" in amplitude — that's the
        # whole point (consistent segment-to-segment loudness).
        self.assertEqual(max(abs(s) for s in samples[:2]), expected_peak)
        self.assertEqual(max(abs(s) for s in samples[2:]), expected_peak)

    def test_normalize_never_produces_a_sample_outside_int16_range(self):
        near_full_scale = make_wav_from_int16_samples([32767, -32768, 20000, -20000])
        result = audio_synth.concatenate_wav_clips([near_full_scale], normalize=True)
        samples = read_int16_samples(result)
        self.assertTrue(all(-32768 <= s <= 32767 for s in samples))

    def test_normalize_leaves_true_silence_unchanged_no_divide_by_zero(self):
        silent = make_wav_from_int16_samples([0, 0, 0, 0])
        result = audio_synth.concatenate_wav_clips([silent], normalize=True)
        self.assertEqual(read_int16_samples(result), [0, 0, 0, 0])

    def test_normalize_with_unsupported_sample_width_raises(self):
        clip = make_wav(5, sample_width=1)
        with self.assertRaises(ValueError):
            audio_synth.concatenate_wav_clips([clip], normalize=True)

    def test_normalize_and_silence_padding_can_combine(self):
        quiet = make_wav_from_int16_samples([50, -50])
        louder = make_wav_from_int16_samples([500, -500])
        result = audio_synth.concatenate_wav_clips([quiet, louder], normalize=True, inter_segment_silence_ms=10.0)
        with wave.open(io.BytesIO(result), "rb") as wf:
            self.assertEqual(wf.getnframes(), 2 + 240 + 2)  # 240 = 24000Hz * 10ms


class SilenceFrames(unittest.TestCase):
    def test_zero_duration_returns_empty_bytes(self):
        self.assertEqual(audio_synth._silence_frames(0.0, 24000, 1, 2), b"")

    def test_negative_duration_returns_empty_bytes(self):
        self.assertEqual(audio_synth._silence_frames(-5.0, 24000, 1, 2), b"")

    def test_frame_byte_length_matches_rate_channels_and_width(self):
        # 24000Hz * 0.5s = 12000 frames * 1 channel * 2 bytes/sample = 24000 bytes.
        result = audio_synth._silence_frames(500.0, 24000, 1, 2)
        self.assertEqual(len(result), 24000)

    def test_stereo_doubles_the_byte_length(self):
        mono = audio_synth._silence_frames(100.0, 24000, 1, 2)
        stereo = audio_synth._silence_frames(100.0, 24000, 2, 2)
        self.assertEqual(len(stereo), len(mono) * 2)

    def test_every_byte_is_zero(self):
        result = audio_synth._silence_frames(50.0, 24000, 1, 2)
        self.assertTrue(all(b == 0 for b in result))


class NormalizePcmPeakInt16(unittest.TestCase):
    def _samples(self, pcm_bytes):
        arr = array.array("h")
        arr.frombytes(pcm_bytes)
        return list(arr)

    def test_empty_input_returns_empty(self):
        self.assertEqual(audio_synth._normalize_pcm_peak_int16(b""), b"")

    def test_true_silence_is_unchanged(self):
        pcm = array.array("h", [0, 0, 0]).tobytes()
        self.assertEqual(audio_synth._normalize_pcm_peak_int16(pcm), pcm)

    def test_quiet_signal_is_scaled_up_to_target_peak(self):
        pcm = array.array("h", [10, -10, 5]).tobytes()
        result = audio_synth._normalize_pcm_peak_int16(pcm, target_peak_ratio=0.9)
        self.assertEqual(max(abs(s) for s in self._samples(result)), int(32767 * 0.9))

    def test_loud_signal_at_target_ratio_already_stays_essentially_the_same(self):
        target = int(32767 * 0.9)
        pcm = array.array("h", [target, -target]).tobytes()
        result = audio_synth._normalize_pcm_peak_int16(pcm, target_peak_ratio=0.9)
        self.assertEqual(max(abs(s) for s in self._samples(result)), target)

    def test_full_scale_signal_never_overflows_int16(self):
        pcm = array.array("h", [32767, -32768]).tobytes()
        result = audio_synth._normalize_pcm_peak_int16(pcm, target_peak_ratio=1.0)
        samples = self._samples(result)
        self.assertTrue(all(-32768 <= s <= 32767 for s in samples))

    def test_custom_target_peak_ratio_is_respected(self):
        pcm = array.array("h", [10, -10]).tobytes()
        result = audio_synth._normalize_pcm_peak_int16(pcm, target_peak_ratio=0.5)
        self.assertEqual(max(abs(s) for s in self._samples(result)), int(32767 * 0.5))

    def test_relative_proportions_between_samples_are_preserved(self):
        # A sample at half the peak's magnitude should still be at
        # (approximately) half the new peak's magnitude after scaling —
        # normalization must not distort the waveform's shape.
        pcm = array.array("h", [100, 50, -100]).tobytes()
        result = audio_synth._normalize_pcm_peak_int16(pcm, target_peak_ratio=0.9)
        samples = self._samples(result)
        self.assertAlmostEqual(samples[1] / samples[0], 0.5, places=2)


class AssembleEpisodeAudio(unittest.TestCase):
    def test_length_mismatch_between_segments_and_audio_raises(self):
        script = make_script([make_segment("intro"), make_segment("outro")])
        with self.assertRaises(ValueError):
            audio_synth.assemble_episode_audio(script, [make_wav(5)])

    def test_zero_segments_and_zero_clips_raises_via_concatenate(self):
        # Lengths match (0 == 0), so this passes assemble_episode_audio's
        # own length check — but concatenate_wav_clips itself rejects an
        # empty clip list, since an episode with no segments has no audio
        # to assemble at all.
        script = make_script([])
        with self.assertRaises(ValueError):
            audio_synth.assemble_episode_audio(script, [])

    def test_each_output_segment_carries_the_script_segments_ids(self):
        segments = [
            make_segment("intro", canonical_id=None, claim_id=None, source_id=None),
            make_segment("top_three_item", canonical_id="c1", claim_id="claim-1", source_id="src-1"),
        ]
        script = make_script(segments)
        clips = [make_wav(3), make_wav(3)]
        result = audio_synth.assemble_episode_audio(script, clips)
        self.assertEqual(result["segments"][0]["segment_type"], "intro")
        self.assertIsNone(result["segments"][0]["canonical_id"])
        self.assertEqual(result["segments"][1]["canonical_id"], "c1")
        self.assertEqual(result["segments"][1]["claim_id"], "claim-1")
        self.assertEqual(result["segments"][1]["source_id"], "src-1")

    def test_each_output_segment_carries_its_own_audio_bytes(self):
        segments = [make_segment("intro"), make_segment("outro")]
        script = make_script(segments)
        first_clip, second_clip = make_wav(3, fill_byte=b"\x01"), make_wav(3, fill_byte=b"\x02")
        result = audio_synth.assemble_episode_audio(script, [first_clip, second_clip])
        self.assertEqual(result["segments"][0]["audio_wav"], first_clip)
        self.assertEqual(result["segments"][1]["audio_wav"], second_clip)

    def test_full_episode_wav_is_every_clip_concatenated_in_order(self):
        segments = [make_segment("intro"), make_segment("outro")]
        script = make_script(segments)
        clips = [make_wav(4, fill_byte=b"\x03"), make_wav(6, fill_byte=b"\x04")]
        result = audio_synth.assemble_episode_audio(script, clips)
        _, frames = read_wav_params_and_frames(result["full_episode_wav"])
        self.assertEqual(frames, (b"\x03\x03" * 4) + (b"\x04\x04" * 6))

    def test_normalize_and_silence_params_pass_through_to_full_episode_wav(self):
        segments = [make_segment("intro"), make_segment("outro")]
        script = make_script(segments)
        clips = [make_wav_from_int16_samples([10, -10]), make_wav_from_int16_samples([10, -10])]
        result = audio_synth.assemble_episode_audio(script, clips, normalize=True, inter_segment_silence_ms=100.0)
        with wave.open(io.BytesIO(result["full_episode_wav"]), "rb") as wf:
            self.assertEqual(wf.getnframes(), 2 + 2400 + 2)  # 24000Hz * 100ms = 2400 silent frames
        samples = read_int16_samples(result["full_episode_wav"])
        expected_peak = int(32767 * audio_synth.DEFAULT_NORMALIZE_TARGET_PEAK_RATIO)
        self.assertEqual(max(abs(s) for s in samples), expected_peak)

    def test_per_segment_audio_wav_is_never_altered_even_when_full_episode_is_normalized(self):
        segments = [make_segment("intro"), make_segment("outro")]
        script = make_script(segments)
        quiet_clip = make_wav_from_int16_samples([10, -10])
        clips = [quiet_clip, quiet_clip]
        result = audio_synth.assemble_episode_audio(script, clips, normalize=True, inter_segment_silence_ms=100.0)
        # Each segment's own audio_wav is byte-for-byte the ORIGINAL unquiet
        # clip — only full_episode_wav (checked above) gets normalized/padded.
        self.assertEqual(result["segments"][0]["audio_wav"], quiet_clip)
        self.assertEqual(result["segments"][1]["audio_wav"], quiet_clip)

    def test_output_segment_count_matches_input(self):
        segments = [make_segment("intro"), make_segment("top_three_item"), make_segment("outro")]
        script = make_script(segments)
        clips = [make_wav(1), make_wav(1), make_wav(1)]
        result = audio_synth.assemble_episode_audio(script, clips)
        self.assertEqual(len(result["segments"]), 3)


if __name__ == "__main__":
    unittest.main()
