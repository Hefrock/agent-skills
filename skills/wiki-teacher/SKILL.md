---
name: wiki-teacher
description: Portfolio-aware project accountability for several concurrent projects — /checkin, a forcing function auto-suggested at session start. Surfaces 1–2 projects that genuinely need attention (never a dump of everything overdue), batch-elicits priority when it's needed instead of asking one per day, and answers "what should I work on" even when nothing's overdue. State lives declaratively in each project note's own frontmatter (priority, checkin_interval, status) — no separate tracking mechanism. Triggers on "check in on my projects," "what should I be working on," and the command /checkin. Requires the obsidian-vault MCP server connected. Deliberately narrow for now — see the note below.
---

# Wiki Teacher

Portfolio-aware project accountability — not a passive reference. `/checkin` surfaces what genuinely needs attention across several concurrent projects, without turning into either a nag or a dump.

**Currently `/checkin` only.** `/teach` (project-scoped teaching) and `/reflect` (portfolio-wide mentoring) were part of the original three-command design but are deliberately held back — neither has ever run against real usage or had any real critique pass, unlike `/checkin`, which has now survived three rounds of scrutiny (a dry-run in the original design, a privacy review, and a from-scratch prose walkthrough that found real gaps). Shipping them alongside a validated `/checkin` would dress up unvalidated behavior in credibility it hasn't earned. They come back once `/checkin` has real mileage and an actual felt need surfaces — see `knowledge-os/sitrep.md` for the full history.

## Prerequisites

The `obsidian-vault` MCP server must be connected. Verify with `/mcp` before running.

**Reference implementation:** `/checkin`'s narrowing algorithm — the batch bootstrap, the priority sort, the tie-margin, the cap, and the "what should I work on" fallback — plus portfolio breadth (active count, days since anything last completed) are mechanical enough to script: `skills/wiki-teacher/scripts/wiki_teacher.py`, tested both against synthetic cases and against real parsed files on disk (`test_wiki_teacher.py`). Check a real run's output against it; treat a mismatch as a bug in the run, not in the script.

**Note on commands:** `/checkin` is a natural-language trigger — write it in the chat window. It is NOT a Claude Code CLI slash command.

## Principles

1. **Portfolio-aware, not single-project.** Several concurrent projects are the expected case, not an edge case.
2. **Elicit missing signal, don't fabricate it.** When `priority` is needed and undeclared, ask — never default it. A guessed default would just reintroduce the exact ranking problem declaring `priority` exists to solve.
3. **Stateless — the vault is the state substrate.** `priority`, `checkin_interval`, and `status` live in the project note's own frontmatter (see `wiki-operator`'s note schema). No teacher-owned tracking file, no computed ease-factor.
4. **Growth-mindset tone, not a verdict.** Describe what's observed — "3 projects are overdue" is a fact; "you're falling behind" is a verdict. Stick to the fact.

## Cadence & session start

At session start, read `Maps/_context.md`. If `last_checkin` is not today, check whether any non-`paused`/`complete` project is flaggable (see `/checkin` below). If at least one is, suggest running `/checkin` — never run it unprompted. If nothing is flaggable, skip silently; a suggestion with nothing to say is worse than no suggestion. Throttled to once per day regardless of how many sessions start.

## /checkin

Surface 1–2 projects that genuinely need attention — never a dump of everything overdue. Answers both "what have I neglected" (accountability) and, when nothing's overdue, "what should I work on" — the second half didn't exist in the first build even though this skill's own description already promised that trigger phrase; see `compute_checkin()`'s `explicit_request` parameter below.

1. Retrieve all `type: project` notes. Exclude any with `status: paused` or `status: complete` entirely — the off-ramp.
2. For each remaining project, it's **flaggable** once `days_since(updated) ≥ checkin_interval` (default 14 if the field is absent).
3. **If one or more flaggable projects have no `priority` declared, that's the check-in — ask about all of them in one pass, not one per day.** Staleness alone can't rank importance — a purely date-derived score puts differently-important projects within noise of each other — but eliciting them one at a time across multiple sessions would make the system nearly useless with several concurrent projects, since nothing can be narrowed until priorities are known. Ask about every priority-less flaggable project together: *"3 projects are overdue and don't have a declared priority — quick pass: [[A]], [[B]], [[C]] — high, medium, or low for each?"* Write each answer back with `patch_frontmatter` as it's given. Once every flaggable project has a priority — including ones just answered in this same exchange — proceed immediately to step 4 in the same run. Don't make the user invoke `/checkin` a second time just to see the result of the bootstrap they just did.
4. **Once priorities are known**, narrow among flaggable projects: sort by `priority` (high → medium → low), then by `days_since(updated) ÷ checkin_interval` as a tiebreaker within a tier. Surface the top item. Include a second only if it shares the same `priority` tier as the first *and* its overdue-ratio is within 15% of it — a real tie, not a forced pair. Cap at 2, always. Reference implementation of this exact rule (bootstrap batching, the sort, the tie-margin, the cap) in `skills/wiki-teacher/scripts/wiki_teacher.py`'s `compute_checkin()` — check a real run against it rather than trusting the prose alone.
5. Any flaggable projects beyond the surfaced 1–2 get one passive line: *"N other projects are also overdue — say 'show all' to see them."*
6. **Nothing flaggable, but explicitly asked** ("what should I work on," a direct `/checkin` invocation, not the silent session-start check): don't just say there's nothing to report. Rank all active projects with a declared `priority` (staleness doesn't gate this — nothing here is overdue) and suggest the highest-priority one, tie-broken by whichever has sat untouched longest. If *no* active project has a declared priority either, say so plainly and offer to set some — don't force a full bootstrap pass for an idle question with no urgency behind it, but don't guess either. `compute_checkin(..., explicit_request=True)` implements this exactly — `"suggested"` or `"no_signal"`.
7. **Nothing flaggable, silent session-start check:** skip entirely — a suggestion with nothing to say is worse than no suggestion. This is the one case where step 6's fallback does *not* apply; a proactive suggestion is only useful when actually asked for, never as an unprompted nag.
8. Passively report portfolio breadth: `portfolio_breadth()` in `wiki_teacher.py` gives the active project count and days since anything last completed — append one neutral line, e.g. *"5 active projects."* No framing, no threshold, no "that's a lot" — just the count.
9. After any run, set `last_checkin` in `Maps/_context.md` to today.

## Output discipline

- Never auto-run `/checkin` — session start only suggests it.
- Growth-mindset framing: state what's observed as a fact, never as a verdict on the person.
- If MCP tools are unavailable, stop and tell the user — do not simulate vault operations in the conversation.
