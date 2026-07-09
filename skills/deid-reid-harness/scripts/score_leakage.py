#!/usr/bin/env python3
"""
Track 1 scorer — Safe Harbor leakage. DETERMINISTIC, no judge.

A ground-truth identifier instance LEAKED if it survives into the scrubbed note. We
determine survival two ways depending on what the defender reports:

  * Exact (preferred): the defender returned the char spans it redacted. A ground-truth
    span is CAUGHT iff it is covered by a redacted span, else LEAKED. Offset-aligned,
    unambiguous.
  * Fallback: black-box defender returned only text. A ground-truth span is LEAKED iff
    its exact surface string still occurs in the scrubbed text (counting occurrences to
    avoid over-counting coincidental substrings). Documented as approximate.

Output is coverage broken down by category × surface_form × context — never a single
averaged number, because averaging hides exactly the gaps (spelled-out dates in
narrative) that matter. Results are labeled Safe Harbor COVERAGE, not re-id risk.
"""
from __future__ import annotations
import argparse, json
from collections import defaultdict

# The 18 canonical hipaa_category values (see references/safe-harbor-identifiers.md).
# Used to report which categories a corpus never exercised, so a coverage number over
# 7 categories can't be mistaken for coverage of the full checklist.
ALL_CATEGORIES = [
    "name", "geo_subdivision", "date", "phone_fax", "fax", "email", "ssn", "mrn",
    "health_plan_id", "account_number", "license_number", "vehicle_id", "device_id",
    "url", "ip_address", "biometric_id", "photo", "other_unique_id",
]

def span_covered(gt, redacted_spans) -> bool:
    """True if ground-truth span gt is fully inside some SINGLE redacted span.
    A defender that redacts a ground-truth span in two adjacent chunks scores as a
    leak — conservative on purpose; merge adjacent redactions in the pipeline if
    that penalty is unwanted."""
    for r in redacted_spans:
        if r["start"] <= gt["start"] and r["end"] >= gt["end"]:
            return True
    return False

def score_record(rec, scrubbed_text, redacted_spans):
    results = []
    exact = redacted_spans is not None
    leaked_by_id = {}
    if exact:
        for gt in rec["identifiers"]:
            leaked_by_id[gt["span_id"]] = not span_covered(gt, redacted_spans)
    else:
        # Fallback: occurrence budget per literal. If a literal was injected k times
        # and survives c times in the scrubbed text, count min(k, c) instances as
        # leaked instead of marking all k leaked on a single coincidental survival.
        groups = defaultdict(list)
        for gt in rec["identifiers"]:
            groups[gt["text"]].append(gt["span_id"])
        for text, span_ids in groups.items():
            budget = scrubbed_text.count(text)
            for i, sid in enumerate(span_ids):
                leaked_by_id[sid] = i < budget
    for gt in rec["identifiers"]:
        results.append({
            "span_id": gt["span_id"], "hipaa_category": gt["hipaa_category"],
            "surface_form": gt["surface_form"], "context": gt["context"],
            "leaked": leaked_by_id[gt["span_id"]],
            "mode": "exact" if exact else "fallback",
        })
    return results

def aggregate(all_results):
    # coverage = caught / total, sliced multiple ways
    def bucket():
        return {"total": 0, "leaked": 0}
    by_cat = defaultdict(bucket)
    by_cat_form = defaultdict(bucket)
    by_context = defaultdict(bucket)
    overall = bucket()
    for r in all_results:
        for key, d in ((r["hipaa_category"], by_cat),
                       (f'{r["hipaa_category"]}::{r["surface_form"]}', by_cat_form),
                       (r["context"], by_context)):
            d[key]["total"] += 1
            d[key]["leaked"] += int(r["leaked"])
        overall["total"] += 1
        overall["leaked"] += int(r["leaked"])
    def cov(d):
        return {k: {**v, "coverage": round(1 - v["leaked"]/v["total"], 3)}
                for k, v in sorted(d.items())}
    ov = round(1 - overall["leaked"]/overall["total"], 3) if overall["total"] else None
    return {
        "overall": {**overall, "coverage": ov},
        "categories_exercised": sorted(by_cat),
        "categories_not_exercised": [c for c in ALL_CATEGORIES if c not in by_cat],
        "by_category": cov(by_cat),
        "by_category_surface_form": cov(by_cat_form),
        "by_context": cov(by_context),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True, help="JSONL from run_track1: one record per line")
    ap.add_argument("--out", default="leakage_report.json")
    args = ap.parse_args()
    all_results = []
    with open(args.runs) as f:
        for line in f:
            row = json.loads(line)
            all_results.extend(score_record(
                {"identifiers": row["identifiers"]},
                row["scrubbed_text"], row.get("redacted_spans")))
    report = aggregate(all_results)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    ov = report["overall"]
    n_ex = len(report["categories_exercised"])
    print(f"Safe Harbor COVERAGE (caught/total): {ov['coverage']}  "
          f"[{ov['total']-ov['leaked']}/{ov['total']}]  "
          f"over {n_ex}/18 categories")
    if report["categories_not_exercised"]:
        print(f"NOT exercised by this corpus: "
              f"{', '.join(report['categories_not_exercised'])}")
    print("\nWorst categories (most leakage):")
    worst = sorted(report["by_category"].items(), key=lambda kv: kv[1]["coverage"])[:5]
    for cat, d in worst:
        print(f"  {cat:18s} coverage={d['coverage']:.3f}  leaked={d['leaked']}/{d['total']}")
    print(f"\nFull breakdown -> {args.out}")

if __name__ == "__main__":
    main()
