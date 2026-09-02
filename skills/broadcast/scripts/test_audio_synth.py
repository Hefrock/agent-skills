#!/usr/bin/env python3
"""Tests for audio_synth.py's pure logic (_pcm_to_wav, concatenate_wav_clips,
assemble_episode_audio) — no network. synthesize_text() is deliberately NOT
unit tested here, same treatment dedup_store.embed_text() gets: it's a thin
network wrapper, verified live via live_smoke_test.py instead (see that
file for the real, GitHub-Actions-confirmed request/response shape this
module's docstring documents).

Run: python test_audio_synth.py"""

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

    def test_output_segment_count_matches_input(self):
        segments = [make_segment("intro"), make_segment("top_three_item"), make_segment("outro")]
        script = make_script(segments)
        clips = [make_wav(1), make_wav(1), make_wav(1)]
        result = audio_synth.assemble_episode_audio(script, clips)
        self.assertEqual(len(result["segments"]), 3)


if __name__ == "__main__":
    unittest.main()
