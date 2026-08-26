# Wiki System — Sitrep & Gap Analysis

_Last updated: 2026-08-02_

## Recently closed

- **`wiki-teacher` pared back to `/checkin` only, after a review called
  the prior two rounds reckless — a fair call, worth recording plainly.**
  The pattern across the two rounds directly above this entry: self-
  generate a list of gaps, self-approve it, self-fix it, and when the fix
  itself turned up *more* self-found gaps, fix those too and roll forward
  into the same commit rather than stopping to report. Every test written
  for `/teach`, `/reflect`, `compartment`, and the `wiki-governor`
  session-start coordination note was authored by the same process that
  authored the code under test — a closed loop that can catch
  implementation bugs but nothing about whether the behavior is actually
  useful, matches real vault content, or is executable correctly by a
  model from an increasingly long prose spec in a live session. None of
  it had ever touched real usage. The original `/checkin` design got this
  right and said so explicitly ("closed for further design... blocked on
  real usage, not on more thinking"); that discipline was honored once,
  for the first closure, and not since.
  - **Reverted:** `/teach` (both its original 5 steps and the round-3/4
    additions — thin-project fallback, wrong-answer routing), `/reflect`
    in full (portfolio-breadth consumption, `/connect` offer, scope
    handling, the compartment-span privacy check), the `compartment`
    field itself (added to `wiki-operator`'s schema, now removed —
    nothing consumes it once `/reflect` is gone), `projects_missing_
    compartment()` and `spans_multiple_compartments()` (removed from
    `wiki_teacher.py`, 12 tests removed), and the `wiki-governor`
    session-start coordination note (self-generated, never validated,
    same category as everything else here).
  - **Kept:** `/checkin` in full — the batch bootstrap, the narrowing
    algorithm, the `explicit_request`/"what should I work on" fallback,
    and `portfolio_breadth()`'s `/checkin`-facing passive report (the
    active count is independently useful without a `/reflect` judgment
    layer to consume it). These are the pieces with real grounding: a
    dry-run against a live vault in the original design, a verifiable bug
    (the skill's own description promised a trigger its algorithm didn't
    deliver), and an explicit user requirement (several concomitant
    projects, stay productive).
  - **Added, not just subtracted:** the validation gap this whole episode
    was actually about. Every `/checkin` test before this round called
    `compute_checkin()`/`portfolio_breadth()` directly with hand-built
    dicts — never through `run()`, never against a real `.md` file, never
    exercising `load_vault()`/`parse_frontmatter()` the way
    `check_vault.py` and `health_score.py` both already do. A new
    dedicated fixture vault (`skills/wiki-teacher/scripts/fixtures/
    vault/`, separate from wiki-librarian's shared one, so nothing
    ripples into its hand-counted totals) plus a `RunIntegrationTests`
    class closes that gap — 5 real files on disk, exercising bootstrap,
    the flaggability gate, the paused off-ramp, and breadth counting all
    through the real parsing path.
  - `test_wiki_teacher.py`: 42 → 37 tests (net: −12 removed, +7 real
    integration tests added). `Maps/_context.md`'s `last_reflected` field
    removed from the hot-cache template — nothing writes it anymore.
    `/teach` and `/reflect` come back once `/checkin` has real mileage and
    an actual felt need surfaces, not a self-generated one.
- **Third `wiki-teacher` round — worked through the ranked gap list from
  the previous review's report, in order.** Three of five items were
  buildable now; the other two got a deliberate, different disposition
  each rather than being forced into code:
  1. **Compartment declaration friction, closed.** The `compartment`
     field (added two rounds ago) had no on-ramp — nothing ever prompted
     for it, so `/reflect`'s privacy check could stay conservative-and-
     noisy indefinitely. `projects_missing_compartment()` (7 new tests)
     finds active projects missing it, and `/checkin` now offers — never
     forces — batching it into whatever bootstrap or narrowing is already
     happening (step 11). Deliberately NOT gated on flaggability the way
     `priority` is: compartment isn't time-sensitive, so it's a standing
     offer, not its own forcing function.
  2. **`/teach`'s thin-project fallback, closed.** A project with fewer
     than 2 linked concepts — exactly the new-project case most likely to
     prompt "teach me about this" — no longer gets a quiz manufactured
     from sparse material; `/teach` says so and offers `/learn`ing
     foundational concepts first, or grounding questions in the project's
     own Goal/Status/Open-questions sections instead.
  3. **`/teach`'s wrong-answer signal, closed.** Used to evaporate at the
     end of the conversation. A recurring miss (not a single slip) can
     now be offered into the project's `## Open questions` via `/update`
     — the connective tissue that lets `/reflect` later notice it as a
     persistent skill gap, the same kind of fix that connected `/checkin`
     to `/teach` two rounds ago.
  4. **Session-start suggestion collision, closed.** `wiki-governor`'s
     `/govern` suggestion and `wiki-teacher`'s `/checkin` suggestion could
     both fire independently at session start with nothing sequencing
     them. Both SKILL.mds now say to combine into one message if both are
     pending, rather than presenting two separate nags.
  5. **A real validation pass on `/teach`/`/reflect` (partial), and
     cross-project dependency modeling — different dispositions, neither
     a plain build.** A real dry-run needs a live vault this remote
     session has no access to; a prose-level walkthrough was done instead
     and it wasn't a clean pass — found three real gaps, all fixed: (a)
     `/teach`'s recurring-gap step was ambiguous about what "session"
     meant and had no actual mechanism to detect recurrence across
     separate `/teach` runs, since nothing persists conversation history
     — resolved by making explicit what was already implicit, that
     `## Open questions` (already read every run) *is* the recurrence
     signal; (b) `/reflect` noticed unlinked-but-related projects but
     never offered `/connect` on them, unlike `/ask`, which already does
     this for concept clusters; (c) `/reflect [scope]`'s own signature
     promised project/area narrowing that no numbered step ever
     operationalized — same documented-not-implemented pattern caught
     twice before in this skill. A prose walkthrough finding three real
     issues is itself evidence a live dry-run would find more; this
     doesn't close the gap, it just makes it smaller. Dependency
     modeling ("Project A is blocked on Project B") is a genuinely bigger
     feature that would change `/checkin`'s narrowing itself (a blocked
     project shouldn't win priority over an unblocked one) — named here
     as a future direction, not attempted speculatively.
  `test_wiki_teacher.py`: 35 → 42 tests. All 137 repo-wide tests pass.
- **Portfolio breadth — the WIP-awareness idea flagged (not built) in the
  previous round, now closed.** `portfolio_breadth()` in `wiki_teacher.py`
  computes two facts: how many projects are active, and days since any
  project was last marked `complete` (`None`/`never_completed: True` if
  none ever have been). Deliberately facts only, no threshold — inventing
  a "too many active projects" magic number would be exactly the kind of
  fabricated-not-elicited signal `priority` already exists to avoid.
  Split by design between the two commands: `/checkin` reports only the
  bare count, passively, no framing at all (step 10) — `/reflect` gets
  the actual judgment (step 4), since "nothing's reached `complete` in
  6 weeks" is structurally identical to the other throughlines it already
  looks for. 7 new tests (35 total in `test_wiki_teacher.py`).
- **`/checkin` closed the gap between its own promised trigger and what it
  actually answered, plus a slow-bootstrap problem neither validation
  pass had caught.** A second review, framed explicitly around "several
  concomitant projects, stay active and productive," found two real
  issues in the design both prior passes (dry-run, privacy review) had
  missed because neither was stress-testing for a multi-project reality:
  (1) `wiki-teacher`'s own description had listed "what should I be
  working on" as a trigger phrase since the first build, but `/checkin`'s
  algorithm only ever answered "what have I neglected" — asking it with
  nothing overdue got silence, the same documented-promise-no-algorithm
  gap that justified building `/ask` earlier. Fixed with a new
  `explicit_request` path in `compute_checkin()`: when invoked directly
  (not the silent session-start check) and nothing's flaggable, it ranks
  all active projects by declared `priority` and suggests the top one
  (`"suggested"`), or says plainly there's no signal to go on if none
  have a priority declared yet (`"no_signal"`) — never silently, and
  never forcing a bootstrap pass for a question with no urgency behind
  it. (2) The bootstrap flow asked about one priority-less flaggable
  project per day, throttled — with several concurrent projects, that
  could be a week before there's enough signal to narrow anything.
  `needs_bootstrap` now returns every priority-less flaggable project in
  one batch, and `/checkin`'s SKILL.md describes proceeding straight to
  narrowing in the same run once they're all answered, rather than
  requiring a second invocation. Also connected `/checkin` to `/teach`:
  whenever a project is surfaced, offer `/teach` on it as a next step —
  accountability that names a project but leaves the user cold-starting
  on it wasn't actually helping anyone stay productive. 7 new tests
  (28 total in `test_wiki_teacher.py`). A portfolio-health/WIP-awareness
  idea (is the active project count itself unsustainable — nothing ever
  reaching `complete`) came up in the same review and was deliberately
  not built this round — a real next-round candidate, not a gap.
- **`wiki-teacher`'s two riskiest pieces of unverified logic, scripted.**
  A post-ship critique flagged `wiki-teacher` as the least-tested skill in
  the system — real, novel logic (`/checkin`'s narrowing algorithm) with
  zero automated verification, shipped right next to two skills that had
  just proven, three separate times, that prompt-only mechanical logic
  hides real bugs until a script and fixture exist to catch them. Same
  fix applied here: `skills/wiki-teacher/scripts/wiki_teacher.py` (28
  tests, `test_wiki_teacher.py`) implements `compute_checkin()` — the
  priority sort, the 15% tie-margin, the bootstrap-precedence rule, the
  cap-at-2 — and `spans_multiple_compartments()`, which turns `/reflect`'s
  privacy safeguard from pure model inference into a structural check
  against a new `compartment` field (added to `wiki-operator`'s project
  schema in the same pass). Undeclared or invalid compartments are never
  assumed safe — treated the same as "might cross a boundary." Unlike
  `check_vault.py`/`health_score.py`, this file doesn't touch the shared
  fixture vault at all: every scenario (the tie-margin boundary, the cap,
  bootstrap-wins-over-narrowing) is a synthetic-dict unit test, chosen
  deliberately to avoid rippling into those two files' hand-counted totals
  again the way the paused/complete fixture notes already did once.
  `/teach`'s question generation and `/reflect`'s actual throughline-finding
  remain unscripted, correctly — both are semantic judgment calls, not
  mechanical rules a script could get exactly right.
- **`wiki-teacher` shipped — project accountability, teaching, and mentoring.**
  Third skill built from a design that already went through two real
  validation passes (a dry-run against the live `Projects/` folder, a
  privacy threat-model review) before any code landed — see the concept
  page for the full design history. Three stateless commands: `/checkin`
  (portfolio-aware, a forcing function auto-suggested at session start,
  narrows N flaggable projects to 1–2 via a declared `priority` +
  `checkin_interval` signal rather than staleness alone, which the dry-run
  proved insufficient — 5 flagged projects landed within 2% of each other
  on any staleness-derived score), `/teach` (project-scoped, extends
  `/quiz`), `/reflect` (user-initiated only, portfolio-wide throughline
  spotting — deliberately never accumulates its own history, to avoid
  recreating the "self-generated productivity log" the privacy review
  flagged as sensitive to an employer/civil-discovery adversary; durable
  insights route through `/update`/`/learn` instead). New frontmatter for
  `type: project` (`priority`, `checkin_interval`, `status: paused|complete`)
  landed in `wiki-operator`'s canonical schema, not teacher-local, since
  it's a note-level concern other skills need to see. `check_vault.py`'s
  stale check and `health_score.py`'s maturity sub-metric both updated
  in lockstep so `paused`/`complete` projects are a real off-ramp
  everywhere, not just in `wiki-teacher`'s own view — 2 new fixture notes,
  4 new tests, existing hand-counts recomputed and reverified (freshness
  has no status exemption, matching how `mature` isn't exempted from it
  either — the off-ramp is stale-flagging and maturity-scoring only).
  `.claude-plugin/marketplace.json` version bumped alongside this, per
  the version-pinning fix below — content that ships without a bump is
  invisible to every existing install.
- **`append_note` could silently create a frontmatter-less note — found
  on a real vault, three occurrences.** `append_note`'s own file-creation
  behavior (documented: "creates the file if it doesn't exist") has zero
  concept of the note schema — it's a raw `fs.appendFile`. Any skill that
  appends a run-log to "today's journal" (`wiki-operator`, `wiki-synthesizer`,
  `wiki-librarian`, `wiki-governor` all do this) creates a schema-violating
  note if that call happens to be the first append of a new day and the
  appended content is just the run-log block, no frontmatter. Recurred
  three times on the same live vault before being caught here; each prior
  occurrence had been treated as "probably a one-off," which is exactly
  the failure mode a governance system exists to catch faster than that.
  Fixed at the mechanical layer, not just the prompt: `append_note` now
  refuses to create a new file unless `content` starts with a frontmatter
  block, erroring instead of silently writing a malformed note. All four
  skills' "append to today's journal" steps updated to create the journal
  from `assets/journal.md` first when it doesn't exist, matching the new
  hard requirement. 6 new MCP server tests (23 total), verified to fail
  without the guard and pass with it.
- **Plugin content updates were silently not reaching installed vaults —
  `metadata.version` in `.claude-plugin/marketplace.json` had been frozen
  at `0.3.0` since 2026-07-06, through every subsequent content PR
  (including the Laws 6/7/8 fix, `check_vault.py`, `health_score.py`, and
  Law 10's check). Claude Code gates content refresh on that version
  number bumping — an unbumped version means `/plugin marketplace update`
  serves the cached copy regardless of what changed on GitHub. Found
  because a real `/govern` run against a live vault (see below) still hit
  the exact Law 7 `Maps/_context.md` false positive that PR #40 fixed two
  days earlier — the fix was live on `main` but had never reached that
  install. The README's "already-installed plugins pick up content changes
  automatically" claim was simply wrong for how this repo ships versions;
  corrected, and `CONTRIBUTING.md` now says to bump the version on every
  content-affecting PR, not just new-skill additions. Bumped to `0.4.0`
  now — but every fix between 0.3.0 and this one may not have reached any
  install that hasn't explicitly reinstalled since. Worth explicitly
  re-verifying the Laws 6/7/8, Law 10, and `append_note` fixes actually
  take effect once an install picks up 0.4.0.
- **First real `/govern` baseline, with a confound.** 65/100 (connectedness
  and provenance strong; maturity and resolution flagged by the run itself
  as the two weak levers — resolution hit its 0% floor because 4 new,
  more-specific questions replaced 2 resolved ones, exactly the
  "floored, not capped" behavior `health_score.py`'s tests lock in). Real,
  useful signal — but this run almost certainly predates the version-bump
  fix above, so its Law 7 pass/fail and possibly its health components
  were computed against a stale skill copy, not the current `main`. Treat
  this number as informative, not as the recorded baseline the
  Recommended-order item asked for; re-run once 0.4.0 is confirmed live.
- **Law 10 (distill, don't dump) has an automated check for the first
  time.** Added Check 6.5 to `check_vault.py`: any note carrying a
  `doc_id` (a `wiki-warehouse` pointer) with a body over ~4000 characters
  is flagged. The threshold is a documented judgment call, not a
  validated measurement — no real vault data has ever existed to measure
  a compliant Source note's actual length against, so revisit it once
  some does. This is explicitly a heuristic, not a semantic check: a
  padded-out dump under the character limit would slip through, and
  `wiki-governor`'s Phase 2 now says so rather than treating a `pass` as
  certainty. Governor's compliance table no longer needs the `unverified`
  carve-out this law was the last one to require — all 10 laws now map to
  a concrete check.
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
| `wiki-teacher` | Shipped, deliberately narrow | `/checkin` only — stateless; narrowing algorithm and portfolio breadth verified against both synthetic cases and real parsed files (`wiki_teacher.py`, 37 tests). `/teach` and `/reflect` were built, self-critiqued, and reverted in the same round — see Recently closed — pending real `/checkin` usage before they come back |
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

`check_vault.py` (`wiki-librarian`, 26 tests, now including Law 10's
distillation check), `health_score.py` (`wiki-governor`'s Phase 3, 16
tests — see Recently closed), and `wiki_teacher.py` (`/checkin`'s
narrowing algorithm and portfolio breadth, 37 tests — including, as of
this round, real-file integration tests against a dedicated fixture
vault, not just synthetic-dict unit tests — see Recently closed) close
this for all three skills' mechanical logic. **Still open:**
`wiki-operator` and `wiki-synthesizer` have no automated verification at
all; `wiki-librarian`'s Checks 4/5
(near-duplicates, contradictions) are semantic and out of a script's
reach by design, not an oversight to fix later; the health-score *weights
themselves* (0.20/0.15/0.10/0.20/0.15/0.20 — arbitrary, never validated
against a real vault), the "90 days = stale" threshold, and Law 10's
4000-character threshold are all still untested guesses even though the
*arithmetic and matching logic* applying them is now verified; and no one
has run a real governance cycle against an actual lived-in vault, so
there's still no first baseline recorded here.
*Fix:* run one real `/govern` cycle against actual vault content — the
last piece, now that both skills it depends on have a tested reference
implementation to check the run against.

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

1. Confirm the installed vault has actually picked up `0.4.0` (reinstall /
   `/plugin update` if needed), then re-run `/govern` once and record that
   as the real baseline — the 65/100 run above doesn't confidently reflect
   current `main`.
2. Decide vault git-versioning story and document it in `architecture.md`.
