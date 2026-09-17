#!/usr/bin/env python3
"""Unit + CLI tests for fingerprint.py — crafted fixture text with known,
countable features (a fixed number of em dashes, a fixed sentence count) so
expected values are hand-verifiable, not just "the script agrees with itself."

Stdlib only (unittest). Run: python test_fingerprint.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "fingerprint.py")

spec = importlib.util.spec_from_file_location("fingerprint", SCRIPT)
fingerprint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fingerprint)


def run_script(*args, input_text=None):
    return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, input=input_text)


class TokenizeWords(unittest.TestCase):
    def test_splits_on_whitespace_and_punctuation(self):
        self.assertEqual(fingerprint.tokenize_words("Hello, world! It's fine."), ["hello", "world", "it's", "fine"])

    def test_lowercases(self):
        self.assertEqual(fingerprint.tokenize_words("ABC Def"), ["abc", "def"])

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(fingerprint.tokenize_words(""), [])


class SplitSentences(unittest.TestCase):
    def test_three_sentences(self):
        text = "This is one. This is two! Is this three?"
        self.assertEqual(len(fingerprint.split_sentences(text)), 3)

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(fingerprint.split_sentences(""), [])

    def test_single_sentence_no_terminal_punctuation(self):
        self.assertEqual(len(fingerprint.split_sentences("just some words with no period")), 1)


class SplitParagraphs(unittest.TestCase):
    def test_splits_on_blank_line(self):
        text = "Paragraph one.\n\nParagraph two."
        self.assertEqual(len(fingerprint.split_paragraphs(text)), 2)

    def test_single_paragraph_no_blank_line(self):
        self.assertEqual(len(fingerprint.split_paragraphs("one\ntwo\nthree")), 1)


class TopRepeatedPhrases(unittest.TestCase):
    def test_finds_a_repeated_three_gram(self):
        words = "worth noting that worth noting that something else entirely".split()
        phrases = fingerprint.top_repeated_phrases(words)
        self.assertIn(("worth noting that", 2), phrases)

    def test_no_repeats_returns_empty(self):
        words = "every single word here is different from all the others".split()
        self.assertEqual(fingerprint.top_repeated_phrases(words), [])

    def test_respects_min_count(self):
        words = "a b c a b c".split()  # "a b c" repeats exactly twice
        self.assertEqual(fingerprint.top_repeated_phrases(words, min_count=3), [])


class ComputeFingerprint(unittest.TestCase):
    def test_em_dash_rate_counted_correctly(self):
        # Exactly 2 em dashes in a 10-word text -> 200 per 1000 words.
        text = "one two three — four five — six seven eight nine ten"
        fp = fingerprint.compute_fingerprint(text)
        self.assertEqual(fp["word_count"], 10)
        self.assertEqual(fp["em_dash_per_1k"], 200.0)

    def test_comma_and_semicolon_rates(self):
        text = "one, two, three; four five six seven eight nine ten"  # 2 commas, 1 semicolon, 10 words
        fp = fingerprint.compute_fingerprint(text)
        self.assertEqual(fp["comma_per_1k"], 200.0)
        self.assertEqual(fp["semicolon_per_1k"], 100.0)

    def test_contraction_rate(self):
        text = "it's not that I don't care, it's just complicated honestly today"  # 3 contractions, 11 words
        fp = fingerprint.compute_fingerprint(text)
        self.assertAlmostEqual(fp["contraction_per_1k"], 3 / 11 * 1000, places=1)

    def test_passive_voice_regular_participle_detected(self):
        fp = fingerprint.compute_fingerprint("The ball was kicked by the boy.")
        self.assertGreater(fp["passive_voice_per_1k"], 0)

    def test_passive_voice_irregular_participle_detected(self):
        fp = fingerprint.compute_fingerprint("The book was written by an author nobody has heard of before today somehow.")
        self.assertGreater(fp["passive_voice_per_1k"], 0)

    def test_active_voice_not_flagged_as_passive(self):
        fp = fingerprint.compute_fingerprint("The boy kicked the ball across the field into the goal cleanly.")
        self.assertEqual(fp["passive_voice_per_1k"], 0.0)

    def test_vocabulary_richness_all_unique_words_is_one(self):
        fp = fingerprint.compute_fingerprint("every single word here is completely different from all others")
        self.assertEqual(fp["vocabulary_richness"], 1.0)

    def test_vocabulary_richness_all_repeated_word(self):
        fp = fingerprint.compute_fingerprint("the the the the the")
        self.assertEqual(fp["vocabulary_richness"], 0.2)

    def test_function_word_rate_present(self):
        fp = fingerprint.compute_fingerprint("the the cat sat on the mat quietly today")
        self.assertGreater(fp["fw_the_per_1k"], 0)

    def test_empty_text_all_rates_zero_no_crash(self):
        fp = fingerprint.compute_fingerprint("")
        self.assertEqual(fp["word_count"], 0)
        self.assertEqual(fp["em_dash_per_1k"], 0.0)
        self.assertEqual(fp["vocabulary_richness"], 0.0)

    def test_single_word_no_crash(self):
        fp = fingerprint.compute_fingerprint("hello")
        self.assertEqual(fp["word_count"], 1)
        self.assertEqual(fp["stdev_sentence_length"], 0.0)

    def test_mean_sentence_length(self):
        # Two sentences: 3 words, then 5 words.
        fp = fingerprint.compute_fingerprint("One two three. Four five six seven eight.")
        self.assertEqual(fp["mean_sentence_length"], 4.0)


class SimilarityScore(unittest.TestCase):
    def test_identical_fingerprints_score_100(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing purposes here.")
        self.assertEqual(fingerprint.similarity_score(fp, fp), 100.0)

    def test_very_different_texts_score_lower_than_similar_pair(self):
        style_a1 = fingerprint.compute_fingerprint(
            "I think this works — but it's worth double-checking. It's not perfect; that's fine."
        )
        style_a2 = fingerprint.compute_fingerprint(
            "I think that's right — but it's worth verifying first. It's not exact; that's okay."
        )
        style_b = fingerprint.compute_fingerprint(
            "The quarterly results were announced today. Revenue increased. The company issued a statement."
        )
        same_author_score = fingerprint.similarity_score(style_a1, style_a2)
        different_style_score = fingerprint.similarity_score(style_a1, style_b)
        self.assertGreater(same_author_score, different_style_score)

    def test_empty_reference_does_not_crash(self):
        fp = fingerprint.compute_fingerprint("some real text here for the draft side of the comparison")
        empty_fp = fingerprint.compute_fingerprint("")
        score = fingerprint.similarity_score(fp, empty_fp)
        self.assertIsInstance(score, float)


class FeatureDeltas(unittest.TestCase):
    def test_returns_one_row_per_numeric_feature(self):
        fp_a = fingerprint.compute_fingerprint("some sample text for the draft side right here today now")
        fp_b = fingerprint.compute_fingerprint("some other different sample text for the reference side over there")
        deltas = fingerprint.feature_deltas(fp_a, fp_b)
        numeric_keys = fingerprint._numeric_feature_keys(fp_a)
        self.assertEqual(len(deltas), len(numeric_keys))

    def test_sorted_descending_by_match_percentage(self):
        fp_a = fingerprint.compute_fingerprint("some sample text for the draft side right here today now")
        fp_b = fingerprint.compute_fingerprint("some other different sample text for the reference side over there")
        deltas = fingerprint.feature_deltas(fp_a, fp_b)
        pcts = [row[3] for row in deltas]
        self.assertEqual(pcts, sorted(pcts, reverse=True))


class EmitFindings(unittest.TestCase):
    def test_below_threshold_returns_empty_list(self):
        fp_a = fingerprint.compute_fingerprint("I think this works — but it's worth double-checking honestly.")
        fp_b = fingerprint.compute_fingerprint("The quarterly results were announced today. Revenue increased overall.")
        findings = fingerprint.emit_findings("draft.md", fp_a, fp_b, threshold=99.0)
        self.assertEqual(findings, [])

    def test_at_or_above_threshold_returns_one_finding_shaped_like_privacy_linter(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing purposes here.")
        findings = fingerprint.emit_findings("draft.md", fp, fp, threshold=50.0)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(set(finding.keys()), {"severity", "leak_class", "finding", "reason", "location"})
        self.assertEqual(finding["leak_class"], "stylometric")
        self.assertEqual(finding["location"], "draft.md")
        self.assertIn(finding["severity"], ("low", "medium", "high"))

    def test_identical_fingerprints_score_high_severity(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing purposes here.")
        findings = fingerprint.emit_findings("draft.md", fp, fp, threshold=50.0)
        self.assertEqual(findings[0]["severity"], "high")

    def test_severity_thresholds_are_ordered(self):
        self.assertLess(fingerprint.EMIT_FINDINGS_DEFAULT_THRESHOLD, fingerprint.EMIT_FINDINGS_MEDIUM)
        self.assertLess(fingerprint.EMIT_FINDINGS_MEDIUM, fingerprint.EMIT_FINDINGS_HIGH)


class LoadReferenceText(unittest.TestCase):
    def test_single_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            path = f.name
        try:
            self.assertEqual(fingerprint.load_reference_text(path), "hello world")
        finally:
            os.unlink(path)

    def test_directory_concatenates_md_and_txt_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.md"), "w") as f:
                f.write("first file")
            with open(os.path.join(d, "b.txt"), "w") as f:
                f.write("second file")
            with open(os.path.join(d, "ignored.json"), "w") as f:
                f.write("should not appear")
            text = fingerprint.load_reference_text(d)
            self.assertIn("first file", text)
            self.assertIn("second file", text)
            self.assertNotIn("should not appear", text)

    def test_directory_skips_dotfiles_and_dotdirs(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            with open(os.path.join(d, ".git", "hidden.md"), "w") as f:
                f.write("hidden content")
            with open(os.path.join(d, "visible.md"), "w") as f:
                f.write("visible content")
            text = fingerprint.load_reference_text(d)
            self.assertIn("visible content", text)
            self.assertNotIn("hidden content", text)


class Cli(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def make_file(self, content):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        self._paths.append(path)
        return path

    def test_standalone_report(self):
        path = self.make_file("This is a real piece of writing with several sentences in it. Here is another one.")
        proc = run_script("--file", path)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Stylometric Fingerprint", proc.stdout)
        self.assertIn("Mean sentence length", proc.stdout)

    def test_stdin_mode(self):
        proc = run_script("--text", "-", input_text="Some text piped in through stdin for analysis here today.")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("<stdin>", proc.stdout)

    def test_comparison_report_includes_similarity(self):
        draft = self.make_file("I think this is right — but I'm not fully sure. Worth checking further.")
        reference = self.make_file("I think that's correct — but I'm not totally certain. Worth verifying more.")
        proc = run_script("--file", draft, "--reference", reference)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Overall similarity", proc.stdout)
        self.assertIn("Closely matches your reference on", proc.stdout)

    def test_json_output_is_valid(self):
        path = self.make_file("Some sample text for JSON output testing purposes right here today.")
        proc = run_script("--file", path, "--json")
        data = json.loads(proc.stdout)
        self.assertIn("fingerprint", data)
        self.assertEqual(data["label"], path)

    def test_json_output_with_reference_includes_score(self):
        draft = self.make_file("Some draft text here for the comparison test today right now.")
        reference = self.make_file("Some other reference text here for the comparison test right now.")
        proc = run_script("--file", draft, "--reference", reference, "--json")
        data = json.loads(proc.stdout)
        self.assertIn("similarity_score", data)
        self.assertIn("reference_fingerprint", data)

    def test_zero_word_input_errors_cleanly(self):
        path = self.make_file("   \n  \n")
        proc = run_script("--file", path)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no words found", proc.stderr)

    def test_missing_file_and_text_errors_cleanly(self):
        proc = run_script()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--file", proc.stderr)

    def test_directory_as_reference(self):
        draft = self.make_file("Draft text for the directory-reference CLI test right here today.")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "one.md"), "w") as f:
                f.write("Some known writing sample number one for the reference corpus today.")
            with open(os.path.join(d, "two.md"), "w") as f:
                f.write("Some known writing sample number two for the reference corpus today.")
            proc = run_script("--file", draft, "--reference", d)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Overall similarity", proc.stdout)

    def test_short_input_shows_noise_warning(self):
        path = self.make_file("Short text here.")
        proc = run_script("--file", path)
        self.assertIn("under 100 words", proc.stdout)

    def test_emit_findings_requires_reference(self):
        path = self.make_file("Some draft text with no reference given at all here today.")
        proc = run_script("--file", path, "--emit-findings")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--reference", proc.stderr)

    def test_emit_findings_outputs_finding_list_json(self):
        draft = self.make_file("This is a moderately long piece of sample text for testing purposes here.")
        proc = run_script("--file", draft, "--reference", draft, "--emit-findings")
        self.assertEqual(proc.returncode, 0)
        findings = json.loads(proc.stdout)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["leak_class"], "stylometric")

    def test_emit_findings_below_threshold_outputs_empty_list(self):
        draft = self.make_file("I think this works — but it's worth double-checking honestly today.")
        reference = self.make_file("The quarterly results were announced today. Revenue increased overall this year.")
        proc = run_script("--file", draft, "--reference", reference, "--emit-findings",
                           "--emit-findings-threshold", "99")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
