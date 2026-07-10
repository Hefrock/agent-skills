#!/usr/bin/env python3
"""
Utility scorer — the SECOND axis. DETERMINISTIC, no judge.

The harness's cardinal rule: a privacy number reported without a paired utility number
is meaningless, because redacting the entire note gives perfect privacy and zero value.
This scorer measures what a de-id pipeline DESTROYS. It is the exact mirror of the Track
1 leakage scorer:

  * Track 1 (leakage):  an IDENTIFIER span must NOT survive scrubbing. Coverage = caught.
  * Utility (this):     a CLINICAL span MUST survive scrubbing. Utility  = preserved.

Both read the same run rows (`run_track1.py` output) and the same exact-by-construction
spans, so a defender gets a (privacy, utility) pair from one run over one corpus. That
pair is a point on the privacy-utility frontier — the real deliverable, assembled across
defenders by score_frontier.py.

A clinical span is PRESERVED iff no redacted span overlaps it (any overlap breaks the
term). Exact when the defender reports its redaction spans; a black-box defender
(spans=None) falls back to an occurrence-budget presence check, documented as approximate.

Usage:
    python score_utility.py --corpus corpus.json --runs runs.jsonl --out utility_report.json
    # pair with the privacy axis for a frontier point:
    python score_utility.py --corpus corpus.json --runs runs.jsonl --leakage-report leakage_report.json
"""
from __future__ import annotations
import argparse, json
from collections import defaultdict


def overlaps(a, b) -> bool:
    """True if [a.start,a.end) and [b.start,b.end) intersect."""
    return a["start"] < b["end"] and b["start"] < a["end"]


def score_record(clinical_spans, scrubbed_text, redacted_spans):
    exact = redacted_spans is not None
    results = []
    if not exact:
        # Fallback: a clinical literal is preserved while the scrubbed text still has an
        # unspent occurrence of it. Approximate (coincidental substrings), like Track 1.
        budget = {}
        for c in clinical_spans:
            budget.setdefault(c["text"], scrubbed_text.count(c["text"]))
    for c in clinical_spans:
        if exact:
            preserved = not any(overlaps(c, r) for r in redacted_spans)
        else:
            preserved = budget.get(c["text"], 0) > 0
            if preserved:
                budget[c["text"]] -= 1
        results.append({
            "span_id": c["span_id"], "clinical_category": c["clinical_category"],
            "preserved": preserved, "mode": "exact" if exact else "fallback",
        })
    return results


def aggregate(all_results):
    def bucket():
        return {"total": 0, "preserved": 0}
    by_cat = defaultdict(bucket)
    overall = bucket()
    for r in all_results:
        d = by_cat[r["clinical_category"]]
        d["total"] += 1
        d["preserved"] += int(r["preserved"])
        overall["total"] += 1
        overall["preserved"] += int(r["preserved"])

    def util(d):
        return {k: {**v, "utility": round(v["preserved"] / v["total"], 3)}
                for k, v in sorted(d.items())}

    ov = round(overall["preserved"] / overall["total"], 3) if overall["total"] else None
    return {"overall": {**overall, "utility": ov}, "by_clinical_category": util(by_cat)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="corpus with clinical_spans (--utility)")
    ap.add_argument("--runs", required=True, help="JSONL from run_track1 (has redacted_spans)")
    ap.add_argument("--leakage-report", help="optional Track 1 report, to print a frontier point")
    ap.add_argument("--out", default="utility_report.json")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    clinical_by_id = {r["record_id"]: r.get("clinical_spans") for r in corpus["records"]}
    if not any(clinical_by_id.values()):
        raise SystemExit("corpus has no clinical_spans — regenerate with `--utility`.")

    all_results, pipeline = [], None
    with open(args.runs) as f:
        for line in f:
            row = json.loads(line)
            pipeline = row.get("pipeline", pipeline)
            cs = clinical_by_id.get(row["record_id"]) or []
            all_results.extend(score_record(cs, row["scrubbed_text"], row.get("redacted_spans")))

    report = aggregate(all_results)
    report["pipeline"] = pipeline
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    ov = report["overall"]
    print(f"Clinical UTILITY (preserved/total) for '{pipeline}': {ov['utility']}  "
          f"[{ov['preserved']}/{ov['total']}]")
    print("\nBy clinical category:")
    for cat, d in sorted(report["by_clinical_category"].items()):
        print(f"  {cat:12s} utility={d['utility']:.3f}  preserved={d['preserved']}/{d['total']}")

    if args.leakage_report:
        leak = json.load(open(args.leakage_report))
        privacy = leak["overall"]["coverage"]
        print(f"\nFrontier point for '{pipeline}':  "
              f"privacy(Safe Harbor coverage)={privacy}   utility={ov['utility']}")
        print("  (privacy without utility is meaningless — always report the pair)")
    print(f"\nFull breakdown -> {args.out}")


if __name__ == "__main__":
    main()
