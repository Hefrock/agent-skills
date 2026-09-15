---
name: privacy-threat-oracle
description: Deterministic, rule-based privacy decision engine — evaluates a proposed action (which identity/compartment it's taken from, who can see it, what sensitive content it contains) against a threat model of adversary classes and identity compartments, and reports a recommendation (proceed / proceed_with_modification / decline) with a stated residual risk and reason. Reuses privacy-linter's exact content-class vocabulary (direct_pii, secret, metadata, inference_cue, stylometric) so its --json output plugs in directly as the content-sensitivity input. Runs entirely locally, no model or network call — a rule table over a fixed schema, not an LLM judgment call. Use when the user wants to check whether sharing/posting something crosses an identity-compartment boundary, asks "should I post this under my real name," "is this safe to share publicly," "does this leak across my compartments," or wants a structured second opinion before a disclosure decision. Triggers on "check this against my threat model," "will this cross compartments," "is this safe to post/share," "privacy decision check," and the script oracle.py. Does not parse free-text proposed actions (see "What's NOT built here") or replace privacy-linter's own content detection — this is the decision layer on top of it, not a second scanner.
---

# Privacy Threat Oracle

The governance layer of the Privacy OS spine (`Projects/Privacy OS - Threat-Model
Decision Engine` in the source vault): a living threat model — adversary classes,
identity compartments, cost-to-defend tiers — encoded as data, plus a deterministic
decision layer over it. Where `privacy-linter` answers "does this content contain a
leak," this answers "given who this identity is and who'd see it, should this
disclosure happen at all."

## Why this is deterministic-only, not model-based

Unlike `privacy-linter`'s inference-cue/stylometric classes (which genuinely need a
model and are blocked on a local-model decision that hasn't been made), this project's
core design doesn't have the same wall: v1 evaluates *already-structured* facts about a
proposed action (which compartment, which exposure, which content classes) against a
fixed rule table — not raw disclosure content run past an external model. See
`references/decision-rubric.md` for why the rule table is a ranked ordering rather than
a weighted scoring function (the source project's own Open Question #2), and why
structured-only input (not free text) is the deliberate v1 scope (Open Question #3).

## How this works

```bash
python scripts/oracle.py \
  --source-compartment personal --target-compartment public_professional \
  --target-exposure public_internet --content-class direct_pii
```

1. **Compartment violation check** — does the target compartment fall outside what the
   source compartment may reference? All three compartments (`public_professional`,
   `personal`, `sensitive_research`) only reference themselves, per
   `references/threat-model.json` — full mutual isolation is the confirmed policy, not
   a v1 placeholder (see `references/decision-rubric.md`'s Step 1).
2. **Exposed adversaries** — resolved from `--target-exposure` via the same file's
   `target_exposure_map` (e.g. `public_internet` reaches data brokers, criminals,
   employer, corporations, civil discovery, state actors, and autonomous adversarial AI
   agents — automated tools that scrape and correlate public content across
   compartments at machine scale, without needing a human to bother). Any exposed adversary
   flagged `out_of_scope` in `threat-model.json` (currently just the state actor — see
   `references/decision-rubric.md`'s "Out-of-scope adversaries" section) still counts
   toward `adversary_cost_tier` and the recommendation like any other adversary, but is
   also surfaced separately in `out_of_scope_adversaries_exposed` and noted in `reason`
   — a flag this tool can't meaningfully help you defend against, called out rather than
   silently folded into the same bucket as one you can.
3. **Sensitivity tier** — the highest tier among `--content-class` values present
   (`direct_pii`/`secret` = high, `metadata`/`inference_cue`/`stylometric` = medium).
4. **Recommendation** — the rule table in `references/decision-rubric.md`, applied top
   to bottom: compartment violation + high sensitivity → `decline`; compartment
   violation alone → `proceed_with_modification`; high sensitivity reaching a
   high-cost-to-defend adversary → `decline`; and so on down to a clean `proceed`.
5. **Reversibility** — `--reversible` downgrades the recommendation one step (`decline`
   → `proceed_with_modification`, `proceed_with_modification` → `proceed`; never skips
   straight to an unqualified `proceed` on a compartment-violation-plus-high-sensitivity
   case — see `references/decision-rubric.md`'s Step 4), modeling that a retractable
   action carries genuinely lower cost.
6. **Plug in privacy-linter directly** instead of naming content classes by hand:
   ```bash
   python ../privacy-linter/scripts/scan_diff.py --file draft_post.txt --json | \
     python scripts/oracle.py --source-compartment personal --target-compartment personal \
       --target-exposure public_internet --from-linter-json -
   ```
   This is the "wire oracle to Pre-Disclosure Linter for severity context" integration
   the source project named as a milestone — `--from-linter-json` reads a
   `scan_diff.py --json` payload (or any list of `{"leak_class": ...}` objects) and folds
   its finding classes into the sensitivity calculation, deduped. If the input can't be
   read (malformed JSON, wrong shape), the oracle fails **closed**, not open: it warns on
   stderr, sets `linter_json_parse_error: true`, and forces the sensitivity floor to
   `high` rather than silently falling back to "nothing sensitive found."
7. **`--json`** for machine-readable output; default is a short human-readable report.

This exits 0 by default — `decline` is the rule table's most severe label, not an
automatically enforced block. `--block-on {proceed_with_modification,decline}` exits 1
instead, for a caller that wants the exit code to reflect the verdict (mirrors
`privacy-linter`'s own `--block-on`). See `references/decision-rubric.md`'s closing
note for why this is opt-in rather than a default-behavior change: nothing invokes
`oracle.py` unattended today, unlike `privacy-linter`'s git hook.

## What's NOT built here

- **Free-text proposed-action parsing.** The original design's input was a sentence
  ("I want to post this to my public GitHub"); turning that into the five structured
  fields this script needs is a judgment call, not pattern-matching. Claude can do that
  translation conversationally before calling the script — a reasonable division of
  labor — but the script itself only accepts already-structured input. Not a silent
  scope cut: see `references/decision-rubric.md`'s Inputs section for the reasoning.
- **A calibration/validation pass against real expert judgment** (source project's Open
  Question #5). The rule table is reasoned from the vault's existing threat-modeling
  concept pages, not yet tested against real proposed-action scenarios.
- **Anything that actually calls `oracle.py` unattended.** `--block-on` (see above) only
  matters once some scripted or automated context invokes this and checks its exit
  code — none does yet, unlike `privacy-linter`'s git hook.
- **Live editing of the vault's threat model.** `references/threat-model.json` is the
  executable copy of the adversary/compartment schema already documented in
  `Knowledge/AI/privacy-threat-modeling.md` and the project page; keeping the two in
  sync by hand is the current state, the same relationship `privacy-linter`'s
  `references/leak-taxonomy.md` has to its own `scan_diff.py` patterns.

## Pairing

- **privacy-linter** — `--from-linter-json` consumes its `--json` output directly;
  content-class vocabulary is shared, not reinvented.
- **wiki-privacy-audit** — a natural next integration (not built): running the oracle
  over a vault-wide audit's findings the same way it already runs over a single
  linter scan.

## Files

| Path | What it is |
|---|---|
| `scripts/oracle.py` | The decision engine — rule table, linter-JSON bridge, CLI |
| `scripts/test_oracle.py` | Unit + CLI test suite (stdlib unittest) |
| `references/threat-model.json` | The adversary/compartment schema data (executable copy of the vault's threat-modeling pages) |
| `references/decision-rubric.md` | The rule table itself, with rationale for every branch |
