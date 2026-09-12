#!/usr/bin/env python3
"""
run_pairwise.py - Run a pairwise LLM-as-judge comparison with structural
position-bias handling, and flatten the result into score_eval.py's schema.

SKILL.md names pairwise comparison as one of five eval types and states
the one real pitfall it has ("watch for position bias — always run both
orderings and average") — but until this script existed, that mitigation
was prose only. Every other eval type documented in this skill has real
tooling behind it (score_eval.py for aggregation, run_judge.py for rubric/
trajectory grading); pairwise had a single bullet point and nothing to
actually run both orderings, so "always run both orderings" was something
a future eval either remembered to do by hand or, more likely, skipped.

This runs the comparison twice per case — once with each output in each
position — reconciles the two positional answers back to which actual
output won, and reports disagreement between orderings explicitly rather
than quietly averaging it away. See references/pairwise-comparison.md for
the full rationale, the prompt template, and the scoring convention.

Usage:
    export ANTHROPIC_API_KEY=...
    python run_pairwise.py cases.jsonl --template pairwise_prompt.txt --out results.jsonl
    python run_pairwise.py cases.jsonl --template pairwise_prompt.txt --out results.jsonl --provider gemini

Case input format (JSONL): {"id": "pair_001", "input": "...", "output_a": "...", "output_b": "...", "category": "accuracy"}

Reuses run_judge.py's load_cases/call_judge/_extract_json/PROVIDERS
rather than duplicating them — same "don't let two copies of the same
logic drift apart" precedent this repo already follows elsewhere (see,
e.g., distribute.py's docstring on not reimplementing wiki-operator's
vault writes). --provider (default: anthropic) selects which judge API
each of the two orderings' calls goes through — see run_judge.py's own
docstring for why this exists and call_judge()'s for the per-provider
caveats (gemini's cost tracking specifically is lower-confidence).

Stdlib only. Run: python run_pairwise.py ..."""

import argparse
import functools
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_judge  # noqa: E402

VALID_WINNERS = ("response_1", "response_2", "tie")


def fill_pairwise_template(template: str, case: dict, first: str, second: str) -> str:
    """first/second are "a" or "b" — which underlying output is shown as
    Response 1 vs Response 2 for this particular ordering. Reuses
    run_judge.fill_template for the {input} substitution (and anything
    else the case carries), then separately fills {response_1}/
    {response_2} from whichever of output_a/output_b this ordering
    assigns to each position — a case never carries literal "response_1"/
    "response_2" keys itself, so run_judge.fill_template alone can't do
    this half."""
    filled = run_judge.fill_template(template, case)
    filled = filled.replace("{response_1}", case[f"output_{first}"])
    filled = filled.replace("{response_2}", case[f"output_{second}"])
    return filled


def _parse_winner(response_text: str) -> str:
    """Returns "response_1", "response_2", or "tie". Raises ValueError on
    anything else — treated by the caller as "this ordering didn't
    produce a usable answer," same as run_judge.py's own per-case failure
    handling, not something to guess a default for."""
    parsed = run_judge._extract_json(response_text)
    winner = parsed.get("winner")
    if winner not in VALID_WINNERS:
        raise ValueError(f"judge returned an unrecognized 'winner': {winner!r}")
    return winner, parsed.get("rationale", "")


def _remap_to_ab(winner: str, first: str, second: str) -> str:
    """winner is positional ("response_1"/"response_2"/"tie"); first/
    second say which of "a"/"b" occupied each position for this
    ordering. Returns "a", "b", or "tie" — the actual output that won,
    independent of which position it happened to be shown in."""
    if winner == "tie":
        return "tie"
    return first if winner == "response_1" else second


def judge_pairwise(case: dict, template: str, api_key: str, judge_fn=run_judge.call_judge, model: str = run_judge.DEFAULT_MODEL) -> dict:
    """Runs both orderings for one case and reconciles them. Returns
    {"winner_ab": "a"|"b"|"tie", "position_bias_detected": bool,
    "rationale": str, "input_tokens": int, "output_tokens": int,
    "latency_ms": float} — a case-level result, not yet flattened to
    score_eval.py's schema (that's run_pairwise()'s job, same split as
    run_judge.flatten_judge_response() vs run_judge.run_judge())."""
    start = time.perf_counter()

    prompt_1 = fill_pairwise_template(template, case, "a", "b")
    response_1 = judge_fn(prompt_1, api_key, model)
    winner_1, rationale_1 = _parse_winner(response_1["text"])
    winner_1_ab = _remap_to_ab(winner_1, "a", "b")

    prompt_2 = fill_pairwise_template(template, case, "b", "a")
    response_2 = judge_fn(prompt_2, api_key, model)
    winner_2, rationale_2 = _parse_winner(response_2["text"])
    winner_2_ab = _remap_to_ab(winner_2, "b", "a")

    latency_ms = (time.perf_counter() - start) * 1000
    position_bias_detected = winner_1_ab != winner_2_ab

    if position_bias_detected:
        rationale = (
            f"Position bias detected — orderings disagreed (favored {winner_1_ab} first, {winner_2_ab} second). "
            f"Ordering 1: {rationale_1} | Ordering 2: {rationale_2}"
        )
        winner_ab = "tie"  # no reliable signal either way — see references/pairwise-comparison.md
    else:
        rationale = rationale_1 or rationale_2
        winner_ab = winner_1_ab

    return {
        "winner_ab": winner_ab,
        "position_bias_detected": position_bias_detected,
        "rationale": rationale,
        "input_tokens": response_1["input_tokens"] + response_2["input_tokens"],
        "output_tokens": response_1["output_tokens"] + response_2["output_tokens"],
        "latency_ms": latency_ms,
    }


def run_pairwise(
    cases: list[dict],
    template: str,
    api_key: str,
    judge_fn=run_judge.call_judge,
    model: str = run_judge.DEFAULT_MODEL,
    default_category: str | None = None,
    input_price_per_mtok: float | None = None,
    output_price_per_mtok: float | None = None,
) -> list[dict]:
    """Pure orchestration over judge_fn, same testability convention as
    run_judge.run_judge() — no direct network access here. A per-case
    failure (either ordering's judge_fn raising, or either response not
    parsing to a recognized winner) is reported to stderr and that case
    is dropped, never given a fabricated result."""
    winner_to_score = {"a": 1.0, "b": 0.0, "tie": 0.5}
    results = []
    for case in cases:
        try:
            outcome = judge_pairwise(case, template, api_key, judge_fn=judge_fn, model=model)
        except Exception as e:
            print(f"Warning: skipping case {case['id']} — pairwise judging failed: {e}", file=sys.stderr)
            continue

        row = {
            "id": case["id"],
            "score": winner_to_score[outcome["winner_ab"]],
            "rationale": outcome["rationale"],
            "position_bias_detected": outcome["position_bias_detected"],
            "latency_ms": round(outcome["latency_ms"], 1),
        }
        category = case.get("category", default_category)
        if category is not None:
            row["category"] = category
        if input_price_per_mtok is not None and output_price_per_mtok is not None:
            cost = (outcome["input_tokens"] / 1_000_000) * input_price_per_mtok + (outcome["output_tokens"] / 1_000_000) * output_price_per_mtok
            row["cost_usd"] = round(cost, 6)
        results.append(row)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", help="Path to a JSONL file of cases (id, input, output_a, output_b, ...).")
    parser.add_argument("--template", required=True, help="Path to a pairwise prompt template (see references/pairwise-comparison.md).")
    parser.add_argument("--out", required=True, help="Path to write flattened results, in score_eval.py's JSONL schema.")
    parser.add_argument("--provider", choices=sorted(run_judge.PROVIDERS), default="anthropic", help="Which judge API to call (default: anthropic).")
    parser.add_argument("--model", default=None, help="Judge model (default: the chosen --provider's own default model).")
    parser.add_argument("--category", help="Default category for cases that don't carry their own 'category' field.")
    parser.add_argument("--input-price-per-mtok", type=float, help="USD per 1M input tokens — set both prices to get cost_usd in the output.")
    parser.add_argument("--output-price-per-mtok", type=float, help="USD per 1M output tokens — see --input-price-per-mtok.")
    args = parser.parse_args()

    api_key_env = run_judge.PROVIDERS[args.provider]["api_key_env"]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        print(f"{api_key_env} must be set (--provider {args.provider}).", file=sys.stderr)
        return 2
    model = args.model or run_judge.PROVIDERS[args.provider]["default_model"]

    cases = run_judge.load_cases(args.cases)
    with open(args.template, encoding="utf-8") as f:
        template = f.read()

    judge_fn = functools.partial(run_judge.call_judge, provider=args.provider)
    results = run_pairwise(
        cases, template, api_key, judge_fn=judge_fn, model=model, default_category=args.category,
        input_price_per_mtok=args.input_price_per_mtok, output_price_per_mtok=args.output_price_per_mtok,
    )

    with open(args.out, "w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")

    bias_count = sum(1 for r in results if r.get("position_bias_detected"))
    print(f"Judged {len(results)}/{len(cases)} case(s) -> {args.out} ({bias_count} position-bias disagreement(s))")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
