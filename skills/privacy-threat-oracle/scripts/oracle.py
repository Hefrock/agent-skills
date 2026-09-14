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
"""
from __future__ import annotations
import argparse, json, os, sys

REFERENCES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references")
THREAT_MODEL_PATH = os.path.join(REFERENCES, "threat-model.json")

SENSITIVITY_RANK = {"none": 0, "medium": 1, "high": 2}
COST_RANK = {"low": 0, "medium": 1, "high": 2}
RECOMMENDATION_DOWNGRADE = {
    "decline": "proceed_with_modification",
    "proceed_with_modification": "proceed",
    "proceed": "proceed",
}


def load_threat_model(path: str = THREAT_MODEL_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def content_classes_from_linter_json(raw: str) -> list[str]:
    """Extract a deduped list of content classes from a privacy-linter --json payload
    (a list of finding objects each carrying a "class" field). Unknown/malformed input
    yields an empty list rather than raising -- an oracle call should degrade to "no
    known content classes", not crash, if the linter's output shape ever changes."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    classes = []
    for finding in data:
        if isinstance(finding, dict) and isinstance(finding.get("class"), str):
            if finding["class"] not in classes:
                classes.append(finding["class"])
    return classes


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
) -> dict:
    """The rule table from references/decision-rubric.md, applied top to bottom."""
    tm = threat_model or load_threat_model()
    if target_exposure not in tm["target_exposure_map"]:
        raise ValueError(f"unknown target_exposure: {target_exposure!r}")

    violation = is_compartment_violation(source_compartment, target_compartment, tm)
    exposed = exposed_adversaries(target_exposure, tm)
    sensitivity = sensitivity_tier(content_classes, tm)
    cost_tier = adversary_cost_tier(exposed, tm)
    high_cost_exposed = cost_tier == "high"

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

    downgraded = False
    if reversible and recommendation != "proceed":
        recommendation = RECOMMENDATION_DOWNGRADE[recommendation]
        downgraded = True
        reason += " Downgraded one step: action is reversible."

    return {
        "compartment_violation": violation,
        "exposed_adversaries": exposed,
        "sensitivity_tier": sensitivity,
        "adversary_cost_tier": cost_tier,
        "reversible": reversible,
        "reversibility_applied": downgraded,
        "recommendation": recommendation,
        "residual_risk": residual_risk,
        "reason": reason,
    }


def format_human(result: dict) -> str:
    lines = [
        f"[{result['residual_risk']}] recommendation: {result['recommendation']}",
        f"  reason: {result['reason']}",
        f"  compartment_violation: {result['compartment_violation']}",
        f"  exposed_adversaries: {', '.join(result['exposed_adversaries']) or '(none)'}",
        f"  sensitivity_tier: {result['sensitivity_tier']}  adversary_cost_tier: {result['adversary_cost_tier']}",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Deterministic privacy threat-model oracle.")
    ap.add_argument("--source-compartment", required=True, choices=["public_professional", "personal", "sensitive_research"])
    ap.add_argument("--target-compartment", required=True, choices=["public_professional", "personal", "sensitive_research"])
    ap.add_argument("--target-exposure", required=True, choices=["public_internet", "specific_person", "close_group", "employer_visible"])
    ap.add_argument("--content-class", action="append", default=[],
                     choices=["direct_pii", "secrets", "metadata", "inference_cue", "stylometric", "none"],
                     help="Repeatable. Omit (or pass 'none') for content with no flagged sensitivity.")
    ap.add_argument("--from-linter-json", metavar="PATH",
                     help="Read a privacy-linter --json payload ('-' for stdin) and add its finding classes to --content-class.")
    ap.add_argument("--reversible", action="store_true", help="The action can be deleted/retracted after the fact.")
    ap.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = ap.parse_args()

    content_classes = list(args.content_class)
    if args.from_linter_json:
        raw = sys.stdin.read() if args.from_linter_json == "-" else open(args.from_linter_json, encoding="utf-8").read()
        for c in content_classes_from_linter_json(raw):
            if c not in content_classes:
                content_classes.append(c)

    try:
        result = evaluate(
            source_compartment=args.source_compartment,
            target_compartment=args.target_compartment,
            target_exposure=args.target_exposure,
            content_classes=content_classes,
            reversible=args.reversible,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2) if args.json else format_human(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
