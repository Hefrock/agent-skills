# Wiki System — Sitrep & Gap Analysis

_Last updated: 2026-07-21_

## Recently closed

- **Stalled-work digest rebuilt end-to-end (v1 → v3), plus a durable
  dashboard.** The original curl-based design never actually worked — this
  environment's egress proxy blocks raw `api.github.com`/`github.com` for
  every session, confirmed by testing directly, not just inferred from one
  failed run. Rebuilt on `mcp__Claude_Code_Remote__list_repos` (unrestricted
  repo discovery) → `add_repo` → `mcp__github__list_issues` →
  `PushNotification`, bound to a persistent session instead of a tool-less
  fresh spawn per fire (fresh-spawn Routines get zero MCP tools, confirmed
  twice). Verified live: 5 stalled items found correctly across 3 of 14
  public repos. Added a seventh step that regenerates and republishes a
  bookmarkable dashboard artifact to the same URL each Monday, since the
  push notification is short and easy to miss if logged out or the tab
  isn't open. The per-repo search links in `docs/stalled-work-tracking.md`
  (stale — scoped to `agent-skills` alone, from before auto-discovery)
  are now `user:Hefrock`-scoped and cover every repo the digest actually
  scans; the dashboard link is documented alongside them.
- **Warehouse ↔ governor integration.** `wiki-governor` Phase 1 now runs
  `wiki-warehouse /warehouse-audit` automatically when the vault has any
  `doc_id`-carrying notes, and Phase 3 folds its corrupt/missing/dangling/
  drifted counts into the health score as a sixth, conditional sub-metric
  (excluded and renormalized, not scored 0, when the warehouse isn't in use).
- **Provenance check promoted to `wiki-librarian`.** The provenance
  backlink check is now Check 6.4 in the librarian's routine schema audit,
  not a governor-only pass — caught on every `/audit`, not just weekly
  `/govern` runs. Governor's Phase 2 compliance table now cites it as a
  librarian check like the other laws.
- **Constitution reworked for MECE.** Old Laws 1 (search before write) and 6
  (update over create) covered the same "one canonical page" value at two
  control points — merged into one law. Old Law 8 (backlinks mandatory)
  literally duplicated Law 9's provenance wording inside a law that was also
  trying to cover general connectivity — split cleanly: Law 7 is now pure
  "no islands," Law 8 owns provenance outright. This freed a slot, refilled
  by a genuinely missing law: **Law 10, "Distill, don't dump"** — the
  vault/warehouse content boundary that `wiki-warehouse` previously had to
  invent as its own principle, ungrounded in the constitution. Net count
  unchanged at 10. All downstream law-number references (`wiki-librarian`,
  `wiki-governor`) updated to match.

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
| Stalled-work digest (v3) | Running | Weekly Routine, self-bound session; `list_repos`→`add_repo`→`list_issues`→`PushNotification`; no raw curl (blocked by egress policy for every session) |
| Stalled-work dashboard | Running | Artifact snapshot, republished to the same URL each run; readable without Claude mobile, a login, or the push having landed |

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

### P2 — Law 10 (distill, don't dump) has no automated check
Every other law maps to a concrete check somewhere in `wiki-librarian` or
`wiki-governor`. Law 10 doesn't yet — "was full text dumped into a note
instead of distilled" has no heuristic defined. Governor's compliance table
correctly reports it as `unverified` rather than assuming a silent pass,
but that's honesty about the gap, not a fix for it.
*Fix:* design a heuristic (e.g., a body-length threshold on notes carrying
`warehouse_repo`/`doc_id` frontmatter) and wire it into `wiki-librarian`'s
schema-gap check, the same way provenance was.

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

### P3 — Routine has two undocumented operational quirks
Discovered empirically while rebuilding the digest: (1) `update_trigger`'s
`prompt` field is rejected for this self-bound trigger
(`prompt_update_disabled`) — any future prompt change means delete +
recreate, which loses run history and mints a new trigger ID (currently
`trig_01HhqctQZWgChSq32maTv2LN`). (2) If a firing errors out before
reaching the `PushNotification` step, there's no automated signal that the
week's check silently failed — only a human noticing the digest didn't
arrive would catch it.
*Fix:* neither has a clean fix given current platform constraints;
documented here so it isn't rediscovered by trial and error next time the
routine needs to change.

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
