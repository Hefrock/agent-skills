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

Preflight: before any judge call, every case is checked against the
template's own {placeholder} tokens (and for duplicate ids) — a systemic
schema mistake (wrong template, typo'd field name across the whole file)
gets reported as one upfront summary and aborts (exit 2) instead of
surfacing as a string of quiet per-case skips after budget on the
still-valid cases has already been spent. --skip-invalid grades just the
valid subset instead of aborting.

Provider: --provider selects which judge API to call (default:
anthropic; gemini also supported — see call_judge()'s docstring for why
gemini's cost tracking specifically carries a lower-confidence caveat
than anthropic's). Not every agent using this repo has an Anthropic key
— this repo's own README describes skills as "usable across Claude,
Codex, Gemini CLI, Cursor, and GitHub Copilot," and a judge tool that
only worked with one vendor's API sat oddly against that.

Stdlib only (urllib), matching this repo's other reference tooling."""

import argparse
import functools
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jsonl_io  # noqa: E402

DEFAULT_MODEL = "claude-sonnet-5"  # anthropic's default specifically — see PROVIDERS for gemini's
DEFAULT_MAX_TOKENS = 1024
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# One entry per supported --provider. Adding a new provider means adding
# a _call_<name>() function below, an entry here, and a line in
# _PROVIDER_CALLERS — nothing else in this module (fill_template,
# flatten_judge_response, run_judge()'s orchestration) needs to change,
# since all of it only ever depends on judge_fn's shared (prompt, api_key,
# model) -> {"text", "input_tokens", "output_tokens"} contract.
PROVIDERS = {
    "anthropic": {"default_model": "claude-sonnet-5", "api_key_env": "ANTHROPIC_API_KEY"},
    "gemini": {"default_model": "gemini-3.5-flash", "api_key_env": "GEMINI_API_KEY"},
}


def load_cases(path: str) -> list[dict]:
    """Requires an "id" per case (nothing downstream is meaningful
    without one) — thin wrapper over jsonl_io.load_jsonl(), the shared
    primitive score_eval.load_results() and calibrate_judge.
    load_scores_by_id() also build on."""
    return jsonl_io.load_jsonl(path, required_keys=("id",))


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


PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def find_template_placeholders(template: str) -> set[str]:
    """Every {word}-shaped token in the template that fill_template()
    would treat as a substitutable placeholder. Deliberately narrow — a
    bare identifier between braces with nothing else — so it doesn't
    false-match the judge's own JSON-output-format example (e.g.
    {"criterion_1_name": {"score": 0.0, "rationale": "..."}}), where every
    brace pair has a quote or colon inside it, never a lone identifier."""
    return set(PLACEHOLDER_RE.findall(template))


def missing_placeholders(case: dict, placeholders: set[str]) -> list[str]:
    """Which of `placeholders` this case can't actually satisfy — honoring
    the exact fallbacks fill_template() applies (output falls back to
    final_output, transcript falls back to turns) so this never flags a
    case fill_template() would have filled correctly. Shared by
    run_judge.py's own preflight (validate_cases()) and run_pairwise.py's,
    which reuses this for the plain-substitution placeholders a pairwise
    template can still carry (e.g. {input}) on top of its own output_a/
    output_b check."""
    missing = []
    for placeholder in sorted(placeholders):
        if placeholder in case:
            continue
        if placeholder == "output" and "final_output" in case:
            continue
        if placeholder == "transcript" and "turns" in case:
            continue
        missing.append(placeholder)
    return missing


def validate_cases(cases: list[dict], template: str) -> tuple[list[str], set[int]]:
    """Preflight check over the *whole* cases list, run once before any
    judge call — not the per-case rejection run_judge() already does
    during the run, which only ever sees one case at a time and can't
    notice a duplicate id against a case seen earlier, or give one upfront
    summary instead of N one-at-a-time stderr warnings. Addresses the
    real risk PR #99's batch-crash fix left behind: a systemic mistake
    (case file built against the wrong template, a typo'd field name
    across the whole set) now surfaces as a string of quiet per-case
    skips *after* judge-call budget has already been spent on whichever
    cases happened to still fill correctly, rather than one loud report
    before the first call.

    Returns (problems, invalid_indices): `problems` is one human-readable
    string per issue (for reporting to the user); `invalid_indices` is
    the set of positions in `cases` that --skip-invalid would drop before
    calling run_judge(). A case lands in `invalid_indices` for one of two
    independent reasons: it can't satisfy every {placeholder} the
    template declares (see missing_placeholders()), or it repeats an
    earlier case's id — score_eval.py and calibrate_judge.py both key
    results by id, so a duplicate makes downstream aggregation ambiguous;
    the first occurrence is kept, later ones are what's marked invalid."""
    placeholders = find_template_placeholders(template)
    problems = []
    invalid: set[int] = set()
    seen_ids: dict = {}
    for i, case in enumerate(cases):
        case_id = case.get("id", f"<row {i}>")
        missing = missing_placeholders(case, placeholders)
        if missing:
            wanted = ", ".join("{" + m + "}" for m in missing)
            problems.append(f"case {case_id!r} (row {i}): template needs {wanted}, which this case doesn't supply")
            invalid.add(i)
        if case_id in seen_ids:
            problems.append(f"case {case_id!r} (row {i}): duplicate of row {seen_ids[case_id]} — ids must be unique for score_eval.py's aggregation to be meaningful")
            invalid.add(i)
        else:
            seen_ids[case_id] = i
    return problems, invalid


def report_and_filter_invalid_cases(cases: list[dict], template: str, skip_invalid: bool, validate_fn=validate_cases) -> "list[dict] | None":
    """Shared CLI-level preflight wiring for both run_judge.py's and
    run_pairwise.py's main(): print every problem `validate_fn` finds,
    then either abort (returns None — caller should exit(2) without
    spending any judge-call budget) or drop the invalid cases and
    continue (returns the filtered list). Kept out of validate_cases()
    itself so that function stays pure and testable without capturing
    stdout/stderr. run_pairwise.py passes validate_fn=validate_pairwise_
    cases to reuse this same reporting/filtering logic for its own case
    shape rather than duplicating it."""
    problems, invalid_indices = validate_fn(cases, template)
    if not problems:
        return cases
    print(f"Preflight: {len(problems)} problem(s) found in {len(cases)} case(s), before any judge call was made:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    if not skip_invalid:
        print("\nAborting without spending judge-call budget. Fix the case file, or pass --skip-invalid to grade only the valid cases.", file=sys.stderr)
        return None
    filtered = [c for i, c in enumerate(cases) if i not in invalid_indices]
    print(f"--skip-invalid: continuing with {len(filtered)}/{len(cases)} valid case(s).", file=sys.stderr)
    return filtered


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


def _call_anthropic(prompt: str, api_key: str, model: str, max_tokens: int, timeout: float) -> dict:
    """Anthropic's Messages API. See call_judge()'s docstring for the
    shared return shape every provider function here returns."""
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        ANTHROPIC_API_URL, data=payload, method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    text = "".join(block.get("text", "") for block in body.get("content", []) if block.get("type") == "text")
    usage = body.get("usage", {})
    return {"text": text, "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}


def _call_gemini(prompt: str, api_key: str, model: str, max_tokens: int, timeout: float) -> dict:
    """Gemini's generateContent endpoint. The URL pattern, the contents/
    parts request wrapping, and the candidates[0].content.parts[0].text
    response extraction all mirror broadcast/scripts/narrate.py's
    generate_narration() — this repo's own live-confirmed precedent for
    calling this exact endpoint for text generation (see that function's
    own docstring: "Prompt and response shape confirmed live via
    _diagnose_narration.py's reconnaissance"), not a shape invented for
    this module.

    One real gap this does NOT share that precedent for: token usage.
    Gemini's documented API contract includes a "usageMetadata" object
    (promptTokenCount/candidatesTokenCount) on generateContent responses,
    but nothing in this repo has independently live-verified that field
    the way narrate.py's response-text path was. Read defensively
    (.get(..., 0)) so a missing or differently-named field degrades to
    an untracked (0) token count rather than a crash — but that also
    means cost_usd computed from a gemini call should be treated as
    lower-confidence than an anthropic call's, until someone actually
    confirms this field live."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    text = body["candidates"][0]["content"]["parts"][0]["text"]
    usage = body.get("usageMetadata", {})
    return {"text": text, "input_tokens": usage.get("promptTokenCount", 0), "output_tokens": usage.get("candidatesTokenCount", 0)}


_PROVIDER_CALLERS = {"anthropic": _call_anthropic, "gemini": _call_gemini}


def call_judge(prompt: str, api_key: str, model: str = DEFAULT_MODEL, provider: str = "anthropic", max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = 60.0) -> dict:
    """The one function in this module that touches the network — kept
    separate so run_judge() below can be unit-tested against a fake
    without ever making a real call, same split as this repo's other
    reference tooling (audio_synth.synthesize_text, dedup_store.embed_text).

    provider dispatches to _call_anthropic()/_call_gemini() — defaults to
    "anthropic" so every existing call to call_judge(prompt, api_key,
    model) (the shape judge_fn is always invoked with throughout this
    module and run_pairwise.py) keeps working completely unchanged; a
    caller that wants a different provider passes it via functools.
    partial(call_judge, provider="gemini") as their judge_fn, not by
    changing the call site's own 3-positional-argument shape.

    Returns {"text": <raw response text>, "input_tokens": int,
    "output_tokens": int} — real counts from the API's own usage field,
    not estimated, since cost_usd downstream is only ever computed from
    real numbers. Raises ValueError for an unrecognized provider, same
    "fail loud on a config mistake, fail soft on a data/network problem"
    split the rest of this module follows."""
    if provider not in _PROVIDER_CALLERS:
        raise ValueError(f"Unknown provider {provider!r} — choose one of {sorted(_PROVIDER_CALLERS)}")
    return _PROVIDER_CALLERS[provider](prompt, api_key, model, max_tokens, timeout)


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

    A per-case failure — template-filling (e.g. a malformed "turns" entry
    missing "role"/"content", see format_turns_as_transcript()), judge_fn
    raising, or the response not parsing into flatten_judge_response()'s
    expected shape — is reported to stderr and that case is dropped from
    the returned list. Real, live-found bug this guards against: an
    earlier version of this function called fill_template() outside any
    try/except, so one malformed case crashed the entire batch and
    silently discarded every result already computed for cases before
    it — not just that one case, the way every other failure mode here
    is handled. cost_usd is included only when both price arguments are
    given; latency_ms is always real wall-clock time around the judge_fn
    call, whether or not pricing is known."""
    results = []
    for case in cases:
        try:
            prompt = fill_template(template, case)
        except Exception as e:
            print(f"Warning: skipping case {case['id']} — couldn't fill template: {e}", file=sys.stderr)
            continue

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
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="anthropic", help="Which judge API to call (default: anthropic).")
    parser.add_argument("--model", default=None, help="Judge model (default: the chosen --provider's own default model).")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help=f"Max tokens for the judge's response (default: {DEFAULT_MAX_TOKENS}).")
    parser.add_argument("--category", help="Default category for cases that don't carry their own 'category' field.")
    parser.add_argument("--input-price-per-mtok", type=float, help="USD per 1M input tokens — set both prices to get cost_usd in the output.")
    parser.add_argument("--output-price-per-mtok", type=float, help="USD per 1M output tokens — see --input-price-per-mtok.")
    parser.add_argument("--skip-invalid", action="store_true", help="Grade only cases that pass preflight validation instead of aborting when problems are found.")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    with open(args.template, encoding="utf-8") as f:
        template = f.read()

    # Preflight runs before the API-key check on purpose: validating the
    # case file's structure needs no credentials, and there's no reason to
    # make the user set one up just to find out their case file doesn't
    # match their template.
    cases = report_and_filter_invalid_cases(cases, template, args.skip_invalid)
    if cases is None:
        return 2

    api_key_env = PROVIDERS[args.provider]["api_key_env"]
    api_key = os.environ.get(api_key_env)
    if not api_key:
        print(f"{api_key_env} must be set (--provider {args.provider}).", file=sys.stderr)
        return 2
    model = args.model or PROVIDERS[args.provider]["default_model"]

    judge_fn = functools.partial(call_judge, provider=args.provider, max_tokens=args.max_tokens)
    results = run_judge(
        cases, template, api_key, judge_fn=judge_fn, model=model, default_category=args.category,
        input_price_per_mtok=args.input_price_per_mtok, output_price_per_mtok=args.output_price_per_mtok,
    )

    with open(args.out, "w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row) + "\n")

    print(f"Graded {len(results)}/{len(cases)} case(s) -> {args.out}")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
