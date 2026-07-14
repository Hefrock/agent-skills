---
name: wiki-governor
description: Runs the wiki's self-governing maintenance loop and holds it accountable to its own constitution. Orchestrates wiki-librarian (structure) and wiki-synthesizer (compilation), then adds the three things neither does — a constitution-compliance audit, a tracked wiki health score, and a knowledge-gap queue. Use on a weekly cadence or when the vault hasn't been maintained in a while. Triggers on "govern my wiki," "maintain my wiki," "is my wiki healthy," "wiki health score," "check constitution compliance," "what am I missing in my vault," and the command /govern. Requires the obsidian-vault MCP server connected. Pairs with wiki-librarian and wiki-synthesizer, which it invokes rather than reimplements.
---

# Wiki Governor

The skill that keeps the wiki not just maintained but *accountable*. It does not add knowledge and it does not re-run audits by hand — it **orchestrates** the maintenance skills, then measures whether the vault is living up to its own rules and getting healthier over time.

## Prerequisites

The `obsidian-vault` MCP server must be connected. Verify with `/mcp` before running. Governor invokes `wiki-librarian` and `wiki-synthesizer`; load them alongside it.

**Note on commands:** `/govern` is a natural-language trigger — write it in the chat window. It is NOT a Claude Code CLI slash command.

## Principles

1. **Orchestrate, don't duplicate.** Governor calls `wiki-librarian /audit` and `wiki-synthesizer /synthesize` for structure and compilation. It only implements what neither does: compliance, health scoring, gap surfacing. If a check already lives in the librarian, cite it — don't re-scan.
2. **Measure, don't game.** The health score is a mirror, never a target. Report sub-metrics honestly, including regressions. A score that is being optimized instead of earned is worse than no score — say so if you see it happening (Goodhart's law).
3. **Report before acting.** Inherit the librarian's confirm-before-destructive discipline. Governor auto-applies only the low-risk fixes the librarian already classifies as safe.
4. **Leave a trail.** Every run appends a governance report to today's journal and updates `Maps/_context.md`.
5. **Cadence over impulse.** Designed to run weekly, or when `last_governed` is more than 7 days old.

## /govern [scope]

Scope options: `maintain` (Phases 1 only), `audit` (Phases 2–4, no fixes), or omit for the full loop.

### Phase 1 — Maintain (orchestrate existing skills)

1. Run `wiki-librarian /audit` — full structural audit. Auto-apply only the fixes the librarian classifies as low-risk; surface medium/high-risk fixes for confirmation.
2. Run `wiki-synthesizer /synthesize` — compile any pending journal ideas and `Sources/raw/` files.

Governor invokes these; it does not reimplement their checks.

### Phase 2 — Constitution compliance audit

Hold the vault to `knowledge-os/constitution.md`. Map the librarian's findings to the laws they break, and add the one structural check the librarian does not make:

| Law | Compliance check | Source |
|---|---|---|
| 3 — Preserve uncertainty | `confidence: low` pages have an `## Open questions` section | librarian schema check |
| 7 — Status reflects reality | `status: mature` pages link to ≥2 others | librarian schema check |
| 8 — Backlinks mandatory | no island pages (zero inbound **and** outbound) | librarian orphan check |
| **9 — Preserve provenance** | **every concept page carries a `Captured from [[journal]]` or `Source: [[…]]` backlink** | **governor (new)** |
| 10 — One run, one log | today's journal has a synthesis/audit log entry | governor |

Output a compliance table: law, pass/fail, and the specific pages violating it. Provenance (Law 9) is the governor's distinctive pass — it is what makes the knowledge graph auditable, and nothing else checks it.

### Phase 3 — Health score

Compute five sub-metrics, each a single MCP query over the vault, then roll them into one transparent score:

| Sub-metric | Definition | Weight |
|---|---|---|
| Connectedness | % of `Knowledge/` pages with ≥2 links | 0.25 |
| Maturity | mature ÷ (mature + draft + stale) | 0.20 |
| Freshness | % of pages updated within 90 days | 0.15 |
| Provenance | % of concept pages with a provenance backlink (Law 9) | 0.25 |
| Resolution | 1 − (open questions ÷ total concept pages), floored at 0 | 0.15 |

`health = Σ (submetric × weight)`, reported as a 0–100 score **and** its five components — never the headline alone. Show the delta versus the previous run. Weights are transparent and adjustable; the point is the trend, not the absolute number.

Record in `Maps/_context.md` under `## Health` with the date, so the trajectory is visible run over run.

### Phase 4 — Knowledge-gap queue

Make the wiki notice what it does not know. Aggregate every `## Open questions` entry across the vault (plus any failed `/ask` queries the operator has logged) into `Maps/_gaps.md`, ranked by how many pages reference or depend on each gap. This is the vault's to-learn queue — the input that tells you where to point the next learning session.

### Phase 5 — Governance report + hot cache

Append to today's journal:

```markdown
## Governance run — YYYY-MM-DD

- Maintained: [librarian fixes applied], [synthesizer pages compiled]
- Compliance: [N laws passing / M total]; provenance gaps: [[page]], [[page]]
- Health: [score]/100 ([+/-Δ] vs last run) — connectedness X, maturity Y, freshness Z, provenance P, resolution R
- Top knowledge gaps: [gap], [gap], [gap]
```

Then update `Maps/_context.md`: set `last_governed`, write the `## Health` block, and list the top gaps under `## Open threads`.

## Cadence & session start

At session start, read `Maps/_context.md`. If `last_governed` is more than 7 days ago, suggest running `/govern` — do not run it unprompted. (An optional `SessionStart` hook can automate the reminder; configure it via the `update-config` skill rather than hard-coding it here.)

## Output discipline

- Never inflate the health score. A regression is the most useful thing a run can report — lead with it.
- Confirm before any destructive fix; only librarian-classified low-risk fixes apply automatically.
- The score is diagnostic, not a grade. If a metric is being gamed (e.g., links added only to move connectedness), flag it rather than celebrating the number.
- Governor never invents knowledge — Phases 2–4 only measure and organize what the other skills produced.
