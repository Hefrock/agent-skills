#!/usr/bin/env python3
"""
run_judge.py - Call an LLM-as-judge and flatten its response into
score_eval.py's schema.

This is the step SKILL.md documents in prose (fill the judge-prompt
template, request structured JSON, flatten the nested per-criterion
response to one line per case) but that, before this script existed, had
zero shared code and zero tests — unlike score_eval.py, which aggregates
the *result* of this step and has both. That asymmetry is the actual gap:
the fiddlier, more-repeated-every-run half of the pipeline (call the
judge, parse its JSON, pick the lowest-scoring criterion's rationale) was
redone by hand each time; the easier half (sum up already-flattened rows)
was fully productized.

Usage:
    export ANTHROPIC_API_KEY=...
    python run_judge.py cases.jsonl --template judge_prompt.txt --out results.jsonl
    python run_judge.py cases.jsonl --template judge_prompt.txt --out results.jsonl --category accuracy
    python run_judge.py cases.jsonl --template judge_prompt.txt --out results.jsonl \\
        --input-price-per-mtok 3.00 --output-price-per-mtok 15.00

Case input format (JSONL, one JSON object per line) — one row per case to
grade:
    {"id": "case_001", "input": "...", "output": "...", "category": "accuracy"}
    {"id": "traj_001", "input": "...", "trajectory": [...], "final_output": "...", "category": "trajectory"}
    {"id": "mt_001", "turns": [{"role": "user", "content": "..."}, ...], "input": "...", "category": "jailbreak"}

`category` is optional per-row (falls back to --category, then omitted
entirely — score_eval.py itself already treats a missing category as
"uncategorized", so this script doesn't need to duplicate that default).

Template format: a text file with the judge prompt, using {input}/{output}
(and {trajectory}, for a trajectory-shaped case per references/
trajectory-eval.md, or {transcript}, for a multi-turn case per references/
multi-turn-eval.md) as literal placeholder tokens — see
references/llm-judge-prompt.md for the starting template these come from.
Substitution is plain string replacement, not str.format(), because the
template's own JSON-shaped output instructions are full of unrelated {}
braces that str.format() would choke on.

The judge's response must be the structured JSON shape SKILL.md and both
reference docs already specify: one or more named criteria, each an
object with a "score" and a "rationale", plus a top-level "overall_score".
This script does not care whether those criterion names are rubric-style
("factual_accuracy") or trajectory-style ("tool_selection") — it treats
"any key whose value is a {"score": ..., "rationale": ...} dict" as a
criterion, generically, which is what lets one script serve both
documented case shapes instead of hardcoding two modes.

Real per-case failures (a network error, a judge response that isn't
valid JSON, a response missing "overall_score") are reported to stderr
and that case is skipped — same "never fabricate a score for a case that
didn't actually get graded" discipline score_eval.load_results() already
applies to its own malformed-line handling, not a new convention.

Cost (cost_usd) is only ever computed from real token counts the API
response reports, multiplied by prices the caller supplies explicitly
(--input-price-per-mtok/--output-price-per-mtok) — never a baked-in
pricing table that would silently go stale. Omitted entirely if prices
aren't given.

Stdlib only (urllib), matching this repo's other reference tooling."""

import argparse
import json
import os
import sys
import time
import urllib.request

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_ANTHROPIC_VERSION = "2023-06-01"


def load_cases(path: str) -> list[dict]:
    """Same discipline as score_eval.load_results(): skip a blank or
    malformed line with a warning rather than crashing the whole batch,
    and require an "id" (nothing downstream is meaningful without one)."""
    cases = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: skipping malformed line {lineno} in {path}: {e}", file=sys.stderr)
                continue
            if "id" not in obj:
                print(f"Warning: skipping line {lineno} in {path} — missing 'id'", file=sys.stderr)
                continue
            cases.append(obj)
    return cases


def format_turns_as_transcript(turns: list[dict]) -> str:
    """Renders a multi-turn case's turns (see references/multi-turn-eval.md
    for the case shape) as a readable "Role: content" dialogue transcript,
    one line per turn. fill_template() could substitute a "turns" list as
    raw pretty-printed JSON like any other non-string field — functional,
    but a judge reads a real conversation far more reliably as a
    transcript than as a JSON array of {"role", "content"} objects, the
    same reason references/trajectory-eval.md's case shape is a real
    step-by-step structure rather than a flattened blob."""
    return "\n".join(f"{turn['role'].capitalize()}: {turn['content']}" for turn in turns)


def fill_template(template: str, case: dict) -> str:
    """Plain string replacement of {key} tokens for every key actually
    present in the case, not str.format() — the template's own JSON-
    shaped output instructions are full of {} braces str.format() would
    try (and fail) to treat as fields. A non-string value (e.g.
    "trajectory"'s list of steps) is pretty-printed as JSON so the judge
    reads a real structured trajectory, not Python's repr of a list."""
    filled = template
    for key, value in case.items():
        token = "{" + key + "}"
        if token not in filled:
            continue
        text = value if isinstance(value, str) else json.dumps(value, indent=2)
        filled = filled.replace(token, text)
    # Trajectory cases document their answer field as "final_output" (see
    # references/trajectory-eval.md's case shape), but the shared template
    # placeholder is named {output} (references/llm-judge-prompt.md) — one
    # template serves both documented case shapes without forcing every
    # trajectory case to also carry a redundant "output" key.
    if "{output}" in filled and "output" not in case and "final_output" in case:
        filled = filled.replace("{output}", case["final_output"])
    # Multi-turn cases (references/multi-turn-eval.md) carry "turns" as
    # structured {"role", "content"} data, not a preformatted string — the
    # loop above would substitute a raw JSON dump into a template's
    # {turns} token if one were used, but the documented placeholder is
    # {transcript}, rendered on demand so every multi-turn case doesn't
    # need to precompute and store its own transcript string.
    if "{transcript}" in filled and "turns" in case:
        filled = filled.replace("{transcript}", format_turns_as_transcript(case["turns"]))
    return filled


def _extract_json(text: str) -> dict:
    """Judges are instructed to respond with JSON only, but real models
    sometimes wrap it in a ```json ... ``` fence anyway — strip one if
    present before parsing, rather than failing a case over formatting
    the prompt already told the model not to use."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return json.loads(stripped)


def flatten_judge_response(response_json: dict) -> dict:
    """response_json is the judge's parsed JSON: one or more named
    criteria (each {"score": float, "rationale": str}) plus a top-level
    "overall_score". Generic over criterion names on purpose — this is
    what lets one function serve both the plain rubric shape (arbitrary
    names like "factual_accuracy") and the trajectory shape ("tool_
    selection", "argument_correctness", ...) from references/trajectory-
    eval.md without hardcoding either one.

    Returns {"score": overall_score, "rationale": <lowest-scoring
    criterion's rationale>} — SKILL.md's own documented convention ("For
    a multi-criterion judge response, use the rationale from the lowest-
    scoring criterion, since that's the one explaining the failure").

    Raises KeyError/TypeError/ValueError on a response missing
    "overall_score" or shaped unexpectedly — the caller treats any of
    these as "this case didn't get graded, skip it," not a crash."""
    overall_score = float(response_json["overall_score"])
    criteria = {
        name: value for name, value in response_json.items()
        if name != "overall_score" and isinstance(value, dict) and "score" in value
    }
    rationale = ""
    if criteria:
        lowest = min(criteria.values(), key=lambda c: c["score"])
        rationale = lowest.get("rationale", "")
    return {"score": overall_score, "rationale": rationale}


def call_judge(prompt: str, api_key: str, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = 60.0) -> dict:
    """The one function in this module that touches the network — kept
    separate so run_judge() below can be unit-tested against a fake
    without ever making a real call, same split as this repo's other
    reference tooling (audio_synth.synthesize_text, dedup_store.embed_text).

    Returns {"text": <raw response text>, "input_tokens": int,
    "output_tokens": int} — real counts from the API's own "usage" field,
    not estimated, since cost_usd downstream is only ever computed from
    real numbers."""
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        DEFAULT_API_URL, data=payload, method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": DEFAULT_ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    text = "".join(block.get("text", "") for block in body.get("content", []) if block.get("type") == "text")
    usage = body.get("usage", {})
    return {"text": text, "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}


def run_judge(
    cases: list[dict],
    template: str,
    api_key: str,
    judge_fn=call_judge,
    model: str = DEFAULT_MODEL,
    default_category: str | None = None,
    input_price_per_mtok: float | None = None,
    output_price_per_mtok: float | None = None,
) -> list[dict]:
    """Pure orchestration over judge_fn — no direct network access here,
    so this is fully testable with a fake judge_fn standing in for a real
    API call, same convention as orchestrate.run_episode()'s injected
    fetch_fn/embed_fn/synth_fn.

    A per-case failure (judge_fn raising, or the response not parsing
    into flatten_judge_response()'s expected shape) is reported to
    stderr and that case is dropped from the returned list — it never
    appears with a fabricated score. cost_usd is included only when both
    price arguments are given; latency_ms is always real wall-clock time
    around the judge_fn call, whether or not pricing is known."""
    results = []
    for case in cases:
        prompt = fill_template(template, case)
        start = time.perf_counter()
        try:
            response = judge_fn(prompt, api_key, model)
        except Exception as e:
            print(f"Warning: skipping case {case['id']} — judge call failed: {e}", file=sys.stderr)
            continue
        latency_ms = (time.perf_counter() - start) * 1000

        try:
            parsed = _extract_json(response["text"])
            flattened = flatten_judge_response(parsed)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            print(f"Warning: skipping case {case['id']} — couldn't parse judge response: {e}", file=sys.stderr)
            continue

        row = {
            "id": case["id"],
            "score": flattened["score"],
            "rationale": flattened["rationale"],
            "latency_ms": round(latency_ms, 1),
        }
        category = case.get("category", default_category)
        if category is not None:
            row["category"] = category
        if input_price_per_mtok is not None and output_price_per_mtok is not None:
            cost = (response["input_tokens"] / 1_000_000) * input_price_per_mtok + (response["output_tokens"] / 1_000_000) * output_price_per_mtok
            row["cost_usd"] = round(cost, 6)
        results.append(row)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", help="Path to a JSONL file of cases to grade (one object per line: id, input, output, ...).")
    parser.add_argument("--template", required=True, help="Path to a judge-prompt template (see references/llm-judge-prompt.md).")
    parser.add_argument("--out", required=True, help="Path to write flattened results, in score_eval.py's JSONL schema.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Judge model (default: {DEFAULT_MODEL}).")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help=f"Max tokens for the judge's response (default: {DEFAULT_MAX_TOKENS}).")
    parser.add_argument("--category", help="Default category for cases that don't carry their own 'category' field.")
    parser.add_argument("--input-price-per-mtok", type=float, help="USD per 1M input tokens — set both prices to get cost_usd in the output.")
    parser.add_argument("--output-price-per-mtok", type=float, help="USD per 1M output tokens — see --input-price-per-mtok.")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY must be set.", file=sys.stderr)
        return 2

    cases = load_cases(args.cases)
    with open(args.template, encoding="utf-8") as f:
        template = f.read()

    results = run_judge(
        cases, template, api_key, model=args.model, default_category=args.category,
        input_price_per_mtok=args.input_price_per_mtok, output_price_per_mtok=args.output_price_per_mtok,
    )

    with open(args.out, "w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")

    print(f"Graded {len(results)}/{len(cases)} case(s) -> {args.out}")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
