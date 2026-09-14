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
`may_reference` list (see `threat-model.json`). All three compartments currently
only reference themselves, so any source ≠ target compartment pairing is a
violation — cross-compartment linkage is the exact aggregation risk
compartmentalization exists to prevent, independent of content sensitivity.

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

## Why `decline` is advisory language, not a block

This tool always exits 0 — like `privacy-linter`, it reports a recommendation, it does
not gate anything by default. `decline` means "the rule table's most severe bucket,"
not "action refused." Wiring an actual block (e.g. a CI-style `--block-on decline`) is
a natural extension, not built here, to keep the advisory-first design of the sibling
projects.
