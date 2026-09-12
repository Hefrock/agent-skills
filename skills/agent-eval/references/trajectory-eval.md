# Trajectory Evaluation

Use when grading *how* an agent reached its output, not just the output itself. A correct final answer reached via a broken process — the wrong tool, malformed arguments, a redundant retry loop — is still a signal of an unreliable agent, and final-output scoring alone can't see it.

This extends the same LLM-as-judge mechanics as `references/llm-judge-prompt.md` — same structured-JSON discipline, same flattening step, same biases to guard against. What's different is the rubric and the case shape.

## What a trajectory case needs, upstream of scoring

A trajectory case must capture the step sequence, not just the final output — this is what plain output-only eval cases don't need:

```json
{
  "id": "traj_001",
  "input": "What's the status of ticket #4521?",
  "trajectory": [
    {"step": 1, "tool": "search_ticket", "args": {"id": 4521}},
    {"step": 2, "tool": "read_ticket", "args": {"ticket_id": 4521}}
  ],
  "final_output": "Ticket #4521 is open, assigned to..."
}
```

Log the actual tool-call sequence somewhere retrievable, not just the eventual score — the sequence is the evidence a debugging session needs later, and it can't be reconstructed from a bare number.

## Judge rubric — trajectory-specific criteria

Add these to the standard judge prompt (`references/llm-judge-prompt.md`) alongside whatever output-quality criteria the task also needs:

- **Tool selection** — did it call the right tool(s) for the task? A broad/expensive tool where a targeted one existed, or a tool that doesn't match the request, scores low even if the agent eventually recovers.
- **Argument correctness** — were the arguments to each call correct and well-formed? A malformed argument that forces a corrective retry is a partial failure, not a free pass because it self-corrected.
- **Step efficiency and order** — a reasonable number of steps in a sensible order. Not an exhaustive crawl when a targeted call would do; not calling a tool for information the agent already had.

Still grade final-output correctness too — trajectory criteria supplement outcome criteria, they don't replace them. Structured output shape:

```json
{
  "tool_selection": {"score": 0.0, "rationale": "..."},
  "argument_correctness": {"score": 0.0, "rationale": "..."},
  "step_efficiency": {"score": 0.0, "rationale": "..."},
  "final_output_correctness": {"score": 0.0, "rationale": "..."},
  "overall_score": 0.0
}
```

**The core discipline this eval type exists to enforce**: a correct `final_output` does not earn a clean `overall_score` of 1.0 if `tool_selection` or `argument_correctness` was broken. If the judge prompt lets a lucky-correct answer paper over a broken trajectory, the eval isn't doing its job.

## Flattening to `score_eval.py`'s schema

Same convention as any other rubric-based eval: one flattened line per case, `category: "trajectory"`, `score` is the judge's `overall_score`, `rationale` is the lowest-scoring criterion (the one explaining the failure):

```json
{"id": "traj_001", "score": 0.4, "category": "trajectory", "rationale": "Called list_all_tickets instead of search_ticket by ID — wrong tool selection", "cost_usd": 0.0087, "latency_ms": 2100}
```

If a case set mixes trajectory and output-quality grading, keep `category` distinct (`trajectory` vs. `accuracy`/`format`/etc.) so `score_eval.py`'s per-category breakdown separates "did it answer correctly" from "did it get there well" — collapsing them into one score hides exactly the gap this eval type is for.

See [`examples/trajectory_example.jsonl`](../examples/README.md) for a worked case set where every final answer is correct but trajectory scoring still catches three broken processes.

## What this is not

Don't over-penalize a trajectory that took a reasonable exploratory step before finding the right path — "reasonable sequence" means sensible, not shortest-possible. An agent that checks one plausible tool, finds it insufficient, and correctly switches is not the same failure as one that retries an identical bad call three times or reaches for a broad tool by default.
