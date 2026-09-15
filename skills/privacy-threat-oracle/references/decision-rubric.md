# Decision rubric

`oracle.py`'s recommendation is a deterministic rule table, not a weighted scoring
function — the source project's own Open Question #2 ("how to weight adversary ×
sensitivity × compartment × cost-to-defend") is answered here as: **don't weight, rank.**
An ordered rule table is auditable and testable in a way a tunable weighted formula
isn't, and nothing about this problem has demonstrated it needs continuous weights
over discrete tiers. If real usage later shows the rule table is too coarse, that's
the trigger to revisit — not a default assumption now.

## Inputs (structured only — v1 does not parse free text)

| Field | Values | What it represents |
|---|---|---|
| `source_compartment` | `public_professional`, `personal`, `sensitive_research` | Which identity is taking the action |
| `target_compartment` | same three | Which identity context the content would effectively be attributed to |
| `target_exposure` | `public_internet`, `specific_person`, `close_group`, `employer_visible` | Who can actually see it — drives which adversary classes are reachable |
| `content_classes` | zero or more of `direct_pii`, `secret`, `metadata`, `inference_cue`, `stylometric`, or `none` | What's actually in the content — same vocabulary `privacy-linter` uses, so its `--json` output plugs in directly |
| `reversible` | bool, default `false` | Can this be deleted/retracted after the fact |

Free-text proposed-action parsing (the original design's "I want to post this to my
public GitHub" input) is deliberately deferred — see `SKILL.md`'s "What's NOT built
here." Turning a sentence into these five structured fields needs judgment a regex
can't provide; Claude can do that translation step conversationally before calling
the script, which is a reasonable division of labor, but the script itself only
accepts already-structured input.

## Step 1 — compartment violation

`target_compartment` is a violation if it's not in `source_compartment`'s
`may_reference` list (see `threat-model.json`). All three compartments only reference
themselves, so any source ≠ target compartment pairing is a violation — cross-
compartment linkage is the exact aggregation risk compartmentalization exists to
prevent, independent of content sensitivity.

**Confirmed 2026-09-15: full mutual isolation is the intended policy, not a v1
placeholder.** The vault's own design doc briefly read ambiguously here — it described
`sensitive_research` as isolated from "both others" but didn't explicitly say `personal`
was isolated from `public_professional`, which could be misread as permitting the two to
mix. They don't. `personal` explicitly includes family content, which alone is reason
enough to keep it walled off from `public_professional` too, not just from
`sensitive_research`. This file's self-only `may_reference` for every compartment was
already the correct encoding; the vault prose has since been corrected to match
(`Projects/Privacy OS - Threat-Model Decision Engine.md` and
`Knowledge/AI/privacy-threat-modeling.md`).

## Step 2 — exposed adversaries and content-sensitivity tier

`exposed_adversaries = target_exposure_map[target_exposure]`. `sensitivity_tier` is
the highest tier among `content_classes` present, via `content_sensitivity_tiers`
(`high` > `medium` > `none`). `adversary_cost_tier` is the highest `cost_to_defend`
among the exposed adversaries.

## Step 3 — recommendation table

Applied top to bottom, first match wins:

| Compartment violation? | Sensitivity | High-cost adversary exposed? | Recommendation | Residual risk |
|---|---|---|---|---|
| yes | `high` | — | `decline` | `high` |
| yes | `medium` or `none` | — | `proceed_with_modification` | `medium` |
| no | `high` | yes | `decline` | `high` |
| no | `high` | no | `proceed_with_modification` | `medium` |
| no | `medium` | yes | `proceed_with_modification` | `medium` |
| no | `medium` | no | `proceed` | `low` |
| no | `none` | — | `proceed` | `low` |

## Step 4 — reversibility downgrade

If `reversible` is true, downgrade one notch: `decline` → `proceed_with_modification`,
`proceed_with_modification` → `proceed`. `proceed` stays `proceed`. This models that a
retractable action (can delete the post, can revoke the share) carries genuinely lower
cost than an irretractable one, even at the same nominal sensitivity — but it never
turns a compartment-violation-plus-high-sensitivity case into an unqualified `proceed`;
it only ever moves one step, matching this tool's advisory (never silently-safe)
posture.

## Out-of-scope adversaries

`threat-model.json` can flag an adversary class `out_of_scope: true` — currently just
`state_actor`, per `Knowledge/AI/privacy-threat-modeling.md`'s cost-to-defend rationale:
meaningfully defending against a state actor means real operational security
(infrastructure hardening, traffic analysis resistance), categorically different from
"add 2FA" or "opt out of a data broker," and out of reach for a personal-OS v1 tool.

This flag is **informational, not a rule-table input**. An out-of-scope adversary still
counts toward `adversary_cost_tier` and the recommendation exactly like any other
adversary — Step 3's table doesn't know or care whether the high-cost adversary driving
a `decline` is one you can realistically do something about. What the flag does is
surface, separately, which of the exposed adversaries fall in that category:
`out_of_scope_adversaries_exposed` in the JSON output, and a trailing note in `reason`
when non-empty. The point isn't to soften or override the recommendation — it's so a
`decline` triggered partly or wholly by an adversary this tool can't help you defend
against reads differently than one triggered by an adversary it can (e.g. the stalker
case, which the reversibility downgrade and compartment fixes above genuinely address).

Whether `out_of_scope` *should* eventually exclude an adversary from the cost-tier
calculation entirely (rather than just being noted) is still an open call — flagged
here rather than decided, pending real usage. See the "Update cadence" and "Oracle
calibration" open questions on the design doc's project page.

## Why `decline` is advisory language, not a block by default

This tool exits 0 by default — like `privacy-linter`, it reports a recommendation
without gating anything unless asked to. `decline` means "the rule table's most severe
bucket," not "action refused." As of 2026-09-16, `--block-on {proceed_with_modification,
decline}` exists (mirroring `scan_diff.py`'s own `--block-on`) for a caller that wants
the exit code to reflect the verdict — but unlike `privacy-linter`'s git hook, nothing
invokes `oracle.py` unattended today, so there's no default behavior this flips: the
flag is opt-in capability, not a posture change. It only does something once some
scripted or automated context actually calls `oracle.py` and checks its exit code.
