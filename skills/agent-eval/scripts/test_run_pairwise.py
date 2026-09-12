#!/usr/bin/env python3
"""
Unit tests for run_pairwise.py — the pairwise eval type's structural
position-bias handling, which had no tooling behind it before this script
existed (SKILL.md documented the mitigation as a single bullet point).

Covers position-based template filling, winner parsing, positional-to-
actual (a/b) remapping, full two-ordering reconciliation (agreement and
disagreement cases) against a fake judge_fn, and run_pairwise()'s
orchestration/flattening. No real network access anywhere in this file.

Stdlib only (unittest). Run: python test_run_pairwise.py"""

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
SCRIPT = os.path.join(HERE, "run_pairwise.py")

spec = importlib.util.spec_from_file_location("run_pairwise", SCRIPT)
run_pairwise_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_pairwise_mod)


def write_jsonl(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


TEMPLATE = "Task: {input}\nResponse 1: {response_1}\nResponse 2: {response_2}"


class FillPairwiseTemplate(unittest.TestCase):
    def test_a_first_ordering(self):
        case = {"id": "p1", "input": "task", "output_a": "A's answer", "output_b": "B's answer"}
        filled = run_pairwise_mod.fill_pairwise_template(TEMPLATE, case, "a", "b")
        self.assertIn("Response 1: A's answer", filled)
        self.assertIn("Response 2: B's answer", filled)

    def test_b_first_ordering(self):
        case = {"id": "p1", "input": "task", "output_a": "A's answer", "output_b": "B's answer"}
        filled = run_pairwise_mod.fill_pairwise_template(TEMPLATE, case, "b", "a")
        self.assertIn("Response 1: B's answer", filled)
        self.assertIn("Response 2: A's answer", filled)

    def test_input_field_also_substituted(self):
        case = {"id": "p1", "input": "summarize this", "output_a": "x", "output_b": "y"}
        filled = run_pairwise_mod.fill_pairwise_template(TEMPLATE, case, "a", "b")
        self.assertIn("Task: summarize this", filled)


class ParseWinner(unittest.TestCase):
    def test_response_1(self):
        winner, rationale = run_pairwise_mod._parse_winner('{"winner": "response_1", "rationale": "better"}')
        self.assertEqual(winner, "response_1")
        self.assertEqual(rationale, "better")

    def test_tie(self):
        winner, _ = run_pairwise_mod._parse_winner('{"winner": "tie", "rationale": "equal"}')
        self.assertEqual(winner, "tie")

    def test_unrecognized_winner_raises(self):
        with self.assertRaises(ValueError):
            run_pairwise_mod._parse_winner('{"winner": "response_a", "rationale": "x"}')

    def test_missing_rationale_defaults_to_empty(self):
        _, rationale = run_pairwise_mod._parse_winner('{"winner": "tie"}')
        self.assertEqual(rationale, "")


class RemapToAb(unittest.TestCase):
    def test_response_1_maps_to_whichever_is_first(self):
        self.assertEqual(run_pairwise_mod._remap_to_ab("response_1", "a", "b"), "a")
        self.assertEqual(run_pairwise_mod._remap_to_ab("response_1", "b", "a"), "b")

    def test_response_2_maps_to_whichever_is_second(self):
        self.assertEqual(run_pairwise_mod._remap_to_ab("response_2", "a", "b"), "b")
        self.assertEqual(run_pairwise_mod._remap_to_ab("response_2", "b", "a"), "a")

    def test_tie_stays_tie(self):
        self.assertEqual(run_pairwise_mod._remap_to_ab("tie", "a", "b"), "tie")


class JudgePairwise(unittest.TestCase):
    def test_consistent_orderings_agree_no_bias(self):
        # "a" wins regardless of which position it's shown in.
        def judge_fn(prompt, api_key, model):
            if "Response 1: A" in prompt:
                return {"text": json.dumps({"winner": "response_1", "rationale": "A better"}), "input_tokens": 10, "output_tokens": 5}
            else:
                return {"text": json.dumps({"winner": "response_2", "rationale": "A better"}), "input_tokens": 10, "output_tokens": 5}

        case = {"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}
        result = run_pairwise_mod.judge_pairwise(case, TEMPLATE, "fake-key", judge_fn=judge_fn)
        self.assertEqual(result["winner_ab"], "a")
        self.assertFalse(result["position_bias_detected"])

    def test_position_biased_judge_disagrees_and_is_flagged(self):
        # Judge always favors whichever is shown first — a real judge's
        # position-bias signature.
        def biased_judge_fn(prompt, api_key, model):
            return {"text": json.dumps({"winner": "response_1", "rationale": "first one seemed better"}), "input_tokens": 10, "output_tokens": 5}

        case = {"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}
        result = run_pairwise_mod.judge_pairwise(case, TEMPLATE, "fake-key", judge_fn=biased_judge_fn)
        self.assertTrue(result["position_bias_detected"])
        self.assertEqual(result["winner_ab"], "tie")
        self.assertIn("Position bias detected", result["rationale"])

    def test_tie_in_both_orderings(self):
        def tie_judge_fn(prompt, api_key, model):
            return {"text": json.dumps({"winner": "tie", "rationale": "equally good"}), "input_tokens": 10, "output_tokens": 5}

        case = {"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}
        result = run_pairwise_mod.judge_pairwise(case, TEMPLATE, "fake-key", judge_fn=tie_judge_fn)
        self.assertEqual(result["winner_ab"], "tie")
        self.assertFalse(result["position_bias_detected"])

    def test_token_counts_summed_across_both_calls(self):
        def judge_fn(prompt, api_key, model):
            return {"text": json.dumps({"winner": "tie", "rationale": "x"}), "input_tokens": 10, "output_tokens": 5}

        case = {"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}
        result = run_pairwise_mod.judge_pairwise(case, TEMPLATE, "fake-key", judge_fn=judge_fn)
        self.assertEqual(result["input_tokens"], 20)
        self.assertEqual(result["output_tokens"], 10)


class RunPairwise(unittest.TestCase):
    def _agree_a_wins(self, prompt, api_key, model):
        if "Response 1: A" in prompt:
            return {"text": json.dumps({"winner": "response_1", "rationale": "A better"}), "input_tokens": 10, "output_tokens": 5}
        return {"text": json.dumps({"winner": "response_2", "rationale": "A better"}), "input_tokens": 10, "output_tokens": 5}

    def test_full_wiring_produces_flattened_row(self):
        cases = [{"id": "p1", "input": "t", "output_a": "A", "output_b": "B", "category": "accuracy"}]
        results = run_pairwise_mod.run_pairwise(cases, TEMPLATE, "fake-key", judge_fn=self._agree_a_wins)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "p1")
        self.assertEqual(results[0]["score"], 1.0)
        self.assertEqual(results[0]["category"], "accuracy")
        self.assertFalse(results[0]["position_bias_detected"])

    def test_b_wins_scores_zero(self):
        def b_wins(prompt, api_key, model):
            if "Response 1: B" in prompt:
                return {"text": json.dumps({"winner": "response_1", "rationale": "B better"}), "input_tokens": 10, "output_tokens": 5}
            return {"text": json.dumps({"winner": "response_2", "rationale": "B better"}), "input_tokens": 10, "output_tokens": 5}

        cases = [{"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}]
        results = run_pairwise_mod.run_pairwise(cases, TEMPLATE, "fake-key", judge_fn=b_wins)
        self.assertEqual(results[0]["score"], 0.0)

    def test_biased_disagreement_scores_as_tie_with_flag(self):
        def biased(prompt, api_key, model):
            return {"text": json.dumps({"winner": "response_1", "rationale": "first"}), "input_tokens": 10, "output_tokens": 5}

        cases = [{"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}]
        results = run_pairwise_mod.run_pairwise(cases, TEMPLATE, "fake-key", judge_fn=biased)
        self.assertEqual(results[0]["score"], 0.5)
        self.assertTrue(results[0]["position_bias_detected"])

    def test_cost_computed_only_when_both_prices_given(self):
        cases = [{"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}]
        results = run_pairwise_mod.run_pairwise(
            cases, TEMPLATE, "fake-key", judge_fn=self._agree_a_wins,
            input_price_per_mtok=3.0, output_price_per_mtok=15.0,
        )
        # 20 input tokens total (10 per ordering) * $3/1M + 10 output * $15/1M
        expected = (20 / 1_000_000) * 3.0 + (10 / 1_000_000) * 15.0
        self.assertAlmostEqual(results[0]["cost_usd"], expected)

    def test_failure_in_either_ordering_skips_case_without_crashing(self):
        def flaky(prompt, api_key, model):
            raise ConnectionError("down")

        cases = [{"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            results = run_pairwise_mod.run_pairwise(cases, TEMPLATE, "fake-key", judge_fn=flaky)
        self.assertEqual(results, [])
        self.assertIn("pairwise judging failed", err.getvalue())

    def test_one_bad_case_does_not_block_the_rest(self):
        def mostly_fine(prompt, api_key, model):
            if "BADCASE" in prompt:
                raise ValueError("bad response")
            return self._agree_a_wins(prompt, api_key, model)

        cases = [
            {"id": "bad", "input": "BADCASE", "output_a": "A", "output_b": "B"},
            {"id": "good", "input": "t", "output_a": "A", "output_b": "B"},
        ]
        with contextlib.redirect_stderr(io.StringIO()):
            results = run_pairwise_mod.run_pairwise(cases, TEMPLATE, "fake-key", judge_fn=mostly_fine)
        self.assertEqual([r["id"] for r in results], ["good"])


class Cli(unittest.TestCase):
    def setUp(self):
        self._paths = []

    def tearDown(self):
        for p in self._paths:
            os.unlink(p)

    def test_missing_api_key_exits_nonzero(self):
        cases_path = write_jsonl([{"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}])
        self._paths.append(cases_path)
        fd, template_path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(TEMPLATE)
        self._paths.append(template_path)

        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        proc = subprocess.run(
            [sys.executable, SCRIPT, cases_path, "--template", template_path, "--out", "/dev/null"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("ANTHROPIC_API_KEY", proc.stderr)

    def test_gemini_provider_checks_gemini_api_key_not_anthropic(self):
        cases_path = write_jsonl([{"id": "p1", "input": "t", "output_a": "A", "output_b": "B"}])
        self._paths.append(cases_path)
        fd, template_path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(TEMPLATE)
        self._paths.append(template_path)

        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY")}
        env["ANTHROPIC_API_KEY"] = "irrelevant-should-not-be-checked"
        proc = subprocess.run(
            [sys.executable, SCRIPT, cases_path, "--template", template_path, "--out", "/dev/null", "--provider", "gemini"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("GEMINI_API_KEY", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
