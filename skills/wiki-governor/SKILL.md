---
name: wiki-governor
description: Runs the wiki's self-governing maintenance loop and holds it accountable to its own constitution. Orchestrates wiki-librarian (structure), wiki-synthesizer (compilation), and wiki-warehouse (cold-storage integrity, if in use), then adds the things none of them do — a constitution-compliance audit, a tracked wiki health score, and a knowledge-gap queue. Use on a weekly cadence or when the vault hasn't been maintained in a while. Triggers on "govern my wiki," "maintain my wiki," "is my wiki healthy," "wiki health score," "check constitution compliance," "what am I missing in my vault," and the command /govern. Requires the obsidian-vault MCP server connected. Pairs with wiki-librarian, wiki-synthesizer, and wiki-warehouse, which it invokes rather than reimplements.
---

# Wiki Governor

The skill that keeps the wiki not just maintained but *accountable*. It does not add knowledge and it does not re-run audits by hand — it **orchestrates** the maintenance skills, then measures whether the vault is living up to its own rules and getting healthier over time.

## Prerequisites

The `obsidian-vault` MCP server must be connected. Verify with `/mcp` before running. Governor invokes `wiki-librarian` and `wiki-synthesizer` always, and `wiki-warehouse` if the vault has any warehouse-linked notes; load whichever apply alongside it.

**Note on commands:** `/govern` is a natural-language trigger — write it in the chat window. It is NOT a Claude Code CLI slash command.

## Principles

1. **Orchestrate, don't duplicate.** Governor calls `wiki-librarian /audit`, `wiki-synthesizer /synthesize`, and — when the warehouse is in use — `wiki-warehouse /warehouse-audit`. It only implements what none of them do: compliance, health scoring, gap surfacing. If a check already lives in another skill, cite it — don't re-scan.
2. **Measure, don't game.** The health score is a mirror, never a target. Report sub-metrics honestly, including regressions. A score that is being optimized instead of earned is worse than no score — say so if you see it happening (Goodhart's law).
3. **Report before acting.** Inherit the librarian's confirm-before-destructive discipline. Governor auto-applies only the low-risk fixes the librarian already classifies as safe.
4. **Leave a trail.** Every run appends a governance report to today's journal and updates `Maps/_context.md`.
5. **Cadence over impulse.** Designed to run weekly, or when `last_governed` is more than 7 days old.

## /govern [scope]

Scope options: `maintain` (Phases 1 only), `audit` (Phases 2–4, no fixes), or omit for the full loop.

### Phase 1 — Maintain (orchestrate existing skills)

1. Run `wiki-librarian /audit` — full structural audit. Auto-apply only the fixes the librarian classifies as low-risk; surface medium/high-risk fixes for confirmation.
2. Run `wiki-synthesizer /synthesize` — compile any pending journal ideas and `Sources/raw/` files.
3. Check whether any vault note carries a `doc_id` (a `wiki-warehouse` pointer). If so, run `wiki-warehouse /warehouse-audit` — both halves — and keep its corrupt/missing/dangling/drifted counts for Phase 3. If no `doc_id` notes exist, skip this step; the warehouse simply isn't in use for this vault, which is not itself a finding.

Governor invokes these; it does not reimplement their checks.

### Phase 2 — Constitution compliance audit

Hold the vault to `knowledge-os/constitution.md`. Map the librarian's and warehouse's findings to the laws they break — Law 9 is the only check governor makes itself, and Law 10 has no automated check yet (see note below):

| Law | Compliance check | Source |
|---|---|---|
| 3 — Preserve uncertainty | `confidence: low` pages have an `## Open questions` section | librarian schema check |
| 6 — Status reflects reality | `status: mature` pages link to ≥2 others | librarian schema check |
| 7 — No islands | zero inbound **and** outbound links | librarian orphan check |
| 8 — Preserve provenance | every concept page carries a `Captured from [[journal]]` or `Source: [[…]]` backlink | librarian schema check |
| 9 — One run, one log | today's journal has a synthesis/audit log entry | governor |

Output a compliance table: law, pass/fail, and the specific pages violating it. Law 9 is governor's own distinctive check — it's about the governance run itself, so nothing else is positioned to verify it.

**Law 10 (distill, don't dump) has no automated check yet.** Detecting "was full text dumped into a note" needs a heuristic (e.g., a body-length threshold on notes carrying warehouse frontmatter) that hasn't been designed or agreed on. Until it exists, list Law 10 in the compliance table as `unverified`, not `pass` — don't silently assume compliance for a law with no check behind it.

### Phase 3 — Health score

Compute six sub-metrics, then roll them into one transparent score. Five come from a single MCP query over the vault; the sixth reuses the `wiki-warehouse /warehouse-audit` results gathered in Phase 1:

| Sub-metric | Definition | Weight |
|---|---|---|
| Connectedness | % of `Knowledge/` pages with ≥2 links | 0.20 |
| Maturity | mature ÷ (mature + draft + stale) | 0.15 |
| Freshness | % of pages updated within 90 days | 0.10 |
| Provenance | % of concept pages with a provenance backlink (Law 8) | 0.20 |
| Resolution | 1 − (open questions ÷ total concept pages), floored at 0 | 0.15 |
| Warehouse integrity | 1 − (corrupt + missing + dangling + 0.5×drifted) ÷ total warehouse-linked docs | 0.20 |

`health = Σ (submetric × weight)`, reported as a 0–100 score **and** its components — never the headline alone. Show the delta versus the previous run. Weights are transparent and adjustable; the point is the trend, not the absolute number.

**Warehouse integrity is conditional, not always-on.** "Warehouse-linked docs" are vault notes carrying a `doc_id` (from `wiki-warehouse`). If none exist, the warehouse isn't in use for this vault — drop the sub-metric entirely and renormalize the remaining five weights to sum to 1.0. Do not score it 0 (unfairly tanks the score for an unused feature) or 1 (falsely implies a clean bill of health). Corrupt/missing/dangling each count as a full penalty — the original or its pointer is actually broken. Drifted counts at half weight, since a drifted path is stale-but-fixable, not a break (see `wiki-warehouse`'s `references/warehouse-schema.md` for the corrupt/missing/dangling/drifted definitions).

Record in `Maps/_context.md` under `## Health` with the date, so the trajectory is visible run over run.

### Phase 4 — Knowledge-gap queue

Make the wiki notice what it does not know. Aggregate every `## Open questions` entry across the vault plus every entry in `Maps/_ask_log.md` (written exclusively by `wiki-operator`'s `/ask` — governor reads it, never writes it) into `Maps/_gaps.md`, ranked by how many pages reference or depend on each gap. This is the vault's to-learn queue — the input that tells you where to point the next learning session.

### Phase 5 — Governance report + hot cache

Append to today's journal:

```markdown
## Governance run — YYYY-MM-DD

- Maintained: [librarian fixes applied], [synthesizer pages compiled], [warehouse-audit result, or "warehouse: not in use"]
- Compliance: [N laws passing / M total]; provenance gaps: [[page]], [[page]]
- Health: [score]/100 ([+/-Δ] vs last run) — connectedness X, maturity Y, freshness Z, provenance P, resolution R, warehouse W (or "warehouse: n/a")
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
