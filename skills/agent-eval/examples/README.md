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

## Trajectory example — right answer, broken process

A second worked example, for the eval type covered in
[`references/trajectory-eval.md`](../references/trajectory-eval.md):
[`trajectory_example.jsonl`](./trajectory_example.jsonl) is six cases from the
same ticket-support agent, each of which reaches the **correct final
answer** — an output-only eval would score this set 6/6. Trajectory scoring
tells a different story:

```bash
python scripts/score_eval.py examples/trajectory_example.jsonl
```

| id | score | what happened |
|---|---|---|
| traj_01 | 1.0 | one correct call, correct args |
| traj_02 | **0.4** | right answer, but called the broad `list_all_tickets` instead of a targeted lookup |
| traj_03 | 0.9 | correct, one unnecessary-but-reasonable confirmatory call |
| traj_04 | **0.2** | right answer, but retried an identical failing call 3× before succeeding by accident |
| traj_05 | 1.0 | two necessary calls, correct args |
| traj_06 | **0.5** | right tool, malformed argument forced a corrective retry |

Against `score_eval.py`'s default 0.7 threshold, that's a **50% pass rate
(3/6)** — mean score 0.67 — on a case set where every final answer was
right. `traj_04` and `traj_02` are the two lowest-scoring cases, exactly the
regression-with-a-lucky-correct-answer pattern trajectory eval exists to
catch, and neither would show up in an accuracy-only or format-only eval at
all.

This file uses a single `trajectory` category on purpose — a real eval set
would usually mix it with `accuracy`/`format`/etc. so `score_eval.py`'s
per-category breakdown can separate "did it answer correctly" from "did it
get there well," per `references/trajectory-eval.md`.

## Calibration example — a judge fooled by confident, verbose wrong answers

A third worked example (fictional, like the two above — not a real
calibration run) for step 5's calibration check: hand-score a handful of
cases and compare to the judge, per SKILL.md.
[`calibration_judge_scores.jsonl`](./calibration_judge_scores.jsonl) /
[`calibration_human_scores.jsonl`](./calibration_human_scores.jsonl) are 8
cases where the judge rated two long, well-structured, *confidently wrong*
answers almost as highly as the genuinely correct ones — the verbosity bias
`references/llm-judge-prompt.md`'s "Known biases" section already names,
caught the way step 5 actually intends: by a human spot-check, not by
staring at the judge's own rationale text (which reads perfectly reasonable
in isolation).

```bash
python scripts/calibrate_judge.py examples/calibration_judge_scores.jsonl examples/calibration_human_scores.jsonl
```

```
Calibration: 8 case(s) checked, mean delta 0.206 (threshold 0.2)

Per-case (worst agreement first):
  cal_02: judge=0.90 human=0.10 delta=0.80
  cal_05: judge=0.85 human=0.10 delta=0.75
  cal_06: judge=0.90 human=0.85 delta=0.05
  cal_03: judge=0.95 human=0.90 delta=0.05
  cal_01: judge=0.90 human=0.90 delta=0.00
  cal_04: judge=0.90 human=0.90 delta=0.00
  cal_07: judge=0.40 human=0.40 delta=0.00
  cal_08: judge=0.85 human=0.85 delta=0.00

⚠ ACTION NEEDED: mean delta 0.206 exceeds 0.2 — revise the judge prompt
```

Exit code **1** — this is meant to be run as a real gate on your own
calibration cadence too, same as `score_eval.py`'s `--fail-under`. Add
`--update-log references/llm-judge-prompt.md` to append this run to the
calibration log automatically instead of editing the table by hand.

## Cost/latency regression example — the scenario the accuracy gate can't see

The [worked example above](#the-scenario) demonstrates an accuracy
regression that's invisible to cost/latency dashboards. The reverse gap
existed too: `score_eval.py` has always computed and printed mean
`cost_usd`/`latency_ms`, but nothing ever *gated* on them — a change that
holds accuracy perfectly steady while tripling cost sailed through
`--fail-under`/`--fail-on-regression` untouched.

[`results_cost_regressed.jsonl`](./results_cost_regressed.jsonl) is
[`results_baseline.jsonl`](./results_baseline.jsonl) with identical scores
and rationales on every one of the 20 cases (same 90% pass rate, same 0.89
mean score — no accuracy regression at all) but real cost/latency roughly
tripled:

```bash
python scripts/score_eval.py examples/results_cost_regressed.jsonl \
    --baseline examples/results_baseline.jsonl \
    --fail-on-cost-regression --fail-on-latency-regression
```

```
Mean cost: $0.0152
Mean latency: 3412ms
...
⚠ Cost regression: mean $0.0152 vs baseline $0.0049 (+210%, tolerance 20%)
⚠ Latency regression: mean 3412ms vs baseline 1312ms (+160%, tolerance 20%)

GATE FAILED:
  - --fail-on-cost-regression: mean cost $0.0152 exceeds baseline $0.0049 by more than 20%
  - --fail-on-latency-regression: mean latency 3412ms exceeds baseline 1312ms by more than 20%
```

Exit code **1** — a real gate, not just a printed number: `--fail-on-cost-regression`/`--fail-on-latency-regression` (tolerance configurable via `--cost-regression-tolerance`/`--latency-regression-tolerance`, default 20%) both require `--baseline`. `--fail-if-mean-cost-above`/`--fail-if-mean-latency-above` set an absolute ceiling instead, with no baseline needed — useful for a hard budget rather than a relative-regression check.
