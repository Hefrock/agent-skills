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

**⚠ Trust boundary — read before running the mechanical check on an unvetted target.**
`check_structural_claims.py` executes every `test_*.py` file it finds under the target's
skill directories via `subprocess.run()`, with no sandboxing — no network isolation, no
resource limits, no restricted filesystem access. That's safe against a target you already
trust (this repo's own skills, a codebase you maintain), because you're just running code
you already trust in a different way. It is **not** safe against "evaluating a dependency
or vendor repo before committing to it" — one of this skill's own documented use cases —
because that target's test files are exactly the untrusted code the audit exists to
evaluate, and running them unsandboxed hands them this session's full privileges. Only run
the mechanical check against a target you'd already be willing to run arbitrary code from;
for a genuinely unvetted target, read Pass 2's entry points and tests statically instead of
executing them, at least until they've been read.

0. **Run the mechanical check first, before any reading.** `scripts/check_structural_claims.py`
   verifies one narrow, high-value claim type — "N-test suite" / "N-test regression suite"
   claims — by actually running the referenced tests and comparing counts, not estimating:
   ```bash
   python skills/repo-pincer/scripts/check_structural_claims.py --claims-file README.md --skills-dir skills
   ```
   This exists because exact-number claims are simultaneously the cheapest to verify (one
   command, no judgment) and the most likely to drift silently — nobody re-counts "26 tests"
   by hand every time a test gets added. Confirmed empirically: a repo-wide pass against this
   very repo found three real Drift findings this way in seconds, before any conversational
   tracing started. A fourth output bucket, `errored`, is not Drift — it means a test file
   crashed or timed out, so the actual count isn't trustworthy enough to call a numeric
   mismatch; that's a different, usually more urgent finding worth its own line in the report,
   not one folded silently into a Drift number. Narrow on purpose — see "What's NOT built here."
   Add `--fail-on-drift` to use this as a direct CI gate — it exits 1 if any claim is Drift
   or Errored, rather than only ever reporting and exiting 0. Missing-skill and no-tests-found
   findings don't gate: those more often mean the line's skill-directory association is
   ambiguous than that something regressed, so they stay report-only either way.
1. Identify entry points first: exported/public functions, CLI commands, API routes, error-handling paths. Start here, not with every private helper — this is where claims are made and where drift matters most.
2. For each entry point, read the actual implementation. Record real behavior — inputs, outputs, side effects, error handling — from the code itself, not from comments or docstrings (those are claims, and belong in Pass 1 if load-bearing).
3. Trace the call graph outward from each entry point only as far as needed to confirm or refute a specific claim — not exhaustively. Depth follows the claim being checked, not a fixed crawl.
4. Build the **as-built model**: what the system actually does and how the pieces actually connect, independent of what Pass 1 said.
5. **Silent findings are the most expensive direction to check, and the first thing to get
   skipped under time pressure.** Finding an undocumented capability means reading code
   looking for behavior nobody claimed exists — there's no claim to start from, unlike
   Confirmed/Drift/Aspirational, which all start from something already written down. At
   repo scale (many targets, limited time), this is exactly the direction that silently
   gets shortchanged first. If time pressure forces a choice, say so in the report's "Open
   questions" section rather than letting thin Silent-coverage look the same as thorough
   coverage.

### Pass 3 — Reconciliation

1. Two directions, not one — a Silent finding has no claim to walk from, so it can't
   surface from the claims ledger alone:
   - Walk the **claims ledger** against the as-built model. Classify each claim as
     **Confirmed** (matches reality), **Drift** (was true once; reality has since
     moved — version skew, partial refactor), or **Aspirational** (describes
     something not yet implemented — a TODO in disguise).
   - Walk the **as-built model** for anything with no corresponding claim at all.
     That's **Silent** — reality does something the docs never mention.
2. Rank each Drift/Aspirational/Silent finding **High/Medium/Low**: High if it sits on a write path or a safety/security-relevant boundary; Low if it's cosmetic (a stale README line, a renamed variable with no behavior change).
3. The ranked discrepancy list is the primary deliverable — report it before either summary. Confirmed claims aren't listed individually; note them only in aggregate ("14 of 18 claims confirmed").

## /pincer [path or repo] [--depth quick|standard|thorough]

Default scope is a single skill/module/directory, not the whole repo — widen only if asked, since a full pass on a large repo in one shot is rarely what's useful.

Depth controls how far Pass 2 traces *within one target*:
- `quick` — top-level README + entry points only, no call-graph tracing
- `standard` (default) — trace only as far as needed to confirm or refute each ledger claim
- `thorough` — trace the full call graph from every entry point

**None of these address breadth** — how to allocate effort *across* many targets when a
repo-wide pass genuinely is what's asked for, despite the default guidance above. Depth is
about one target; a multi-target pass needs a different strategy, found only by actually
running this against a real large repo, not by reading the methodology: run the mechanical
check (Pass 2 step 0) across every target first, then spend conversational tracing effort
on the highest-authority sources (root README, top-level manifests) before per-module
depth, rather than attempting uniform `standard`-depth tracing across everything at once —
that doesn't scale and produces thin, uneven coverage without ever admitting it's thin.

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

## What's NOT built here

- **`check_structural_claims.py` only checks test-count claims.** Not path-existence
  claims, not any other kind of specific number (tool counts, rule counts) — those were
  checked by hand in the pass that motivated this script and found no drift, but
  auto-extracting "things that look like a path" from arbitrary backtick spans in prose
  is meaningfully noisier (backticks cover code snippets and command names too, not just
  paths) and risks false positives if generalized carelessly. A real, scoped follow-up,
  not attempted here.
- **Tuned to this repo's own documentation conventions**, not portable to an arbitrary
  target repo's arbitrary claim phrasing. It assumes a claim and its skill-directory name
  share one line, tree-block style (`├── name/  # ... N-test suite`). An honest limit,
  not really a meaningful one in practice — a different repo phrases these claims
  differently regardless, so a checker built for one repo's convention was never going to
  be zero-effort to point at another one.
- **No semantic or behavioral checking whatsoever.** Never touches Silent findings or
  whether code still does what it claims to do — that stays Pass 2's conversational job,
  unchanged. This script only accelerates the narrow, exact-number slice of it.
- **Can't verify a test-count claim about `repo-pincer` itself.** Its own test file
  includes a live check that re-invokes this script against the real README — counting
  it via subprocess would recurse (verify the claim → run the test file → which runs
  this script again → which tries to verify the same claim → ...). Caught as an actual
  runaway subprocess tree while first verifying this script, not a hypothetical risk.
  `count_actual_tests()` excludes `test_check_structural_claims.py` by name
  unconditionally, so this fails safe (reports "no test files found") rather than
  recursing even if a numbered claim is ever written for `repo-pincer` again — which is
  exactly why the README describes this skill's own suite generically ("test suite"),
  not with a number this tool would just report as unverifiable.
- **A per-test-file timeout (`TEST_FILE_TIMEOUT_SECONDS`, 30s) is the general backstop**
  for the class of bug the recursion above turned out to be one instance of. The filename
  exclusion defuses that one specific known trigger; the timeout defends against any
  *other* future test file that blocks — a stray network call, a leftover `input()`, an
  unrelated infinite loop — turning a hang into a reported `errored` finding instead of a
  wedged process. Confirmed with a deliberately-hanging fixture test file in
  `test_check_structural_claims.py`.
- **Only recognizes stdlib `unittest`'s own summary line** (`"Ran N tests"`) as evidence a
  test file ran. Every skill in this repo uses `unittest` today, so this is untested
  against any other runner's output format (e.g. pytest's `"N passed"`) — a test file
  using a different runner would currently show up as `errored` (no recognized "ran"
  line), not silently miscounted, but it's still a real scope boundary, not a hypothetical
  one, if a future skill's tests are ever written differently.
- **No sandboxing around the code it executes.** `count_actual_tests()`'s `subprocess.run()`
  has a timeout but no network isolation, no resource limits, and no filesystem
  restriction — see the trust-boundary warning at the top of Pass 2 for what this means
  in practice: safe against a target you already trust, not safe as a way to evaluate an
  unvetted third-party repo's code by running it.

## Output discipline

- Never present a Pass 1 claim as verified until Pass 2 has actually checked it. A claims ledger is an input, not a finding.
- Never trace exhaustively when a targeted check would confirm or refute a claim faster.
- Lead with High-severity discrepancies. A report with one High finding buried at the bottom is worse than a short one that leads with it.
- If nothing to reconcile is found — the docs are accurate and complete for the scope checked — say so plainly. Don't manufacture findings to seem thorough.

## Files

| Path | What it is |
|---|---|
| `scripts/check_structural_claims.py` | Pass 2's mechanical accelerant — verifies test-count claims by actually running the tests |
| `scripts/test_check_structural_claims.py` | Unit + CLI test suite (stdlib unittest), including a live check against this repo's own current README.md |
