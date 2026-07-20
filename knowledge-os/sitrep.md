# Wiki System — Sitrep & Gap Analysis

_Last updated: 2026-07-20_

Status snapshot and honest gap list for the wiki system (`wiki-operator`,
`wiki-synthesizer`, `wiki-librarian`, `wiki-governor`, `wiki-warehouse` +
`knowledge-warehouse`) and the stalled-work tracking layer built alongside it.
Update this file whenever the system's shape changes — it's the "where do
things actually stand" doc, separate from `constitution.md` (the rules) and
`architecture.md` (the design).

## What's built and shipped

| Component | Status | Notes |
|---|---|---|
| `wiki-operator` | Shipped | `/learn /update /connect /ask /review /quiz /map /source /clean /health` |
| `wiki-synthesizer` | Shipped | Journal preprocessing + promotion, `Sources/raw/` compilation |
| `wiki-librarian` | Shipped | 6 structural checks, risk-tiered fix confirmation |
| `wiki-governor` | Shipped | Orchestrates librarian + synthesizer; adds compliance audit, health score, gap queue |
| `wiki-warehouse` | Shipped | `/ingest`, `/warehouse-audit` (two-half: warehouse `bin/audit.py` + MCP pointer check) |
| `knowledge-warehouse` repo | Shipped | `intake.py`, `audit.py`, 7-test suite, private, content-hash join |
| `obsidian-vault` MCP server | Shipped | 10 tools, user-level launch via `~/.claude.json` |
| Stalled-work digest | Running | Weekly Routine, auto-discovers public repos, phone push |

Everything above exists and is merged. What follows is what's missing or
untested.

## Gap analysis

### P1 — No operational mileage
Five skills, a 10-law constitution, and a health-score formula exist, but
nothing in this repo shows the system has run against a real, lived-in
vault. The health-score weights (`architecture.md` Phase 3), the "90 days =
stale" threshold, and the 5-issue cap in `/review` are all untested
guesses. Unlike `deid-reid-harness` and `knowledge-warehouse`, which both
shipped with test suites, the wiki skills have **zero automated
verification** — a prompt edit to `wiki-operator` could silently change
behavior and nothing would catch it.
*Fix:* run a real governance cycle against actual vault content and record
the first health-score baseline in this file. Consider a small regression
set (a fixture vault + expected `/health` findings) the way `agent-eval`
does for other skills.

### P1 — The wiki's own maintenance cadence has the problem the digest just solved
`wiki-governor` says to *suggest* running `/govern` when `last_governed` is
stale — never to run it, and nothing pings you. That's the identical
failure mode the stalled-work digest was built to fix for GitHub issues,
just not applied to the wiki itself.
*Fix:* have `/govern` (or the existing weekly Routine) open a
`[followup: YYYY-MM-DD]` issue when `last_governed` exceeds 7 days or the
health score regresses — one mechanism covering both systems instead of two.

### P2 — Warehouse and governor don't share findings
`/warehouse-audit`'s dangling/drifted/orphaned results never reach the
health score or `Maps/_gaps.md`. A document warehoused but never
synthesized is the same failure shape as an orphan page, but only
`wiki-librarian`'s orphan check feeds Phase 3 of `wiki-governor`.
*Fix:* fold warehouse-audit's finding counts into the health score as a
sixth sub-metric, or at minimum surface them in the governance report.

### P2 — Vault versioning is unspecified
`knowledge-warehouse` is git-backed by design; whether the Obsidian vault
itself is under git is never stated in `architecture.md`. Librarian merges
and deletes are "confirm first," but confirm-then-regret has no undo path
if the vault has no version history of its own.
*Fix:* document (and if not already true, set up) git for the vault itself,
even a private repo with no remote — cheap insurance for a system whose
whole job is irreversible-in-place edits.

### P3 — Command fragility is documented, not fixed
Every wiki skill repeats the same disclaimer: `/learn`, `/synthesize`,
`/govern` etc. are plain-text triggers, not Claude Code CLI slash commands,
because typing them in the terminal hits the CLI parser instead. That's a
standing footgun baked into the design.
*Fix:* low priority, cosmetic risk only — worth a one-line mention in the
top-level README rather than five repeated skill-level disclaimers, if it
keeps tripping anyone up in practice.

### P3 — Law 9 (provenance) is the newest and least enforced law
Only `wiki-governor` Phase 2 checks it, and its own compliance table marks
it "**(new)**" — it isn't one of `wiki-librarian`'s core schema checks
alongside the other laws librarian already covers.
*Fix:* promote the provenance check into `wiki-librarian`'s Check 6
(schema gaps) so it's caught during routine audits, not just governance runs.

## Recommended order

1. Run one real `/govern` cycle against the actual vault; record the
   baseline health score here.
2. Wire the digest (or `/govern`) to self-report when governance lapses —
   closes the P1 cadence gap using infrastructure that already exists.
3. Decide vault git-versioning story and document it in `architecture.md`.
4. Fold warehouse-audit findings into the health score.
5. Move the Law 9 check into `wiki-librarian`.
