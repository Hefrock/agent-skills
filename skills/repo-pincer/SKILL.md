---
name: repo-pincer
description: Reverse-engineers a codebase by reconciling what it claims to do against what it actually does. Runs two independent passes — top-down from docs/README/manifests/API surface, bottom-up from entry points through the actual implementation — then classifies every claim as Confirmed, Drift (was true, isn't anymore), Aspirational (documented but never built), or Silent (implemented but undocumented). The discrepancy list is the deliverable, not either summary alone. Use this when onboarding onto an unfamiliar codebase, auditing whether documentation matches implementation, evaluating a dependency or vendor repo before committing to it, or when a long-lived project's docs and code have drifted apart. Triggers on "reverse engineer this repo," "does the documentation match the code," "audit this codebase," "onboard me onto this project," or "find where the docs and code disagree." Runs standalone — no MCP or wiki setup required. If an obsidian-vault MCP server is connected, offers to compile its output into Sources/raw/ for wiki-operator's /source command to pick up.
---

# Repo Pincer

A system's real behavior is not what it claims about itself — it's the intersection of what it claims and what it does. Most code review reads one direction (docs, or code) and stops. This methodology reads both independently and closes them like a pincer: the discrepancies between the two passes are the actual finding.

## How this works

Two independent passes that must reconcile. Do not let the second pass be informed by the first — bottom-up findings should stand on their own before being checked against the claims ledger.

### Pass 1 — Top-down (claims)

1. Read outward-facing claims in order of authority: root README → architecture/design docs → package manifests (`package.json`, `pyproject.toml`, `Cargo.toml`, etc.) → per-module docs → public API/CLI surface (exported symbols, defined commands).
2. Build a **claims ledger**: for each claim, record what it asserts, exactly where (file + line or section), and how falsifiable it is. A specific claim ("retries 3× with exponential backoff") is worth more than a vague one ("handles errors gracefully") — note the difference, don't just list claims flatly.
3. Do not verify anything yet. This pass only records what the system says about itself.

### Pass 2 — Bottom-up (reality)

1. Identify entry points first: exported/public functions, CLI commands, API routes, error-handling paths. Start here, not with every private helper — this is where claims are made and where drift matters most.
2. For each entry point, read the actual implementation. Record real behavior — inputs, outputs, side effects, error handling — from the code itself, not from comments or docstrings (those are claims, and belong in Pass 1 if load-bearing).
3. Trace the call graph outward from each entry point only as far as needed to confirm or refute a specific claim — not exhaustively. Depth follows the claim being checked, not a fixed crawl.
4. Build the **as-built model**: what the system actually does and how the pieces actually connect, independent of what Pass 1 said.

### Pass 3 — Reconciliation

1. Walk the claims ledger against the as-built model. Classify every claim:
   - **Confirmed** — matches reality
   - **Drift** — was true once; reality has since moved (version skew, partial refactor)
   - **Aspirational** — describes something not yet implemented (a TODO in disguise)
   - **Silent** — reality does something the docs never mention
2. Rank each Drift/Aspirational/Silent finding **High/Medium/Low**: High if it sits on a write path or a safety/security-relevant boundary; Low if it's cosmetic (a stale README line, a renamed variable with no behavior change).
3. The ranked discrepancy list is the primary deliverable — report it before either summary. Confirmed claims aren't listed individually; note them only in aggregate ("14 of 18 claims confirmed").

## /pincer [path or repo] [--depth quick|standard|thorough]

Default scope is a single skill/module/directory, not the whole repo — widen only if asked, since a full pass on a large repo in one shot is rarely what's useful.

Depth controls how far Pass 2 traces:
- `quick` — top-level README + entry points only, no call-graph tracing
- `standard` (default) — trace only as far as needed to confirm or refute each ledger claim
- `thorough` — trace the full call graph from every entry point

1. Run Pass 1, then Pass 2, then Pass 3, in that order.
2. Compose the output (see schema below).
3. If an `obsidian-vault` MCP server is connected, offer to write the output to `Sources/raw/<repo>-pincer-<date>.md` — the existing raw-capture convention `wiki-synthesizer`/`wiki-operator /source` already compiles. This is optional; the skill runs standalone with no MCP dependency.

## Output schema

If writing into a wiki vault, use this frontmatter — one new field (`subtype`) on the existing `source` note type, no other schema changes:

```yaml
type: source
subtype: codebase
status: draft
confidence: medium   # findings are provisional until cross-checked with a maintainer or the test suite
updated: YYYY-MM-DD
repo: owner/name
commit: <sha analyzed>
```

Body sections:
- `## Top-down summary` — the condensed claims ledger
- `## Bottom-up summary` — the condensed as-built model: entry points, core abstractions, key call-graph findings
- `## Discrepancies` — the ranked Drift/Aspirational/Silent list, each with a `file:line` pointer; Confirmed claims noted only in aggregate
- `## Open questions` — anything genuinely ambiguous even after both passes

If no vault is connected, present the same structure directly in the conversation rather than writing a file.

## Output discipline

- Never present a Pass 1 claim as verified until Pass 2 has actually checked it. A claims ledger is an input, not a finding.
- Never trace exhaustively when a targeted check would confirm or refute a claim faster.
- Lead with High-severity discrepancies. A report with one High finding buried at the bottom is worse than a short one that leads with it.
- If nothing to reconcile is found — the docs are accurate and complete for the scope checked — say so plainly. Don't manufacture findings to seem thorough.
