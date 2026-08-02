# Wiki System — Sitrep & Gap Analysis

_Last updated: 2026-08-01_

## Recently closed

- **`health_score.py` — `wiki-governor`'s Phase 3 arithmetic, verified.**
  Extends `check_vault.py`'s pattern (see below) to the six health-score
  sub-metrics, reusing its vault-loading rather than re-parsing frontmatter
  a second time — this project has hit real bugs twice in one session from
  the same logic existing in two places that drifted apart (the MCP
  server's query tokenization; the Laws 6/7/8 scope text living in four
  files). 16 tests, each component value hand-computed against the fixture
  vault independently of the code before running it, not reverse-derived
  from the output. Includes the conditional warehouse-integrity exclusion/
  renormalization and the corrupt/missing/dangling-vs-drifted penalty
  weighting. Several scoping calls in the SKILL.md prose were genuinely
  ambiguous (does "Maturity"/"Freshness" apply vault-wide or just to the
  knowledge-graph domain? is "open questions" a count of entries or a
  presence check?) and got a documented interpretation rather than a
  silent guess — flagged in-code and worth tightening in the SKILL.md
  itself at some point. Also confirmed, by deliberately removing it, that
  the Laws 6/7/8 system-file exemption is currently redundant everywhere
  in `health_score.py` too, for the identical structural reason
  `check_vault.py` already documented — kept as the same defense-in-depth,
  not claimed as tested coverage it doesn't have.
- **First real operational mileage: `check_vault.py` + a fixture vault.**
  Every wiki skill was pure prompt spec with zero automated verification —
  the P1 gap below, standing since this file was created. Built a
  deterministic reference checker (`skills/wiki-librarian/scripts/
  check_vault.py`) implementing the mechanical subset of `wiki-librarian`'s
  checks (broken links, orphans, stale notes, schema gaps including
  provenance and premature-mature), plus a 17-note fixture vault and a
  23-test regression suite wired into CI. Checks 4/5 (near-duplicates,
  contradictions) are deliberately not implemented — they require semantic
  judgment a script can't make, documented as such rather than faked.
  Building the fixture immediately found a real bug: `premature_mature`
  (Law 6, no scope guard at all) flagged every system file with
  `status: mature` — the same false-positive class as the Laws 7/8 fix
  below, just under a different law, and undiscovered until a script tried
  to apply the rule literally. This doesn't close the P1 gap — the other
  three wiki skills and Checks 4/5 are still unverified — but it closes it
  for the one skill whose rules are mechanical enough to script, and it's
  already paid for itself once.
- **Laws 6, 7, and 8 scope fixed — the recurring `Maps/_context.md` false
  positive, plus the related Law 6 instance found while building the test
  above.** Governance runs flagged the hot cache as an island/provenance
  violation on consecutive days with nothing to fix. Root cause was a spec
  divergence, not a check bug: `wiki-librarian`'s implementing checks were
  already correctly scoped to `Knowledge/`, but the constitution's Laws 7
  and 8 carried no scope qualifier at all — and `wiki-governor` Phase 2
  audits against the constitution, so it applied the unscoped text
  vault-wide. That's why two days of inspecting the librarian found nothing
  wrong. Fixed at the authoritative source: the constitution's "Scope of
  Laws 6, 7, and 8" clause exempts system files (`Maps/_*.md`, `System/`)
  from all three (Law 6 added after `check_vault.py` found it applied to
  the same class of file), with the governor, librarian, and
  `architecture.md` all citing it. Hand-authored `Maps/` pages stay in
  scope for all three.
- **Search excerpt bug in the `obsidian-vault` MCP server.** Reported as an
  "empty string vs. omitted param" trigger; the real mechanism was an empty
  *term*, not an empty *param*. Query tokenization was duplicated across two
  functions — `scoreNote` filtered empty terms, the excerpt selector 27 lines
  later did not. A query with leading/trailing whitespace produced an empty
  term, and `line.includes("")` is true for every line, so the excerpt
  silently became the note's **first line** while scores stayed correct —
  right notes, wrong excerpts, which is why it resisted diagnosis. Fixed by
  extracting a single `tokenize()` helper both sites share, plus a regression
  test verified to fail on the old code and pass on the new.
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

### P1 — No operational mileage (partially closed)
Five skills, a 10-law constitution, and a health-score formula exist, and
until now nothing in this repo showed the system had run against a real
vault, or had any automated verification at all — unlike `deid-reid-harness`
and `knowledge-warehouse`, which both shipped with test suites.

`check_vault.py` (`wiki-librarian`, 23 tests) and `health_score.py`
(`wiki-governor`'s Phase 3, 16 tests — see Recently closed) close this for
both skills' mechanical logic. **Still open:** `wiki-operator` and
`wiki-synthesizer` have no automated verification at all; `wiki-librarian`'s
Checks 4/5 (near-duplicates, contradictions) are semantic and out of a
script's reach by design, not an oversight to fix later; the health-score
*weights themselves* (0.20/0.15/0.10/0.20/0.15/0.20 — arbitrary, never
validated against a real vault) and the "90 days = stale" threshold are
still untested guesses even though the *arithmetic* applying them is now
verified; and no one has run a real governance cycle against an actual
lived-in vault, so there's still no first baseline recorded here.
*Fix:* run one real `/govern` cycle against actual vault content — the
last piece, now that both skills it depends on have a tested reference
implementation to check the run against.

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
   baseline health score here — the arithmetic is verified now, so this
   run finally means something rather than being an unverified number.
2. Decide vault git-versioning story and document it in `architecture.md`.
