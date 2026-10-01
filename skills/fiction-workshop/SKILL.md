---
name: fiction-workshop
description: Orchestrates a 17-persona LLM workflow (ideation, planning, drafting, critique, publishing, post-publication) for drafting and critiquing a novel, routed through 6 tool-scoped subagents rather than one agent per persona. Critique-stage and utility personas are always invoked via a fresh, non-forked subagent call so their judgments stay independent of each other and of the drafting conversation. Triggers on "help me plan/draft/critique/publish my novel," "act as [persona] for my manuscript," and the commands /plan, /draft, /critique, /publish, /check. Pairs with the `Knowledge/Writing/llm-assisted-fiction-workflow.md` wiki page, which is the canonical source for persona scope and boundaries — this skill operationalizes that page, it doesn't redefine it.
---

# Fiction Workshop

Runs the LLM-assisted fiction writing workflow: narrow, single-purpose personas for each stage of writing a novel, each with an explicit scope and exclusion list, so no single conversation ends up both drafting and judging its own prose.

**Status: skeleton.** Persona routing table and command shapes are defined below; the six backing subagent definitions (`agents/story-*.md`) and the per-persona reference docs (`references/personas/*.md`) they draw on are written but nothing has run against a real manuscript yet — see the source page's own Open Questions.

## Core idea

Don't create one subagent per persona (18 agents pollutes the picker for every other project). Instead, 6 subagents grouped by the tool access they actually need, each capable of embodying any persona in its group when told which one to be in the invocation prompt. Isolation between critique personas is a property of *how* they're called — a fresh, non-fork `Agent` invocation always starts with zero context, regardless of which type is used — not a property of having a dedicated agent per persona. `story-doctor` is the one agent that's just a single persona (Scene Doctor) — split out from `story-drafter` on 2026-09-30 because Scene Doctor's diagnose-only role needs read-only tools, which Prose Collaborator (write-capable) can't share without weakening the "diagnose, don't rewrite" rule to instruction-only enforcement.

## Persona → agent routing

| Persona | Stage | Agent type |
|---|---|---|
| Premise Generator | Ideation | `story-planner` |
| Story Architect | Planning | `story-planner` |
| Character Psychologist | Planning | `story-planner` |
| World-builder | Planning | `story-planner` |
| Audience / Positioning Strategist | Planning | `story-planner` |
| Prose Collaborator | Drafting | `story-drafter` |
| Scene Doctor | Drafting | `story-doctor` |
| Developmental Editor | Critique | `story-critic` |
| Line Editor (craft pass) | Critique | `story-critic` |
| Adversarial Beta Reader | Critique | `story-critic` |
| Genre / Comp-title Expert | Critique | `story-critic` |
| Literary Agent | Publishing | `story-publisher` |
| Line Editor (final mechanical pass) | Publishing | `story-publisher` |
| Back-cover / Marketing Copywriter | Publishing | `story-publisher` |
| Launch Strategist | Post-Publication | `story-publisher` |
| Research / Fact-Checker | Cross-cutting | `story-utility` |
| Continuity Checker | Cross-cutting | `story-utility` |
| Voice / Style Consistency Checker | Cross-cutting | `story-utility` |

Line Editor is one persona with two invocation modes (merged with the former standalone Copyeditor/Proofreader persona on 2026-09-30 — see its reference doc), which is why it appears twice above under two different agents.

Full scope + exclusion text for each persona lives in `references/personas/<persona-slug>.md` (one file per persona above) — loaded on demand when a command actually invokes that persona, not held in this file. Treat the wiki page as canonical if the two ever drift; fix the drift here, not there.

## Prerequisites

None required to run standalone. If the `obsidian-vault` MCP server is connected and the user has an active `Projects/<Novel Title>.md` page, commands may read/write project state (outline, character bible, draft status) there instead of holding it in conversation — this integration is optional and not yet built; ask the user before assuming a project page exists.

## Isolation rule (non-negotiable)

Any invocation of a `story-critic` or `story-utility` persona MUST be a fresh `Agent` call — never `fork`, never given another persona's notes, never given the drafting conversation's history. Violating this is the exact sycophantic-anchoring failure mode this whole skill exists to prevent. `story-planner`, `story-drafter`, and `story-doctor` invocations are exempt — none of their personas are critique-stage, so they're meant to build on each other sequentially.

## Commands

### /plan [aspect]
Invoke `story-planner` as the persona matching the requested aspect (premise, structure, character, worldbuilding, positioning). Planning personas may share context with each other within a single planning session.

### /draft [scene]
Invoke `story-drafter` as Prose Collaborator (new/extended scene), or `story-doctor` as Scene Doctor (diagnose a broken scene) — two different agents now, not two personas on one. Prose Collaborator needs the outline and relevant character-bible entries as input, not the full manuscript. Revision passes take the author's *reconciled* direction (see Reconciliation below), never raw multi-persona notes or a raw Scene Doctor diagnosis.

### /critique [persona] [target]
Invoke `story-critic` as one of Developmental Editor, Line Editor (craft pass), Adversarial Beta Reader, or Genre/Comp-title Expert — always per the Isolation rule above. If no persona is specified, ask which one rather than guessing; their exclusions are load-bearing and picking wrong defeats the point.

### /publish [aspect]
Before invoking Literary Agent specifically, ask which path the author is on — traditional gatekeeping or self-publishing — if it isn't already established; never default to traditional silently. Then invoke `story-publisher` as Literary Agent, Line Editor (final mechanical pass), Copywriter, or Launch Strategist, per the requested aspect. If the path is self-publishing, say Literary Agent doesn't apply rather than running it anyway.

### /check [aspect]
Invoke `story-utility` as Research/Fact-Checker, Continuity Checker, or Voice/Style Consistency Checker — always per the Isolation rule above. A good candidate for reading a whole manuscript and reporting discrepancies without fixing them.

## Reconciliation (explicitly not automated)

Critique-stage personas will produce contradictory notes by design. This skill never resolves those contradictions automatically — reconciling and prioritizing conflicting feedback into a single set of instructions is the author's job, and only the author's consolidated direction goes back into `/draft`. `assets/reconciliation-worksheet.md` is an optional scaffold for this step (keep/reject/defer per finding, then one consolidated instruction) — it structures the author's synthesis, it doesn't perform it.

## The loop

Not a linear pipeline: `/plan` → `/draft` → `/critique` → `/draft` → ... → `/publish`. A critique pass can kick back to `/plan` if it surfaces a structural problem (e.g. Developmental Editor finds a plot hole that needs Story Architect to redesign, not patch). Post-Publication (`/publish` as Launch Strategist) is the one genuinely terminal stage — nothing loops back from it.

## Open questions

- Self-publishing path — the silent-assumption problem is fixed (`/publish` now asks before invoking Literary Agent, see above); the underlying gap isn't: a self-published author needs cover-design, platform-formatting, and metadata decisions no current persona covers. Revisit once a real self-publishing project needs one.
- Sensitivity/cultural-accuracy reading identified as a gap, not yet added as a persona or routed to any agent type.
- Title generation/testing has no owning persona — sits in the gap between Premise Generator (loglines/hooks) and Positioning Strategist (comps/audience). Minor; not built pending a real recurring need.
- Series/sequel continuity across books isn't covered — this framework is scoped to a single novel. Speculative until a real series project exists.
- Whether `/plan`, `/draft`, etc. should read/write a `Projects/<Novel>.md` vault page automatically, or stay conversation-scoped until the user asks for persistence, is undecided.
- **Deferred 2026-09-30, after reviewing `magnus919/agent-skills`' `writers-helper` skill:** a `manuscript-stats.py`-style mechanical script (word count, passive voice, adverb density, readability, per-chapter pacing) for `story-utility`, and a set of craft-framework output templates (outline, scene-skeleton, character-profile, query-letter, etc.) for several personas' deliverables. A premortem ruled both premature — either would commit this still-unexercised skill to a manuscript-file interface and a specific craft taxonomy before a single real manuscript has run through it. Revisit after the first real run; the honest-economics (Literary Agent), copyright-hygiene (Research/Fact-Checker, Genre/Comp-title Expert) guardrails, and the reconciliation worksheet from later reviews *were* adopted — see those personas' reference docs and the wiki page's Sources section for the full reasoning.

## What's NOT built here

- **Cross-platform fallback.** The Isolation rule above depends on Claude Code's subagent mechanism (`Agent` tool, `agents/*.md` definitions, tool-scoped access) — Claude Code-only by design. On a platform without an equivalent primitive (Codex, Gemini CLI, Cursor, GitHub Copilot), there's no fresh, zero-context persona call to invoke, so the rule can't be executed as written. Deliberately not building a degraded fallback (e.g., manually separated conversations) yet — a premortem on this exact question ruled it premature: speculative, untested, and doubling the maintenance surface for a platform not currently in use. Revisit only if this skill actually needs to run outside Claude Code.
