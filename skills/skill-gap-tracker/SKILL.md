---
name: skill-gap-tracker
description: Flags languages, frameworks, libraries, statistical methods, or technical concepts that are unfamiliar to the user when they show up while building, reviewing, or discussing a project, and logs them to a learning backlog for later study. Always use this skill when reviewing a codebase, reading dependency files (package.json, requirements.txt, Cargo.toml, go.mod, etc.), explaining a technical approach, or when the user asks "what should I learn next," "what's in my learning backlog," "flag this for later," or wants to update their skill profile. Trigger proactively even if the user doesn't explicitly ask — any time a project surfaces a language, framework, or technique not listed as known in profile.yaml.
---

# Skill Gap Tracker

Tracks the gap between what a project requires and what the user already knows, so nothing unfamiliar slips by unnoticed.

## How this works

1. **Read the profile.** Look for `profile.yaml` in the repo root (or wherever the user points you). It lists what the user already knows, with a confidence level:
   ```yaml
   python: comfortable
   pandas: comfortable
   bayesian-stats: unknown
   rust: familiar
   docker: comfortable
   ```
   If no `profile.yaml` exists, ask the user once for a starting list, then create it. Don't ask again after that — just keep using and updating it.

2. **Scan for unfamiliar territory.** While doing the user's actual task (reviewing code, reading dependencies, explaining an approach), compare every language, framework, library, algorithm, or statistical method you encounter against the profile.
   - Skip anything already listed as `comfortable` or `familiar`.
   - Flag anything `unknown` or not listed at all.
   - Don't flag trivial things (basic syntax, well-known standard-library functions) — only flag things that would take real, deliberate study to learn.

3. **Log flagged items.** Append new entries to `learning-backlog.md` (create it if missing) under the `## Flagged` section, using this exact format:
   ```markdown
   ### Bayesian hierarchical models
   - **Encountered in:** churn-prediction-repo/model.py
   - **Why it showed up:** used for partial pooling across customer segments
   - **Plain explanation:** a way to share statistical strength across related groups instead of treating each separately
   - **Category:** statistics
   - **Seen:** 1 time
   - **First seen:** 2026-06-20
   - **Last seen:** 2026-06-20
   ```
   Before adding, check whether the term (or a close synonym) already exists anywhere in the backlog. If it does, don't duplicate it — increment `Seen` and update `Last seen` instead. Recurring items are a strong signal the user should prioritize learning them; when summarizing, surface high-`Seen` items first.

4. **Don't interrupt the main task.** Logging is a side effect, not the focus — finish what the user actually asked for first, then mention at the end: "I flagged N new things to your learning backlog: [list]."

5. **On request, summarize or triage.** If the user asks "what's in my backlog" or "what should I learn next," read `learning-backlog.md` and:
   - Sort by `Seen` count (highest first), then by recency.
   - When the user says they've learned something, move that entry from `## Flagged` (or `## Learning`) to `## Mastered`, preserving its details. Don't delete entries — mastered items are a visible record of progress.
   - If the user says they're actively working on something, move it to `## Learning`.

## Output discipline

- Never invent an explanation you're not confident about — if unsure what something does, say so in the entry rather than guessing.
- Keep "Plain explanation" to one sentence. This is a flag, not a tutorial.
- Don't flag something the user clearly already understands from context (e.g., they just explained it correctly themselves in the conversation).
- If `profile.yaml` is missing critical context (e.g., it's clearly stale), ask the user to confirm before flagging a wave of items that might already be familiar to them now.
