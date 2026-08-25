---
name: wiki-teacher
description: Turns the wiki into an accountability and growth-mindset system, not just a reference. Three stateless commands — /checkin (portfolio-aware project accountability, a forcing function auto-suggested at session start), /teach (project-scoped teaching, extends wiki-operator's /quiz), and /reflect (user-initiated, portfolio-wide throughline-spotting across active projects — the mentoring layer, an emergent property of seeing the whole portfolio rather than a tone constraint). State lives declaratively in each project note's own frontmatter (priority, checkin_interval, status) — no separate tracking mechanism. Triggers on "check in on my projects," "what should I be working on," "teach me about this project," "quiz me on X project," "help me reflect," "am I developing a specialization," and the commands /checkin /teach /reflect. Requires the obsidian-vault MCP server connected. Separate skill from wiki-operator by design — trigger clarity and a genuine category shift, since none of wiki-operator's principles govern tone, pacing, or accountability.
---

# Wiki Teacher

Accountability, teaching, and mentoring for the project portfolio — not a passive reference. Combines two distinct activities (`/checkin` for accountability, `/teach` for teaching) under a growth-mindset tone constraint, plus `/reflect`, whose mentoring value turns out to be an emergent property of seeing the whole portfolio at once, not a third activity bolted on.

## Prerequisites

The `obsidian-vault` MCP server must be connected. Verify with `/mcp` before running. Pairs with `wiki-operator` — `/teach` extends `/quiz`'s structure, and `/checkin`'s cadence pattern mirrors `wiki-governor`'s.

**Reference implementation:** `/checkin`'s narrowing algorithm (including the batch bootstrap and the "what should I work on" fallback) and `/reflect`'s compartment-span check are both mechanical enough to script — `skills/wiki-teacher/scripts/wiki_teacher.py`, tested against synthetic cases in `test_wiki_teacher.py` (28 tests). Check a real run's output against it; treat a mismatch as a bug in the run, not in the script. What to actually teach or which throughline is worth surfacing stays a judgment call — those parts aren't scripted, and shouldn't be.

**Note on commands:** `/checkin`, `/teach`, `/reflect` are natural-language triggers — write them in the chat window. They are NOT Claude Code CLI slash commands.

## Principles

1. **Portfolio-aware, not single-project.** Several concurrent projects are the expected case for `/checkin`, and the entire reason `/reflect` can do more than a single project's `/review` — it sees throughlines across all of them.
2. **Opposite trigger philosophies, on purpose.** `/checkin` is a forcing function — auto-suggested, because accountability that only runs when asked stops being accountability. `/reflect` is user-initiated only, ambient-reminded at most — forcing it risks turning genuine reflection into a performed checkbox ritual instead.
3. **Elicit missing signal, don't fabricate it.** When `priority` is needed and undeclared, ask — never default it. A guessed default would just reintroduce the exact ranking problem declaring `priority` exists to solve.
4. **Stateless — the vault is the state substrate.** `priority`, `checkin_interval`, `status`, and `compartment` live in the project note's own frontmatter (see `wiki-operator`'s note schema). No teacher-owned tracking file, no computed ease-factor.
5. **Compartment discipline in `/reflect` output.** Its value is spotting a throughline across the whole portfolio — which by construction can cross whatever identity compartments those projects individually belong to. Never surface a cross-compartment throughline without naming, explicitly, that it crosses compartments. Never treat `/reflect`'s own output as exempt from the placement discipline that governs everything else in the vault.
6. **Growth-mindset tone, not a verdict.** Describe what's observed — "you keep returning to X" — never a character judgment. Applies to `/checkin`'s framing and `/teach`'s handling of a wrong answer alike.

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
8. Whenever a project is surfaced — narrowed, bootstrapped-then-narrowed, or suggested — offer `/teach` on it as an optional next step: *"Want me to `/teach` on this one to get oriented before diving in?"* Accountability that names a project but leaves you cold-starting on it isn't actually helping you stay productive.
9. Passively mention `/reflect`'s cadence: if `Maps/_context.md`'s `last_reflected` is 2+ weeks old (or absent), add one flat, non-escalating line — *"It's also been a while since you `/reflect`ed."* Never repeat, escalate, or turn this into its own check-in item.
10. After any run, set `last_checkin` in `Maps/_context.md` to today.

## /teach [project]

Project-scoped teaching — extends `wiki-operator`'s `/quiz`, which is topic-scoped and generic. `/teach` grounds questions in what a specific project is actually doing right now, not a topic in the abstract.

1. Retrieve the project note (`Projects/[name].md`) and its linked concept pages (the `## Key concepts` section).
2. Also read the project's own `## Open questions` and `## Status` sections — the source of what's actually current, not just what topics the project touches.
3. Generate 3–5 questions at progressive difficulty, each grounded in this project specifically:
   - **Recall** — what do the linked concepts actually say?
   - **Application** — how does that concept apply to what *this* project is trying to do?
   - **Synthesis** — given where the project currently stands, what's a non-obvious next step, risk, or connection to another linked concept?
4. Do not show answers — wait for the user to respond before discussing, same discipline as `/quiz`.
5. Treat a wrong or uncertain answer as a signal for what to review next, never as a failure to flag.

## /reflect [scope]

User-initiated only — never suggested as an action, only mentioned ambiently by `/checkin` (see above). Scope defaults to the whole portfolio; a named project or area narrows it.

1. Retrieve every `type: project` note with `status` not `paused`/`complete` — the active portfolio.
2. Retrieve each one's linked concept pages, to see what's actually being exercised across the portfolio, not just project titles.
3. Look for: a concept or skill recurring across ≥2 projects (a developing specialization), a recurring theme across multiple `## Open questions` sections (a persistent skill gap), or a real connection between two projects that don't already link to each other.
4. Compose the observation in growth-mindset framing — what's observed, not a verdict.
5. **Before presenting anything, check the projects behind the observation against `compartment` in each one's frontmatter** (see `wiki-operator`'s note schema) using `spans_multiple_compartments()` in `skills/wiki-teacher/scripts/wiki_teacher.py` — a structural check, not a judgment call: it returns `True` if the projects declare more than one distinct `compartment`, and *also* `True` if any of them leaves `compartment` undeclared, since an unknown compartment is never assumed safe. If it returns `True`, say so explicitly, plainly, as part of the output — never let a cross-compartment (or unknown-compartment) throughline pass as if it were compartment-neutral. This backstops the model's own read of the content; don't skip it because the content "seems" fine.
6. **Never accumulate a `/reflect` history.** A log of reflections over time is structurally the exact "self-generated productivity log" a privacy review already flagged as sensitive to an employer or civil-discovery adversary — don't create one, even implicitly, by writing to a dedicated file each run. If an observation is worth keeping, route it through an *existing* command instead — `/update` on the relevant project note, or `/learn` into `Knowledge/` if it's durable enough to be a concept in its own right — so it inherits whatever compartment and placement discipline already governs that content. Default output is conversational only.
7. The only durable trace of `/reflect` running is a single `last_reflected: YYYY-MM-DD` field in `Maps/_context.md` — a bare date, not a record of what was observed. That's what `/checkin`'s ambient mention (step 9 above) reads.

## Output discipline

- Never auto-run `/checkin` — session start only suggests it.
- Never write a `/reflect` history file. Durable insights go through `/update` or `/learn`, not a teacher-owned log.
- State explicitly, every time, when a `/reflect` observation crosses identity compartments — this is not optional and not inferred from context.
- Growth-mindset framing throughout: describe what's observed, never render a verdict on the person.
- If MCP tools are unavailable, stop and tell the user — do not simulate vault operations in the conversation.
