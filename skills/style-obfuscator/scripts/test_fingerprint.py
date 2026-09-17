#!/usr/bin/env python3
"""Unit + CLI tests for fingerprint.py — crafted fixture text with known,
countable features (a fixed number of em dashes, a fixed sentence count) so
expected values are hand-verifiable, not just "the script agrees with itself."

Stdlib only (unittest). Run: python test_fingerprint.py"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
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


class SeverityForScore(unittest.TestCase):
    def test_below_threshold_is_none(self):
        self.assertEqual(fingerprint.severity_for_score(50.0, threshold=60.0), "none")

    def test_at_threshold_is_low(self):
        self.assertEqual(fingerprint.severity_for_score(60.0, threshold=60.0), "low")

    def test_medium_boundary(self):
        self.assertEqual(fingerprint.severity_for_score(70.0), "medium")

    def test_high_boundary(self):
        self.assertEqual(fingerprint.severity_for_score(85.0), "high")

    def test_max_score_is_high(self):
        self.assertEqual(fingerprint.severity_for_score(100.0), "high")


class RedactedPhrases(unittest.TestCase):
    def test_no_phrases(self):
        self.assertEqual(fingerprint._redacted_phrases([]), {"count": 0, "lengths": [], "redacted": True})

    def test_never_includes_verbatim_text(self):
        summary = fingerprint._redacted_phrases([("a secret phrase here", 2), ("another one there", 3)])
        self.assertNotIn("a secret phrase here", str(summary))
        self.assertNotIn("another one there", str(summary))
        self.assertEqual(summary["count"], 2)

    def test_lengths_reflect_word_counts(self):
        summary = fingerprint._redacted_phrases([("three word one", 2), ("four word phrase here", 2)])
        self.assertEqual(summary["lengths"], [3, 4])


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


class PrintReportAggregationGap(unittest.TestCase):
    def test_notable_gap_shows_warning_language(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fingerprint.print_report("corpus (3 file(s) pooled)", fp, reference_label="ref.md", reference_fp=fp,
                                      corpus_document_count=3, aggregation_best=("weakest.md", 40.0))
        out = buf.getvalue()
        self.assertIn("Aggregation gap: +60.0 points", out)
        self.assertIn("blind spot a cluster-based stylometric match exploits", out)

    def test_small_gap_shows_reassuring_language(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fingerprint.print_report("corpus (3 file(s) pooled)", fp, reference_label="ref.md", reference_fp=fp,
                                      corpus_document_count=3, aggregation_best=("close.md", 97.0))
        out = buf.getvalue()
        self.assertIn("doesn't reveal much beyond", out)
        self.assertNotIn("blind spot", out)

    def test_no_scoreable_document_reports_that_clearly(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fingerprint.print_report("corpus (1 file(s) pooled)", fp, reference_label="ref.md", reference_fp=fp,
                                      corpus_document_count=1, aggregation_best=None)
        out = buf.getvalue()
        self.assertIn("No individual document in the corpus had any words to score", out)

    def test_json_output_includes_aggregation_fields(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fingerprint.print_report("corpus (3 file(s) pooled)", fp, reference_label="ref.md", reference_fp=fp,
                                      json_out=True, corpus_document_count=3,
                                      aggregation_best=("weakest.md", 40.0))
        data = json.loads(buf.getvalue())
        self.assertEqual(data["corpus_document_count"], 3)
        self.assertEqual(data["highest_individual_score"], 40.0)
        self.assertEqual(data["highest_individual_document"], "weakest.md")
        self.assertEqual(data["aggregation_gap"], 60.0)

    def test_no_aggregation_section_without_corpus_document_count(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fingerprint.print_report("draft.md", fp, reference_label="ref.md", reference_fp=fp)
        self.assertNotIn("Aggregation gap", buf.getvalue())


class EmitFindingsExtraNote(unittest.TestCase):
    def test_extra_note_appended_to_reason(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        findings = fingerprint.emit_findings("corpus.md", fp, fp, threshold=50.0,
                                              extra_note="Pooled across 3 documents, notably higher than any one.")
        self.assertIn("Pooled across 3 documents", findings[0]["reason"])

    def test_no_extra_note_by_default(self):
        fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        findings = fingerprint.emit_findings("draft.md", fp, fp, threshold=50.0)
        self.assertNotIn("Pooled across", findings[0]["reason"])


class WriteRunLog(unittest.TestCase):
    def test_writes_one_json_record(self):
        with tempfile.TemporaryDirectory() as d:
            fingerprint.write_run_log(d, "draft.md", "reference.md", 82.5, "high")
            files = os.listdir(d)
            self.assertEqual(len(files), 1)
            with open(os.path.join(d, files[0])) as f:
                record = json.load(f)
            self.assertEqual(record["label"], "draft.md")
            self.assertEqual(record["reference_label"], "reference.md")
            self.assertEqual(record["similarity_score"], 82.5)
            self.assertEqual(record["severity"], "high")

    def test_never_writes_fingerprint_or_phrase_fields(self):
        with tempfile.TemporaryDirectory() as d:
            fingerprint.write_run_log(d, "draft.md", "reference.md", 82.5, "high")
            files = os.listdir(d)
            with open(os.path.join(d, files[0])) as f:
                record = json.load(f)
            self.assertNotIn("fingerprint", record)
            self.assertNotIn("top_phrases", record)

    def test_single_mode_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            fingerprint.write_run_log(d, "draft.md", "reference.md", 82.5, "high")
            files = os.listdir(d)
            with open(os.path.join(d, files[0])) as f:
                record = json.load(f)
            self.assertEqual(record["mode"], "single")
            self.assertIsNone(record["document_count"])

    def test_corpus_mode_when_document_count_given(self):
        with tempfile.TemporaryDirectory() as d:
            fingerprint.write_run_log(d, "corpus/", "reference.md", 82.5, "high", document_count=5)
            files = os.listdir(d)
            with open(os.path.join(d, files[0])) as f:
                record = json.load(f)
            self.assertEqual(record["mode"], "corpus")
            self.assertEqual(record["document_count"], 5)

    def test_creates_log_dir_if_missing(self):
        with tempfile.TemporaryDirectory() as d:
            nested = os.path.join(d, "nested", "log")
            fingerprint.write_run_log(nested, "draft.md", "reference.md", 10.0, "none")
            self.assertTrue(os.path.isdir(nested))

    def test_does_not_raise_on_unwritable_dir(self):
        # A file where a directory is expected -> os.makedirs raises OSError,
        # caught internally; must not propagate.
        with tempfile.TemporaryDirectory() as d:
            blocked = os.path.join(d, "blocked")
            with open(blocked, "w") as f:
                f.write("not a directory")
            fingerprint.write_run_log(blocked, "draft.md", "reference.md", 10.0, "none")


class LoadCorpusFiles(unittest.TestCase):
    def test_directory_returns_one_entry_per_file(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.md"), "w") as f:
                f.write("first document")
            with open(os.path.join(d, "b.txt"), "w") as f:
                f.write("second document")
            docs = fingerprint.load_corpus_files(d)
            self.assertEqual(len(docs), 2)
            texts = {text for _path, text in docs}
            self.assertEqual(texts, {"first document", "second document"})

    def test_single_file_returns_one_entry(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("solo document")
            path = f.name
        try:
            docs = fingerprint.load_corpus_files(path)
            self.assertEqual(docs, [(path, "solo document")])
        finally:
            os.unlink(path)

    def test_skips_dotfiles_and_dotdirs(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            with open(os.path.join(d, ".git", "hidden.md"), "w") as f:
                f.write("hidden")
            with open(os.path.join(d, "visible.md"), "w") as f:
                f.write("visible")
            docs = fingerprint.load_corpus_files(d)
            self.assertEqual(docs, [(os.path.join(d, "visible.md"), "visible")])

    def test_empty_directory_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(fingerprint.load_corpus_files(d), [])


class AggregationGap(unittest.TestCase):
    def test_returns_highest_scoring_document(self):
        ref_fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        docs = [
            ("weak.md", "The quarterly results were announced today across all regions."),
            ("strong.md", "This is a moderately long piece of sample text for testing here."),
        ]
        best_path, best_score = fingerprint.aggregation_gap(docs, ref_fp)
        self.assertEqual(best_path, "strong.md")
        self.assertEqual(best_score, 100.0)

    def test_skips_zero_word_documents(self):
        ref_fp = fingerprint.compute_fingerprint("This is a moderately long piece of sample text for testing here.")
        docs = [("empty.md", "   \n  "), ("real.md", "some real words here for the corpus today now")]
        best_path, _best_score = fingerprint.aggregation_gap(docs, ref_fp)
        self.assertEqual(best_path, "real.md")

    def test_returns_none_when_no_document_has_words(self):
        ref_fp = fingerprint.compute_fingerprint("some reference text here today")
        docs = [("empty1.md", ""), ("empty2.md", "   ")]
        self.assertIsNone(fingerprint.aggregation_gap(docs, ref_fp))

    def test_returns_none_for_empty_corpus(self):
        ref_fp = fingerprint.compute_fingerprint("some reference text here today")
        self.assertIsNone(fingerprint.aggregation_gap([], ref_fp))


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
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
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

    def test_repeated_phrases_redacted_by_default_in_text_report(self):
        path = self.make_file(
            "worth noting that this matters here worth noting that this matters again "
            "and one more time worth noting that this matters truly here today now."
        )
        proc = run_script("--file", path)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("worth noting that", proc.stdout)
        self.assertIn("text redacted", proc.stdout)

    def test_show_phrase_text_reveals_verbatim_phrases(self):
        path = self.make_file(
            "worth noting that this matters here worth noting that this matters again "
            "and one more time worth noting that this matters truly here today now."
        )
        proc = run_script("--file", path, "--show-phrase-text")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("worth noting that", proc.stdout)

    def test_json_report_redacts_phrase_text_by_default(self):
        path = self.make_file(
            "worth noting that this matters here worth noting that this matters again "
            "and one more time worth noting that this matters truly here today now."
        )
        proc = run_script("--file", path, "--json")
        data = json.loads(proc.stdout)
        self.assertNotIn("worth noting that", proc.stdout)
        self.assertTrue(data["fingerprint"]["top_phrases"]["redacted"])

    def test_json_report_show_phrase_text_includes_verbatim_list(self):
        path = self.make_file(
            "worth noting that this matters here worth noting that this matters again "
            "and one more time worth noting that this matters truly here today now."
        )
        proc = run_script("--file", path, "--json", "--show-phrase-text")
        data = json.loads(proc.stdout)
        self.assertIn(["worth noting that", 3], data["fingerprint"]["top_phrases"])

    def test_log_dir_requires_reference(self):
        path = self.make_file("Some draft text with no reference given at all here today.")
        with tempfile.TemporaryDirectory() as d:
            proc = run_script("--file", path, "--log-dir", d)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--reference", proc.stderr)

    def test_log_dir_writes_a_record_and_still_prints_report(self):
        draft = self.make_file("This is a moderately long piece of sample text for testing purposes here.")
        with tempfile.TemporaryDirectory() as d:
            proc = run_script("--file", draft, "--reference", draft, "--log-dir", d)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("Stylometric Fingerprint", proc.stdout)
            files = os.listdir(d)
            self.assertEqual(len(files), 1)
            with open(os.path.join(d, files[0])) as f:
                record = json.load(f)
            self.assertEqual(record["severity"], "high")
            self.assertNotIn("fingerprint", record)

    def make_corpus(self, *contents):
        d = tempfile.mkdtemp()
        self._paths.append(d)
        for i, content in enumerate(contents):
            with open(os.path.join(d, f"post{i}.md"), "w") as f:
                f.write(content)
        return d

    def test_corpus_standalone_report(self):
        d = self.make_corpus(
            "First pseudonymous post with a few sentences in it today.",
            "Second pseudonymous post with a few more sentences right here.",
        )
        proc = run_script("--corpus", d)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("2 file(s) pooled", proc.stdout)

    def test_corpus_with_reference_shows_aggregation_gap_section(self):
        d = self.make_corpus(
            "First pseudonymous post with a few sentences in it today.",
            "Second pseudonymous post with a few more sentences right here.",
        )
        reference = self.make_file("Some known writing sample for the reference side of this test today.")
        proc = run_script("--corpus", d, "--reference", reference)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Aggregation gap", proc.stdout)

    def test_corpus_without_reference_has_no_aggregation_section(self):
        d = self.make_corpus("Just one pseudonymous post here with a few sentences in it today.")
        proc = run_script("--corpus", d)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("Aggregation gap", proc.stdout)

    def test_corpus_json_includes_document_count(self):
        d = self.make_corpus(
            "First pseudonymous post with a few sentences in it today.",
            "Second pseudonymous post with a few more sentences right here.",
        )
        reference = self.make_file("Some known writing sample for the reference side of this test today.")
        proc = run_script("--corpus", d, "--reference", reference, "--json")
        data = json.loads(proc.stdout)
        self.assertEqual(data["corpus_document_count"], 2)

    def test_corpus_single_file_works_like_a_one_document_corpus(self):
        path = self.make_file("A single file used directly as a --corpus argument here today.")
        proc = run_script("--corpus", path)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("1 file(s) pooled", proc.stdout)

    def test_empty_corpus_directory_errors_cleanly(self):
        d = tempfile.mkdtemp()
        self._paths.append(d)
        proc = run_script("--corpus", d)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--corpus", proc.stderr)

    def test_corpus_emit_findings_pipes_into_a_finding_shaped_like_privacy_linter(self):
        d = self.make_corpus(
            "First pseudonymous post with a few sentences in it today.",
            "Second pseudonymous post with a few more sentences right here.",
        )
        reference = self.make_file("First pseudonymous post with a few sentences in it today.")
        proc = run_script("--corpus", d, "--reference", reference, "--emit-findings",
                           "--emit-findings-threshold", "1")
        self.assertEqual(proc.returncode, 0)
        findings = json.loads(proc.stdout)
        self.assertEqual(len(findings), 1)
        self.assertIn("(2 file(s) pooled)", findings[0]["location"])

    def test_corpus_log_dir_records_document_count(self):
        d = self.make_corpus(
            "First pseudonymous post with a few sentences in it today.",
            "Second pseudonymous post with a few more sentences right here.",
        )
        reference = self.make_file("Some known writing sample for the reference side of this test today.")
        with tempfile.TemporaryDirectory() as log_dir:
            proc = run_script("--corpus", d, "--reference", reference, "--log-dir", log_dir)
            self.assertEqual(proc.returncode, 0)
            files = os.listdir(log_dir)
            with open(os.path.join(log_dir, files[0])) as f:
                record = json.load(f)
            self.assertEqual(record["mode"], "corpus")
            self.assertEqual(record["document_count"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
