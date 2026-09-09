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


if __name__ == "__main__":
    unittest.main(verbosity=2)
