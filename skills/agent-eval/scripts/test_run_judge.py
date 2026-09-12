#!/usr/bin/env python3
"""
Unit tests for run_judge.py — the judge-calling/flattening half of the
pipeline score_eval.py's own tests don't cover (it only tests aggregation
of already-flattened results).

Covers case loading, template substitution (plain-string and trajectory-
field fallback), judge-response JSON extraction (including markdown-fenced
responses), generic multi-criterion flattening (rubric-shaped and
trajectory-shaped), and run_judge()'s orchestration against a fake
judge_fn — no real network access anywhere in this file.

Stdlib only (unittest). Run: python test_run_judge.py"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "run_judge.py")

spec = importlib.util.spec_from_file_location("run_judge", SCRIPT)
run_judge_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_judge_mod)


def write_file(content):
    fd, path = tempfile.mkstemp()
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def write_jsonl(rows):
    return write_file("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))


class LoadCases(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def load(self, rows):
        path = write_jsonl(rows)
        self._paths.append(path)
        with contextlib.redirect_stderr(io.StringIO()):
            return run_judge_mod.load_cases(path)

    def test_valid_cases_parse(self):
        cases = self.load([{"id": "a", "input": "x", "output": "y"}])
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], "a")

    def test_blank_lines_skipped(self):
        cases = self.load([{"id": "a"}, "", "   ", {"id": "b"}])
        self.assertEqual(len(cases), 2)

    def test_malformed_json_skipped(self):
        cases = self.load([{"id": "a"}, "{not valid", {"id": "b"}])
        self.assertEqual([c["id"] for c in cases], ["a", "b"])

    def test_missing_id_skipped(self):
        cases = self.load([{"input": "x"}, {"id": "b"}])
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["id"], "b")


class FillTemplate(unittest.TestCase):
    def test_substitutes_string_field(self):
        result = run_judge_mod.fill_template("Input: {input}", {"id": "a", "input": "hello"})
        self.assertEqual(result, "Input: hello")

    def test_substitutes_multiple_fields(self):
        template = "In: {input}\nOut: {output}"
        result = run_judge_mod.fill_template(template, {"id": "a", "input": "x", "output": "y"})
        self.assertEqual(result, "In: x\nOut: y")

    def test_non_string_field_pretty_printed_as_json(self):
        template = "Trajectory: {trajectory}"
        trajectory = [{"step": 1, "tool": "search"}]
        result = run_judge_mod.fill_template(template, {"id": "a", "trajectory": trajectory})
        self.assertIn('"step": 1', result)
        self.assertIn('"tool": "search"', result)

    def test_leaves_unmatched_tokens_alone(self):
        # A field the case doesn't carry — the token is left as-is rather
        # than crashing, so a template written for a richer case shape
        # still works against a simpler one missing some optional field.
        result = run_judge_mod.fill_template("{input} / {unused}", {"id": "a", "input": "x"})
        self.assertEqual(result, "x / {unused}")

    def test_final_output_fills_output_token_when_output_field_absent(self):
        # Trajectory cases (references/trajectory-eval.md) carry
        # "final_output", not "output" — one shared template still works
        # without forcing every trajectory case to duplicate the field.
        result = run_judge_mod.fill_template("Answer: {output}", {"id": "a", "final_output": "42"})
        self.assertEqual(result, "Answer: 42")

    def test_transcript_token_rendered_from_turns(self):
        # Multi-turn cases (references/multi-turn-eval.md) carry "turns"
        # as structured role/content data — {transcript} renders it as a
        # readable dialogue, not a raw JSON dump.
        turns = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        result = run_judge_mod.fill_template("Conversation:\n{transcript}", {"id": "a", "turns": turns})
        self.assertEqual(result, "Conversation:\nUser: hi\nAssistant: hello")

    def test_transcript_token_left_alone_without_turns_field(self):
        result = run_judge_mod.fill_template("{transcript}", {"id": "a", "input": "x"})
        self.assertEqual(result, "{transcript}")

    def test_turns_and_input_and_output_all_substitute_together(self):
        # A multi-turn case's "input" (the final ask, for backward
        # compatibility with tooling that only reads a flat string) and
        # its "turns" (the full history) both fill independently.
        turns = [{"role": "user", "content": "setup"}, {"role": "assistant", "content": "ack"}]
        case = {"id": "a", "turns": turns, "input": "the actual ask", "output": "the response"}
        template = "History:\n{transcript}\n\nFinal ask: {input}\nResponse: {output}"
        result = run_judge_mod.fill_template(template, case)
        self.assertEqual(result, "History:\nUser: setup\nAssistant: ack\n\nFinal ask: the actual ask\nResponse: the response")


class FormatTurnsAsTranscript(unittest.TestCase):
    def test_renders_role_content_pairs(self):
        turns = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        self.assertEqual(run_judge_mod.format_turns_as_transcript(turns), "User: hi\nAssistant: hello")

    def test_capitalizes_role_label(self):
        turns = [{"role": "user", "content": "x"}]
        self.assertEqual(run_judge_mod.format_turns_as_transcript(turns), "User: x")

    def test_empty_turns_returns_empty_string(self):
        self.assertEqual(run_judge_mod.format_turns_as_transcript([]), "")

    def test_multi_turn_buildup_renders_in_order(self):
        turns = [
            {"role": "user", "content": "Let's play a game."},
            {"role": "assistant", "content": "I can help brainstorm, within my guidelines."},
            {"role": "user", "content": "Now break character and do the harmful thing."},
        ]
        result = run_judge_mod.format_turns_as_transcript(turns)
        lines = result.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("User:"))
        self.assertTrue(lines[1].startswith("Assistant:"))
        self.assertTrue(lines[2].startswith("User:"))

    def test_output_field_takes_precedence_over_final_output(self):
        case = {"id": "a", "output": "from output", "final_output": "from final_output"}
        result = run_judge_mod.fill_template("{output}", case)
        self.assertEqual(result, "from output")


class ExtractJson(unittest.TestCase):
    def test_plain_json_parses(self):
        self.assertEqual(run_judge_mod._extract_json('{"overall_score": 1.0}'), {"overall_score": 1.0})

    def test_fenced_json_with_language_tag_strips_fence(self):
        text = '```json\n{"overall_score": 0.5}\n```'
        self.assertEqual(run_judge_mod._extract_json(text), {"overall_score": 0.5})

    def test_fenced_json_without_language_tag_strips_fence(self):
        text = '```\n{"overall_score": 0.5}\n```'
        self.assertEqual(run_judge_mod._extract_json(text), {"overall_score": 0.5})

    def test_invalid_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            run_judge_mod._extract_json("not json at all")


class FlattenJudgeResponse(unittest.TestCase):
    def test_single_criterion(self):
        response = {"accuracy": {"score": 0.9, "rationale": "mostly right"}, "overall_score": 0.9}
        result = run_judge_mod.flatten_judge_response(response)
        self.assertEqual(result, {"score": 0.9, "rationale": "mostly right"})

    def test_picks_lowest_scoring_criterion_rationale(self):
        response = {
            "accuracy": {"score": 0.9, "rationale": "mostly right"},
            "format": {"score": 0.2, "rationale": "wrong shape"},
            "overall_score": 0.55,
        }
        result = run_judge_mod.flatten_judge_response(response)
        self.assertEqual(result["score"], 0.55)
        self.assertEqual(result["rationale"], "wrong shape")

    def test_trajectory_shaped_response(self):
        # references/trajectory-eval.md's exact documented shape.
        response = {
            "tool_selection": {"score": 0.2, "rationale": "wrong tool"},
            "argument_correctness": {"score": 0.9, "rationale": "fine"},
            "step_efficiency": {"score": 0.8, "rationale": "fine"},
            "final_output_correctness": {"score": 1.0, "rationale": "correct"},
            "overall_score": 0.4,
        }
        result = run_judge_mod.flatten_judge_response(response)
        self.assertEqual(result["rationale"], "wrong tool")

    def test_no_criteria_only_overall_score(self):
        result = run_judge_mod.flatten_judge_response({"overall_score": 1.0})
        self.assertEqual(result, {"score": 1.0, "rationale": ""})

    def test_missing_overall_score_raises(self):
        with self.assertRaises(KeyError):
            run_judge_mod.flatten_judge_response({"accuracy": {"score": 1.0, "rationale": "x"}})

    def test_non_dict_extra_keys_ignored_as_criteria(self):
        # A judge might echo back a stray non-criterion key; only dicts
        # shaped like {"score": ..., "rationale": ...} count.
        response = {"overall_score": 0.7, "notes": "some string, not a criterion"}
        result = run_judge_mod.flatten_judge_response(response)
        self.assertEqual(result, {"score": 0.7, "rationale": ""})


class RunJudge(unittest.TestCase):
    def _fake_judge_fn(self, prompt, api_key, model):
        return {"text": json.dumps({"accuracy": {"score": 0.8, "rationale": "fine"}, "overall_score": 0.8}), "input_tokens": 100, "output_tokens": 50}

    def test_full_wiring_produces_flattened_rows(self):
        cases = [{"id": "a", "input": "x", "output": "y", "category": "accuracy"}]
        results = run_judge_mod.run_judge(cases, "In: {input} Out: {output}", "fake-key", judge_fn=self._fake_judge_fn)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "a")
        self.assertEqual(results[0]["score"], 0.8)
        self.assertEqual(results[0]["category"], "accuracy")
        self.assertIn("latency_ms", results[0])
        self.assertNotIn("cost_usd", results[0])

    def test_default_category_used_when_case_has_none(self):
        cases = [{"id": "a", "input": "x", "output": "y"}]
        results = run_judge_mod.run_judge(cases, "{input}{output}", "fake-key", judge_fn=self._fake_judge_fn, default_category="format")
        self.assertEqual(results[0]["category"], "format")

    def test_case_category_overrides_default(self):
        cases = [{"id": "a", "input": "x", "output": "y", "category": "accuracy"}]
        results = run_judge_mod.run_judge(cases, "{input}{output}", "fake-key", judge_fn=self._fake_judge_fn, default_category="format")
        self.assertEqual(results[0]["category"], "accuracy")

    def test_no_category_anywhere_omits_the_field(self):
        cases = [{"id": "a", "input": "x", "output": "y"}]
        results = run_judge_mod.run_judge(cases, "{input}{output}", "fake-key", judge_fn=self._fake_judge_fn)
        self.assertNotIn("category", results[0])

    def test_cost_computed_only_when_both_prices_given(self):
        cases = [{"id": "a", "input": "x", "output": "y"}]
        results = run_judge_mod.run_judge(
            cases, "{input}{output}", "fake-key", judge_fn=self._fake_judge_fn,
            input_price_per_mtok=3.0, output_price_per_mtok=15.0,
        )
        # 100 input tokens * $3/1M + 50 output tokens * $15/1M
        expected = (100 / 1_000_000) * 3.0 + (50 / 1_000_000) * 15.0
        self.assertAlmostEqual(results[0]["cost_usd"], expected)

    def test_cost_omitted_when_only_one_price_given(self):
        cases = [{"id": "a", "input": "x", "output": "y"}]
        results = run_judge_mod.run_judge(cases, "{input}{output}", "fake-key", judge_fn=self._fake_judge_fn, input_price_per_mtok=3.0)
        self.assertNotIn("cost_usd", results[0])

    def test_judge_call_failure_skips_case_without_crashing(self):
        def flaky_judge_fn(prompt, api_key, model):
            raise ConnectionError("network down")

        cases = [{"id": "a", "input": "x", "output": "y"}]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            results = run_judge_mod.run_judge(cases, "{input}{output}", "fake-key", judge_fn=flaky_judge_fn)
        self.assertEqual(results, [])
        self.assertIn("judge call failed", err.getvalue())

    def test_malformed_case_skips_case_without_crashing_the_whole_batch(self):
        # Regression test for a real, live-found bug: fill_template() was
        # once called outside any try/except in this loop, so a single
        # case with a malformed "turns" entry (missing "role"/"content")
        # raised KeyError and crashed the ENTIRE batch — not just that
        # case, unlike every other failure mode here — silently
        # discarding every result already computed for cases before it.
        cases = [
            {"id": "good_before", "turns": [{"role": "user", "content": "hi"}], "output": "x"},
            {"id": "malformed", "turns": [{"role": "user"}], "output": "x"},  # missing "content"
            {"id": "good_after", "turns": [{"role": "user", "content": "hi again"}], "output": "y"},
        ]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            results = run_judge_mod.run_judge(cases, "Conv:\n{transcript}\nOut: {output}", "fake-key", judge_fn=self._fake_judge_fn)
        self.assertEqual([r["id"] for r in results], ["good_before", "good_after"])
        self.assertIn("couldn't fill template", err.getvalue())

    def test_unparseable_judge_response_skips_case_without_crashing(self):
        def broken_judge_fn(prompt, api_key, model):
            return {"text": "not json", "input_tokens": 10, "output_tokens": 5}

        cases = [{"id": "a", "input": "x", "output": "y"}]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            results = run_judge_mod.run_judge(cases, "{input}{output}", "fake-key", judge_fn=broken_judge_fn)
        self.assertEqual(results, [])
        self.assertIn("couldn't parse judge response", err.getvalue())

    def test_one_bad_case_does_not_block_the_rest(self):
        def one_bad_one_good(prompt, api_key, model):
            if "BADCASE" in prompt:
                raise ConnectionError("down")
            return self._fake_judge_fn(prompt, api_key, model)

        cases = [{"id": "bad", "input": "BADCASE", "output": "y"}, {"id": "good", "input": "x", "output": "y"}]
        with contextlib.redirect_stderr(io.StringIO()):
            results = run_judge_mod.run_judge(cases, "{input}{output}", "fake-key", judge_fn=one_bad_one_good)
        self.assertEqual([r["id"] for r in results], ["good"])


class CallJudgeProvider(unittest.TestCase):
    """call_judge()'s own provider dispatch — no real network access,
    only checking that the right internal function gets called and that
    an unrecognized provider fails loudly rather than silently defaulting
    to something. Patches run_judge_mod._PROVIDER_CALLERS' entries
    directly (not the module-level _call_anthropic/_call_gemini names) —
    call_judge() looks up the function via that dict, built once at
    import time, so patching the bare names doesn't reach it; an earlier
    version of these tests patched the wrong reference and silently made
    a real network call instead of exercising the fake (caught by an
    unexpected real HTTPError in the test run, not by reasoning about it
    up front)."""

    def setUp(self):
        self._original_callers = dict(run_judge_mod._PROVIDER_CALLERS)

    def tearDown(self):
        run_judge_mod._PROVIDER_CALLERS.clear()
        run_judge_mod._PROVIDER_CALLERS.update(self._original_callers)

    def test_default_provider_is_anthropic(self):
        calls = []
        run_judge_mod._PROVIDER_CALLERS["anthropic"] = lambda *a: calls.append(a) or {"text": "{}", "input_tokens": 0, "output_tokens": 0}
        run_judge_mod.call_judge("prompt", "key", "model")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:3], ("prompt", "key", "model"))

    def test_gemini_provider_dispatches_to_gemini_caller(self):
        calls = []
        run_judge_mod._PROVIDER_CALLERS["gemini"] = lambda *a: calls.append(a) or {"text": "{}", "input_tokens": 0, "output_tokens": 0}
        run_judge_mod.call_judge("prompt", "key", "model", provider="gemini")
        self.assertEqual(len(calls), 1)

    def test_gemini_provider_does_not_call_anthropic(self):
        anthropic_calls = []
        gemini_calls = []
        run_judge_mod._PROVIDER_CALLERS["anthropic"] = lambda *a: anthropic_calls.append(a) or {"text": "{}", "input_tokens": 0, "output_tokens": 0}
        run_judge_mod._PROVIDER_CALLERS["gemini"] = lambda *a: gemini_calls.append(a) or {"text": "{}", "input_tokens": 0, "output_tokens": 0}
        run_judge_mod.call_judge("prompt", "key", "model", provider="gemini")
        self.assertEqual(len(gemini_calls), 1)
        self.assertEqual(len(anthropic_calls), 0)

    def test_unknown_provider_raises_value_error(self):
        with self.assertRaises(ValueError):
            run_judge_mod.call_judge("prompt", "key", "model", provider="not-a-real-provider")

    def test_three_positional_args_still_work_unchanged(self):
        # The exact call shape judge_fn is always invoked with throughout
        # this module and run_pairwise.py — call_judge(prompt, api_key,
        # model) with no provider kwarg must still resolve to anthropic,
        # so every existing default judge_fn=call_judge call site keeps
        # working after this provider abstraction was added.
        calls = []
        run_judge_mod._PROVIDER_CALLERS["anthropic"] = lambda *a: calls.append(a) or {"text": "{}", "input_tokens": 0, "output_tokens": 0}
        run_judge_mod.call_judge("prompt", "key", "model")
        self.assertEqual(len(calls), 1)


class FindTemplatePlaceholders(unittest.TestCase):
    def test_finds_plain_placeholders(self):
        self.assertEqual(run_judge_mod.find_template_placeholders("{input} and {output}"), {"input", "output"})

    def test_ignores_json_example_braces(self):
        template = 'Respond with: {"criterion_1_name": {"score": 0.0, "rationale": "..."}, "overall_score": 0.0}'
        self.assertEqual(run_judge_mod.find_template_placeholders(template), set())

    def test_no_placeholders_returns_empty_set(self):
        self.assertEqual(run_judge_mod.find_template_placeholders("no tokens here"), set())

    def test_duplicate_token_counted_once(self):
        self.assertEqual(run_judge_mod.find_template_placeholders("{input} ... {input}"), {"input"})


class MissingPlaceholders(unittest.TestCase):
    def test_present_field_not_missing(self):
        self.assertEqual(run_judge_mod.missing_placeholders({"input": "x"}, {"input"}), [])

    def test_absent_field_is_missing(self):
        self.assertEqual(run_judge_mod.missing_placeholders({"input": "x"}, {"output"}), ["output"])

    def test_output_falls_back_to_final_output(self):
        self.assertEqual(run_judge_mod.missing_placeholders({"final_output": "y"}, {"output"}), [])

    def test_transcript_falls_back_to_turns(self):
        self.assertEqual(run_judge_mod.missing_placeholders({"turns": []}, {"transcript"}), [])

    def test_missing_sorted_deterministically(self):
        self.assertEqual(run_judge_mod.missing_placeholders({}, {"output", "input"}), ["input", "output"])


class ValidateCases(unittest.TestCase):
    def test_no_problems_for_fully_satisfied_cases(self):
        cases = [{"id": "a", "input": "x", "output": "y"}]
        problems, invalid = run_judge_mod.validate_cases(cases, "{input} {output}")
        self.assertEqual(problems, [])
        self.assertEqual(invalid, set())

    def test_missing_field_flagged(self):
        cases = [{"id": "a", "input": "x"}]
        problems, invalid = run_judge_mod.validate_cases(cases, "{input} {output}")
        self.assertEqual(invalid, {0})
        self.assertIn("'a'", problems[0])
        self.assertIn("{output}", problems[0])

    def test_trajectory_fallback_not_flagged(self):
        cases = [{"id": "a", "input": "x", "final_output": "y"}]
        problems, invalid = run_judge_mod.validate_cases(cases, "{input} {output}")
        self.assertEqual(problems, [])
        self.assertEqual(invalid, set())

    def test_duplicate_id_flagged_second_occurrence_only(self):
        cases = [{"id": "dup", "input": "x", "output": "y"}, {"id": "dup", "input": "x2", "output": "y2"}]
        problems, invalid = run_judge_mod.validate_cases(cases, "{input} {output}")
        self.assertEqual(invalid, {1})
        self.assertTrue(any("duplicate" in p for p in problems))

    def test_case_missing_id_uses_row_placeholder_in_message(self):
        cases = [{"input": "x"}]
        problems, invalid = run_judge_mod.validate_cases(cases, "{input} {output}")
        self.assertEqual(invalid, {0})
        self.assertIn("row 0", problems[0])

    def test_mixed_valid_and_invalid_cases(self):
        cases = [
            {"id": "good", "input": "x", "output": "y"},
            {"id": "bad", "input": "x"},
        ]
        problems, invalid = run_judge_mod.validate_cases(cases, "{input} {output}")
        self.assertEqual(invalid, {1})
        self.assertEqual(len(problems), 1)


class ReportAndFilterInvalidCases(unittest.TestCase):
    def test_no_problems_returns_cases_unchanged_silently(self):
        cases = [{"id": "a", "input": "x", "output": "y"}]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = run_judge_mod.report_and_filter_invalid_cases(cases, "{input} {output}", skip_invalid=False)
        self.assertEqual(result, cases)
        self.assertEqual(err.getvalue(), "")

    def test_problems_without_skip_invalid_returns_none_and_reports(self):
        cases = [{"id": "a", "input": "x"}]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = run_judge_mod.report_and_filter_invalid_cases(cases, "{input} {output}", skip_invalid=False)
        self.assertIsNone(result)
        self.assertIn("Preflight", err.getvalue())
        self.assertIn("Aborting", err.getvalue())

    def test_problems_with_skip_invalid_filters_and_continues(self):
        cases = [{"id": "good", "input": "x", "output": "y"}, {"id": "bad", "input": "x"}]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = run_judge_mod.report_and_filter_invalid_cases(cases, "{input} {output}", skip_invalid=True)
        self.assertEqual(result, [cases[0]])
        self.assertIn("continuing with 1/2", err.getvalue())

    def test_custom_validate_fn_used_instead_of_default(self):
        def always_invalid(cases, template):
            return (["forced problem"], {0})
        with contextlib.redirect_stderr(io.StringIO()):
            result = run_judge_mod.report_and_filter_invalid_cases(
                [{"id": "a"}], "irrelevant", skip_invalid=True, validate_fn=always_invalid,
            )
        self.assertEqual(result, [])


class Cli(unittest.TestCase):
    """End-to-end: invoke the script as a subprocess (no real API key —
    just proves the ANTHROPIC_API_KEY gate and argument wiring)."""

    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def make_jsonl(self, rows):
        path = write_jsonl(rows)
        self._paths.append(path)
        return path

    def make_file(self, content):
        path = write_file(content)
        self._paths.append(path)
        return path

    def test_missing_api_key_exits_nonzero_with_clear_message(self):
        cases_path = self.make_jsonl([{"id": "a", "input": "x", "output": "y"}])
        template_path = self.make_file("{input} {output}")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        proc = subprocess.run(
            [sys.executable, SCRIPT, cases_path, "--template", template_path, "--out", "/dev/null"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("ANTHROPIC_API_KEY", proc.stderr)

    def test_gemini_provider_checks_gemini_api_key_not_anthropic(self):
        cases_path = self.make_jsonl([{"id": "a", "input": "x", "output": "y"}])
        template_path = self.make_file("{input} {output}")
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY")}
        env["ANTHROPIC_API_KEY"] = "irrelevant-should-not-be-checked"
        proc = subprocess.run(
            [sys.executable, SCRIPT, cases_path, "--template", template_path, "--out", "/dev/null", "--provider", "gemini"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("GEMINI_API_KEY", proc.stderr)

    def test_unknown_provider_rejected_by_argparse(self):
        cases_path = self.make_jsonl([{"id": "a", "input": "x", "output": "y"}])
        template_path = self.make_file("{input} {output}")
        proc = subprocess.run(
            [sys.executable, SCRIPT, cases_path, "--template", template_path, "--out", "/dev/null", "--provider", "openai"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid choice", proc.stderr)

    def test_preflight_problem_aborts_before_any_output_written(self):
        """A schema mistake (case missing a field the template needs)
        must abort before run_judge() ever runs — proven here by the out
        file never getting created, not just by the exit code, since a
        stray real network call from an un-caught path would also produce
        a nonzero exit for other reasons."""
        cases_path = self.make_jsonl([{"id": "a", "input": "x"}])  # missing "output"
        template_path = self.make_file("{input} {output}")
        out_path = os.path.join(tempfile.mkdtemp(), "results.jsonl")
        env = dict(os.environ, ANTHROPIC_API_KEY="unused-preflight-should-abort-first")
        proc = subprocess.run(
            [sys.executable, SCRIPT, cases_path, "--template", template_path, "--out", out_path],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("Preflight", proc.stderr)
        self.assertIn("{output}", proc.stderr)
        self.assertFalse(os.path.exists(out_path))

    def test_skip_invalid_with_no_valid_cases_left_exits_one_without_network_call(self):
        """Every case is invalid, so after --skip-invalid filters them out
        run_judge() is called with an empty list — exercises the flag's
        wiring end-to-end without ever needing a real judge_fn call."""
        cases_path = self.make_jsonl([{"id": "a", "input": "x"}, {"id": "b", "input": "x"}])  # both missing "output"
        template_path = self.make_file("{input} {output}")
        out_path = os.path.join(tempfile.mkdtemp(), "results.jsonl")
        env = dict(os.environ, ANTHROPIC_API_KEY="unused-no-valid-cases-so-no-call-happens")
        proc = subprocess.run(
            [sys.executable, SCRIPT, cases_path, "--template", template_path, "--out", out_path, "--skip-invalid"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("continuing with 0/2", proc.stderr)
        with open(out_path) as f:
            self.assertEqual(f.read(), "")

    def test_no_preflight_problems_produces_no_preflight_output(self):
        cases_path = self.make_jsonl([{"id": "a", "input": "x", "output": "y"}])
        template_path = self.make_file("{input} {output}")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        proc = subprocess.run(
            [sys.executable, SCRIPT, cases_path, "--template", template_path, "--out", "/dev/null"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 2)  # still hits the missing-API-key gate, but only after preflight passed
        self.assertNotIn("Preflight", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
