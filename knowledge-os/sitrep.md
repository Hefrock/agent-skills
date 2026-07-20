# Wiki System — Sitrep & Gap Analysis

_Last updated: 2026-07-20_

## Recently closed

- **Warehouse ↔ governor integration.** `wiki-governor` Phase 1 now runs
  `wiki-warehouse /warehouse-audit` automatically when the vault has any
  `doc_id`-carrying notes, and Phase 3 folds its corrupt/missing/dangling/
  drifted counts into the health score as a sixth, conditional sub-metric
  (excluded and renormalized, not scored 0, when the warehouse isn't in use).
- **Law 9 (provenance) promoted to `wiki-librarian`.** The provenance
  backlink check is now Check 6.4 in the librarian's routine schema audit,
  not a governor-only pass — caught on every `/audit`, not just weekly
  `/govern` runs. Governor's Phase 2 compliance table now cites it as a
  librarian check like the other laws; Law 10 remains governor's own.

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
| `wiki-librarian` | Shipped | 6 structural checks (schema gaps check now includes provenance), risk-tiered fix confirmation |
| `wiki-governor` | Shipped | Orchestrates librarian + synthesizer + warehouse (conditional); adds compliance audit, 6-submetric health score, gap queue |
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

### P2 — Vault versioning is unspecified
`knowledge-warehouse` is git-backed by design; whether the Obsidian vault
itself is under git is never stated in `architecture.md`. Librarian merges
and deletes are "confirm first," but confirm-then-regret has no undo path
if the vault has no version history of its own.
*Fix:* document (and if not already true, set up) git for the vault itself,
even a private repo with no remote — cheap insurance for a system whose
whole job is irreversible-in-place edits.

### P3 — The wiki's own maintenance cadence has the problem the digest just solved
`wiki-governor` says to *suggest* running `/govern` when `last_governed` is
stale — never to run it, and nothing pings you. That's the identical
failure mode the stalled-work digest was built to fix for GitHub issues.
**Deprioritized by choice, not oversight:** this repo isn't expected to
carry much stalled human-action work itself, so wiring the digest to its
own governance cadence isn't worth the same investment here. Revisit if
that changes, or if this pattern gets reused in a repo where it would.

### P3 — Command fragility is documented, not fixed
Every wiki skill repeats the same disclaimer: `/learn`, `/synthesize`,
`/govern` etc. are plain-text triggers, not Claude Code CLI slash commands,
because typing them in the terminal hits the CLI parser instead. That's a
standing footgun baked into the design.
*Fix:* low priority, cosmetic risk only — worth a one-line mention in the
top-level README rather than five repeated skill-level disclaimers, if it
keeps tripping anyone up in practice.

## Recommended order

1. Run one real `/govern` cycle against the actual vault; record the
   baseline health score here.
2. Decide vault git-versioning story and document it in `architecture.md`.
3. Build the fixture-vault regression test (P1 mileage gap) — the biggest
   remaining piece of work and the one that actually proves any of this
   works rather than just reads well.
