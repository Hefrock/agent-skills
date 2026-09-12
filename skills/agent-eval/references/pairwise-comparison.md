# Pairwise Comparison

Use when judging which of two outputs is better, not scoring each in isolation — more reliable than absolute scoring for subjective quality (two responses are easier to rank against each other than to grade against an abstract 0-1 scale independently).

This extends the same LLM-as-judge mechanics as `references/llm-judge-prompt.md` — same structured-JSON discipline, same "flatten to one line per case" outcome. What's different is the case shape, the prompt asks for a *winner* rather than a *score*, and — the part that's easy to state and easy to skip — **position bias has to be handled structurally, not just remembered.** SKILL.md's step 2 already names the risk ("watch for position bias — always run both orderings and average"); `scripts/run_pairwise.py` is what actually does that, rather than leaving it as a step a future run of this eval might forget.

## Case shape

```json
{"id": "pair_001", "input": "Summarize this ticket for the on-call engineer.", "output_a": "...", "output_b": "...", "category": "accuracy"}
```

`output_a` is conventionally the candidate (the new prompt/model/change being evaluated) and `output_b` the baseline — but the script never assumes which one is "supposed" to win; it only reports which one actually did.

## Why position matters, and how `run_pairwise.py` handles it

A judge shown "Response 1" and "Response 2" tends to favor whichever is shown first, independent of actual quality. Averaging a single ordering's result does nothing about this — it just locks in whichever bias that one ordering happened to produce. The only real mitigation is running the comparison **twice**, with the two outputs swapped between position 1 and position 2, then reconciling:

1. **Ordering 1**: `output_a` shown as "Response 1", `output_b` as "Response 2".
2. **Ordering 2**: `output_b` shown as "Response 1", `output_a` as "Response 2".
3. Map each ordering's positional answer (`response_1`/`response_2`/`tie`) back to `a`/`b`/`tie`.
4. If both orderings agree on the winner, that agreement itself is the signal — position bias couldn't have produced two different favored *positions* landing on the same actual output.
5. If they disagree (each ordering favored whichever output was in "Response 1" that time — the textbook position-bias signature), `run_pairwise.py` doesn't silently average toward a fake middle number and move on. It flags `position_bias_detected: true` in the output row and folds it into the rationale, because a disagreement is itself worth a human looking at the case, not just a slightly-hedged score.

## Prompt template

Same substitution mechanics as `references/llm-judge-prompt.md` (plain string replacement of `{input}`/`{response_1}`/`{response_2}` tokens, not `str.format()`) — `run_pairwise.py` fills `{response_1}`/`{response_2}` itself per ordering, so a template written once serves both calls:

```
You are comparing two AI responses to the same task. You are not the
system that produced either response — judge only what is given below.

## Task
{input}

## Response 1
{response_1}

## Response 2
{response_2}

## Instructions
- Judge strictly on quality for the stated task — do not favor a response
  for being listed first or second, or for being longer.
- If the two responses are genuinely comparable in quality, say "tie"
  rather than forcing a pick.
- Respond with valid JSON only, no other text:

{
  "winner": "response_1",
  "rationale": "..."
}
```

`"winner"` must be exactly `"response_1"`, `"response_2"`, or `"tie"`.

## Flattening to `score_eval.py`'s schema

`run_pairwise.py` writes one row per case, already in `score_eval.py`'s schema:

```json
{"id": "pair_001", "score": 1.0, "category": "accuracy", "rationale": "Response A cited the specific error code; B was vaguer.", "position_bias_detected": false}
```

`score` is `1.0` if `output_a` won both orderings (after remapping), `0.0` if `output_b` won both, and `0.5` for a tie in either ordering *or* a position-bias disagreement between the two orderings — in both cases, there's no reliable signal that one output is actually better, and `0.5` says so honestly rather than picking a side. `position_bias_detected` is an extra field `score_eval.py` ignores harmlessly; check it directly (or grep the results file) when auditing a run for judge reliability, the same way `references/llm-judge-prompt.md`'s calibration log tracks judge/human delta over time.

## What this is not

A pairwise result answers "which of these two is better," not "is this output good in absolute terms" — don't blend a pairwise `score` into the same aggregate as an absolute rubric score for a different case set. Keep them in separate `category` values (or separate files entirely) the same way adversarial and correctness scores are kept separate.
