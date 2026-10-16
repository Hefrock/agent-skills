---
name: review-and-plan
description: A grounded way to decide what to do next. Two modes. Quick mode runs a 7-step checklist (clarify, MECE options, grounded premortem, independent review, verify the mechanism, stop signals, build-only-if-needed-twice) before recommending a design or next step. Review mode wraps that checklist in a sitrep, gap analysis and sprint plan for a whole project, repo, notes vault or skill collection. Use when the user asks for a sitrep, status review, gap analysis, sprint plan, premortem, "what should I do next," "review my X," "should I build this," or "is this worth formalizing." Skip it for small, cheap, reversible decisions. Works on any target; the independent-review step uses Claude Code subagents and degrades to a stated limitation elsewhere.
---

# Review and Plan

Most planning fails the same way: the plan reasons well about a picture of the world that was never checked. This skill makes the checking part of the plan.

## Pick a mode

- **Quick:** one decision, one design, one "should I build this." Run the checklist, answer in a few paragraphs.
- **Review:** the user wants the state of a whole thing (project, repo, vault, skill set). Run Sitrep, then Gap analysis, then Sprint plan. The checklist runs inside the sprint plan.
- **Skip:** if the decision is cheap and reversible, just do it and say what you did. Running seven steps on a trivial choice is the failure mode this skill is meant to avoid.

If unsure which mode, ask once.

## The checklist

1. **Clarify first.** Confirm scope and intent before generating options. If the target's own files can answer the question, read them instead of asking.
2. **MECE options.** Lay out choices so they neither overlap nor leave gaps.
3. **Grounded premortem.** Imagine the pick failed. A risk counts only if it traces to something you actually read or a documented failure pattern. Cite the source next to each risk. Drop invented ones.
4. **Independent review.** The judge of soundness should not be the mind that proposed the idea. Where subagents exist, hand the idea to a fresh, non-forked agent and have it choose its own dimensions to check. If not, state that the review was solo and weaker for it.
5. **Verify the mechanism.** Reasoning cannot catch a tool that does not behave as assumed. Before calling anything done:
   - Refresh remote state (`git fetch`, re-list, re-read) before describing it. A snapshot is a claim about the past.
   - Cite only sources you opened. A link in a search listing is a claim about the listing.
   - Compare installed or cached copies of tools against their source before trusting a run.
   - Prove a check can fail: revert the fix and watch the test fail, then restore it.
6. **Stop signals, written first.** Before starting a sweep or repair loop, write down what ends it, for example: a rerun returns the same findings, a fix touches something outside the list, two checks in a row find nothing new. Do not fix, recheck, fix the fix.
7. **Needed twice.** Do not build anything permanent until the need has shown up twice. Write it down first, formalize later.

## Review mode

### Sitrep
State what exists and what is moving. Say exactly what you read and what you only listed or inferred. Refresh anything remote or cached before stating it (step 5). Where two parts of the target disagree, report both.

### Gap analysis
A table: gap, evidence, where the evidence came from. Evidence must be something you observed. Separate gaps in the target from gaps in your own coverage.

### Sprint plan
A short ordered list, each task with a "done when" line. Put read-only and low-risk work first. Include the stop signals from step 6, and a list of what is deliberately not in the sprint. Run the premortem (step 3) on the plan as a whole and list each risk with its evidence.

## Report honestly

- Open with coverage: what you read in full, in part, listed only, and not at all.
- If a check was skipped, or an earlier statement turned out wrong, say so and correct it.
- Ask the user before any outward-facing or hard-to-reverse action.

## Examples

- "Do a sitrep and gap analysis on my repo and give me a sprint plan."
- "Premortem this design before I build it."
- "Review my notes vault and tell me what to fix first."
- "Is this worth turning into a skill?" (runs step 7 against the real usage log)

## What's NOT built here

- **No automation.** It is instructions only. It cannot force step 5 to run; the trigger description is the only thing that brings it into play, so a task that does not look like a review will not load it.
- **Independent review depends on subagents** (Claude Code's `Agent` tool). On platforms without an equivalent, step 4 degrades to a solo pass that must be labeled as such.
- **No proportionality rule beyond "skip when cheap and reversible."** Whether the full seven steps pay off for mid-sized decisions is untested.
