# Worked example — catching a regression a CI gate would block

A realistic before/after that shows the whole point of an eval: a change that
looks like a clear win on every surface metric, but silently breaks quality —
and the eval catches it and fails the build.

## The scenario

A support-ticket **summarizer** agent. Someone ships a prompt change — *"be more
concise"* — and it looks great:

- **Cheaper**: mean cost $0.0049 → **$0.0030** per call
- **Faster**: mean latency 1312ms → **822ms**
- **Better formatted**: `format` pass rate 80% → **100%** (the length-cap
  offender `fmt_04` now fits)

Every dashboard an engineer usually watches is green. Ship it, right?

## The two runs

- [`results_baseline.jsonl`](./results_baseline.jsonl) — the known-good run (20 cases)
- [`results_regressed.jsonl`](./results_regressed.jsonl) — after the "be concise" change (same 20 case IDs)

## Run it yourself

```bash
# From skills/agent-eval/
python scripts/score_eval.py examples/results_baseline.jsonl
python scripts/score_eval.py examples/results_regressed.jsonl \
    --baseline examples/results_baseline.jsonl \
    --fail-on-regression --fail-under 0.85
```

## What the eval sees that the dashboards don't

The concise prompt compressed away load-bearing detail in three tickets —
a dropped account ID + error code, collapsed reproduction steps, a mangled
timeline. `accuracy` pass rate fell **100% → 62%**. The gate blocks the merge:

```
=== Eval Report ===
Cases: 20
Pass rate (threshold 0.7): 16/20 (80.0%)
Mean score: 0.84
Mean cost: $0.0030
Mean latency: 822ms

By category:
  accuracy: 62% pass, mean 0.72 (n=8), $0.0030/call, 821ms
  format: 100% pass, mean 0.97 (n=5), $0.0027/call, 736ms
  grounding: 100% pass, mean 0.90 (n=4), $0.0030/call, 820ms
  tool_use: 67% pass, mean 0.83 (n=3), $0.0036/call, 973ms

Lowest-scoring cases:
  [0.30] acc_06 — REGRESSION: reproduction steps collapsed to 'user hit an error', detail lost
  [0.40] acc_03 — REGRESSION: dropped the account ID and error code while compressing
  [0.50] acc_08 — REGRESSION: timeline over-compressed, two events merged incorrectly

⚠ 3 regression(s) vs baseline (passed before, failing now):
  acc_03: 1.00 -> 0.40
  acc_06: 0.90 -> 0.30
  acc_08: 0.80 -> 0.50

GATE FAILED:
  - --fail-under 0.85: pass rate 0.800 is below the gate
  - --fail-on-regression: 3 regression(s) vs baseline
```

Exit code **1** — in CI this fails the build.

## The takeaways

1. **Cost, latency, and format all improved — and the change was still wrong.**
   Surface metrics can't see a summary that's cheaper but loses the account ID.
   The per-category breakdown is what isolates *where* it broke.
2. **`--fail-on-regression` catches the specific cases, not just the average.**
   Overall pass rate only dropped 90% → 80%; the three named pass→fail cases are
   the actionable signal.
3. **The gate turns the eval into a control, not a report.** Same command scores
   the change *and* blocks it — this is how an eval belongs in CI. See the CI
   workflow's `agent-redteam` gate step for the pattern applied to a live job.

## Wiring it into CI

```yaml
- name: Eval gate — block regressions in the summarizer
  run: |
    python skills/agent-eval/scripts/score_eval.py results.jsonl \
      --baseline skills/agent-eval/examples/results_baseline.jsonl \
      --fail-on-regression --fail-under 0.85
```

Commit the baseline alongside the case set; regenerate `results.jsonl` from the
current agent on each run; the job fails the moment quality regresses.
