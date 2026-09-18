#!/usr/bin/env python3
"""
oracle.py -- deterministic, rule-based privacy decision engine.

Evaluates a proposed action (structured, not free text -- see
references/decision-rubric.md for why) against the threat model in
references/threat-model.json and reports a recommendation. No model or network
call: this is a rule table over a fixed schema, the same "deterministic-first"
approach privacy-linter used for its own leak classes.

Usage:
    python oracle.py --source-compartment personal --target-compartment public_professional \\
        --target-exposure public_internet --content-class direct_pii

    python oracle.py --source-compartment sensitive_research --target-compartment sensitive_research \\
        --target-exposure close_group --content-class none --reversible

    # Pull content classes straight from a privacy-linter scan instead of naming them by hand:
    python ../privacy-linter/scripts/scan_diff.py --file draft_post.txt --json | \\
        python oracle.py --source-compartment personal --target-compartment personal \\
            --target-exposure public_internet --from-linter-json -

    python oracle.py --json ...   # machine-readable output

    # Exit 1 instead of 0 if the recommendation is 'decline' (or 'proceed_with_modification'):
    python oracle.py ... --block-on decline
"""
from __future__ import annotations
import argparse, json, os, sys

REFERENCES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references")
THREAT_MODEL_PATH = os.path.join(REFERENCES, "threat-model.json")

SENSITIVITY_RANK = {"none": 0, "medium": 1, "high": 2}
COST_RANK = {"low": 0, "medium": 1, "high": 2}
RECOMMENDATION_RANK = {"proceed": 0, "proceed_with_modification": 1, "decline": 2}
RECOMMENDATION_DOWNGRADE = {
    "decline": "proceed_with_modification",
    "proceed_with_modification": "proceed",
    "proceed": "proceed",
}


def check_gate(recommendation: str, block_on: str | None) -> bool:
    """Mirrors privacy-linter's scan_diff.py check_gate() -- same name, same shape,
    for the same reason: a caller scripting against either tool should be able to
    reuse the same mental model of what --block-on means. None means advisory only
    (matches this tool's default; unlike privacy-linter's git hook, nothing calls
    oracle.py unattended today, so there's no existing default behavior to flip)."""
    if block_on is None:
        return False
    return RECOMMENDATION_RANK[recommendation] >= RECOMMENDATION_RANK[block_on]


def load_threat_model(path: str = THREAT_MODEL_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compartment_choices(threat_model: dict) -> list[str]:
    return [c["id"] for c in threat_model["compartments"]]


def target_exposure_choices(threat_model: dict) -> list[str]:
    return list(threat_model["target_exposure_map"].keys())


def content_class_choices(threat_model: dict) -> list[str]:
    return list(threat_model["content_sensitivity_tiers"].keys())


# Derived once at import time from threat-model.json, not hardcoded -- the exact drift
# risk this closes: these choices used to be separate literal lists in main()'s
# argparse setup (and a third copy in test_oracle.py), with nothing to catch them
# silently diverging from the JSON if a compartment/exposure/content-class was ever
# added there and the CLI validation wasn't updated to match. "A living threat model...
# encoded as data" (SKILL.md) is only true in practice if the CLI actually reads from
# the data instead of duplicating it by hand.
_CHOICES_THREAT_MODEL = load_threat_model()
COMPARTMENT_CHOICES = compartment_choices(_CHOICES_THREAT_MODEL)
TARGET_EXPOSURE_CHOICES = target_exposure_choices(_CHOICES_THREAT_MODEL)
CONTENT_CLASS_CHOICES = content_class_choices(_CHOICES_THREAT_MODEL)


def content_classes_from_linter_json(raw: str) -> tuple[list[str], str | None]:
    """Extract a deduped list of content classes from a privacy-linter --json payload
    (a list of Finding objects each carrying a "leak_class" field -- see scan_diff.py's
    Finding dataclass). Returns (classes, error): error is None on success, or a short
    message describing why the input couldn't be read. Malformed input never raises --
    an oracle call should degrade gracefully, not crash, if the linter's output shape
    ever changes -- but the caller MUST NOT treat a non-None error the same as "the
    linter genuinely found nothing": evaluate()'s parse_error flag forces the
    sensitivity floor to "high" instead, so a broken pipe fails closed (forced toward
    'decline'/'proceed_with_modification') rather than open (silently reporting
    'proceed' as if nothing sensitive were found)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return [], f"could not parse --from-linter-json input as JSON ({e})"
    if not isinstance(data, list):
        return [], f"--from-linter-json input must be a JSON list of findings, got {type(data).__name__}"
    classes = []
    for finding in data:
        if isinstance(finding, dict) and isinstance(finding.get("leak_class"), str):
            if finding["leak_class"] not in classes:
                classes.append(finding["leak_class"])
    return classes, None


def sensitivity_tier(content_classes: list[str], threat_model: dict) -> str:
    tiers = threat_model["content_sensitivity_tiers"]
    ranked = [tiers.get(c, "none") for c in content_classes] or ["none"]
    return max(ranked, key=lambda t: SENSITIVITY_RANK.get(t, 0))


def exposed_adversaries(target_exposure: str, threat_model: dict) -> list[str]:
    return threat_model["target_exposure_map"].get(target_exposure, [])


def adversary_cost_tier(adversary_ids: list[str], threat_model: dict) -> str:
    by_id = {a["id"]: a for a in threat_model["adversary_classes"]}
    tiers = [by_id[a]["cost_to_defend"] for a in adversary_ids if a in by_id]
    if not tiers:
        return "low"
    return max(tiers, key=lambda t: COST_RANK.get(t, 0))


def out_of_scope_adversaries(adversary_ids: list[str], threat_model: dict) -> list[str]:
    """IDs from adversary_ids flagged out_of_scope in the threat model (currently just
    state_actor) -- adversaries modeled for completeness, so their cost_to_defend still
    counts toward adversary_cost_tier and the recommendation, but that this tool was
    never designed to meaningfully help defend against (see threat-model.json and
    Knowledge/AI/privacy-threat-modeling.md's cost-to-defend rationale). Purely
    informational: this doesn't change the rule table, it only tells the caller which
    part of "adversary exposed" they can't actually act on with this tool."""
    by_id = {a["id"]: a for a in threat_model["adversary_classes"]}
    return [a for a in adversary_ids if by_id.get(a, {}).get("out_of_scope")]


def is_compartment_violation(source_compartment: str, target_compartment: str, threat_model: dict) -> bool:
    by_id = {c["id"]: c for c in threat_model["compartments"]}
    source = by_id.get(source_compartment)
    if source is None:
        raise ValueError(f"unknown source_compartment: {source_compartment!r}")
    if target_compartment not in by_id:
        raise ValueError(f"unknown target_compartment: {target_compartment!r}")
    return target_compartment not in source["may_reference"]


def evaluate(
    source_compartment: str,
    target_compartment: str,
    target_exposure: str,
    content_classes: list[str],
    reversible: bool = False,
    threat_model: dict | None = None,
    parse_error: bool = False,
) -> dict:
    """The rule table from references/decision-rubric.md, applied top to bottom.

    parse_error=True means --from-linter-json input couldn't be read (see
    content_classes_from_linter_json) -- the sensitivity floor is forced to "high"
    in that case rather than falling back to whatever content_classes happens to be
    (likely empty), so an unreadable input fails closed instead of silently
    reporting the lowest-risk recommendation."""
    tm = threat_model or load_threat_model()
    if target_exposure not in tm["target_exposure_map"]:
        raise ValueError(f"unknown target_exposure: {target_exposure!r}")

    violation = is_compartment_violation(source_compartment, target_compartment, tm)
    exposed = exposed_adversaries(target_exposure, tm)
    sensitivity = "high" if parse_error else sensitivity_tier(content_classes, tm)
    cost_tier = adversary_cost_tier(exposed, tm)
    high_cost_exposed = cost_tier == "high"
    out_of_scope_exposed = out_of_scope_adversaries(exposed, tm)

    if violation and sensitivity == "high":
        recommendation, residual_risk = "decline", "high"
        reason = "Compartment violation with high-sensitivity content."
    elif violation:
        recommendation, residual_risk = "proceed_with_modification", "medium"
        reason = f"Compartment violation: '{source_compartment}' does not reference '{target_compartment}'."
    elif sensitivity == "high" and high_cost_exposed:
        recommendation, residual_risk = "decline", "high"
        reason = "High-sensitivity content reaching a high-cost-to-defend adversary."
    elif sensitivity == "high":
        recommendation, residual_risk = "proceed_with_modification", "medium"
        reason = "High-sensitivity content, but no high-cost-to-defend adversary exposed."
    elif sensitivity == "medium" and high_cost_exposed:
        recommendation, residual_risk = "proceed_with_modification", "medium"
        reason = "Medium-sensitivity content reaching a high-cost-to-defend adversary."
    elif sensitivity == "medium":
        recommendation, residual_risk = "proceed", "low"
        reason = "Medium-sensitivity content, no high-cost-to-defend adversary exposed."
    else:
        recommendation, residual_risk = "proceed", "low"
        reason = "No flagged content sensitivity."

    if parse_error:
        reason = (
            "WARNING: --from-linter-json input could not be read, so content sensitivity "
            "could not be determined. Treating it as high (fail-closed) rather than assuming "
            "nothing sensitive was found -- do not treat this recommendation as validated "
            "until the input is fixed and re-run. " + reason
        )

    if out_of_scope_exposed:
        by_id = {a["id"]: a for a in tm["adversary_classes"]}
        names = ", ".join(by_id[a]["name"] for a in out_of_scope_exposed)
        reason += (
            f" Note: this exposure also reaches {names}, which this tool treats as "
            f"out-of-scope -- modeled for cost purposes, but not something this tool "
            f"can meaningfully help you defend against."
        )

    downgraded = False
    if reversible and recommendation != "proceed":
        recommendation = RECOMMENDATION_DOWNGRADE[recommendation]
        downgraded = True
        reason += " Downgraded one step: action is reversible."

    return {
        "compartment_violation": violation,
        "exposed_adversaries": exposed,
        "out_of_scope_adversaries_exposed": out_of_scope_exposed,
        "sensitivity_tier": sensitivity,
        "adversary_cost_tier": cost_tier,
        "reversible": reversible,
        "reversibility_applied": downgraded,
        "linter_json_parse_error": parse_error,
        "recommendation": recommendation,
        "residual_risk": residual_risk,
        "reason": reason,
    }


def format_human(result: dict) -> str:
    lines = []
    if result.get("linter_json_parse_error"):
        lines.append("*** WARNING: --from-linter-json input could not be read -- see reason below ***")
    lines += [
        f"[{result['residual_risk']}] recommendation: {result['recommendation']}",
        f"  reason: {result['reason']}",
        f"  compartment_violation: {result['compartment_violation']}",
        f"  exposed_adversaries: {', '.join(result['exposed_adversaries']) or '(none)'}",
        f"  out_of_scope_adversaries_exposed: {', '.join(result['out_of_scope_adversaries_exposed']) or '(none)'}",
        f"  sensitivity_tier: {result['sensitivity_tier']}  adversary_cost_tier: {result['adversary_cost_tier']}",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Deterministic privacy threat-model oracle.")
    ap.add_argument("--source-compartment", required=True, choices=COMPARTMENT_CHOICES)
    ap.add_argument("--target-compartment", required=True, choices=COMPARTMENT_CHOICES)
    ap.add_argument("--target-exposure", required=True, choices=TARGET_EXPOSURE_CHOICES)
    ap.add_argument("--content-class", action="append", default=[],
                     choices=CONTENT_CLASS_CHOICES,
                     help="Repeatable. Omit (or pass 'none') for content with no flagged sensitivity.")
    ap.add_argument("--from-linter-json", metavar="PATH",
                     help="Read a privacy-linter --json payload ('-' for stdin) and add its finding classes to --content-class.")
    ap.add_argument("--reversible", action="store_true", help="The action can be deleted/retracted after the fact.")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    ap.add_argument("--block-on", choices=["proceed_with_modification", "decline"], default=None,
                     help="Exit 1 if the recommendation is at or above this level. Default: advisory "
                          "only, always exits 0 (matches privacy-linter's --block-on convention).")
    args = ap.parse_args()

    content_classes = list(args.content_class)
    parse_error = None
    if args.from_linter_json:
        raw = sys.stdin.read() if args.from_linter_json == "-" else open(args.from_linter_json, encoding="utf-8").read()
        linter_classes, parse_error = content_classes_from_linter_json(raw)
        for c in linter_classes:
            if c not in content_classes:
                content_classes.append(c)
        if parse_error:
            print(f"privacy-threat-oracle: warning: {parse_error} -- "
                  f"treating content sensitivity as high (fail-closed), not as 'nothing found'",
                  file=sys.stderr)

    try:
        result = evaluate(
            source_compartment=args.source_compartment,
            target_compartment=args.target_compartment,
            target_exposure=args.target_exposure,
            content_classes=content_classes,
            reversible=args.reversible,
            parse_error=bool(parse_error),
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2) if args.json else format_human(result))

    if check_gate(result["recommendation"], args.block_on):
        if not args.json:
            print(f"\nBLOCKED: recommendation '{result['recommendation']}' is at or above "
                  f"'{args.block_on}'.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
