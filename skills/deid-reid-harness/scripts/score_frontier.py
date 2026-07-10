#!/usr/bin/env python3
"""
Privacy-utility frontier — THE deliverable, assembled across defenders.

A de-identifier is never scored alone: its privacy number only means something next to
what it destroys. This orchestrator runs each defender over ONE corpus and reports a
(privacy, utility) pair per defender, so you can see the tradeoff instead of a
leaderboard. It reuses the two model-independent scorers directly (no subprocess):

    privacy  = Safe Harbor coverage      (score_leakage: caught identifiers / total)
    utility  = clinical preservation     (score_utility: surviving clinical spans / total)

The bundled defenders sit at opposite ends: `regex-baseline-v0` is low-privacy /
high-utility (it barely redacts, so clinical content is safe); `over-redact-v0` is
high-privacy / low-utility (it sweeps up names AND clobbers the age and capitalized
diagnoses). Neither dominates — that gap is the frontier, and the point of the harness.

Requires a corpus generated with --utility (clinical spans) so both axes exist.

Usage:
    python generate_corpus.py --n 50 --seed 20260101 --utility --out corpus.json
    python score_frontier.py --corpus corpus.json --out frontier.json
    python score_frontier.py --corpus corpus.json --pipelines regex-baseline-v0 over-redact-v0
"""
from __future__ import annotations
import argparse, json
from deid_pipelines import get_pipeline, REGISTRY
from score_leakage import score_record as leak_score, aggregate as leak_aggregate
from score_utility import score_record as util_score, aggregate as util_aggregate


def frontier_point(pipe, records):
    leak_results, util_results = [], []
    for rec in records:
        scrubbed, redacted = pipe.scrub(rec["note_text"])
        leak_results.extend(leak_score({"identifiers": rec["identifiers"]}, scrubbed, redacted))
        util_results.extend(util_score(rec.get("clinical_spans") or [], scrubbed, redacted))
    privacy = leak_aggregate(leak_results)["overall"]["coverage"]
    utility = util_aggregate(util_results)["overall"]["utility"]
    return {"pipeline": pipe.name, "privacy": privacy, "utility": utility}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="corpus with clinical_spans (--utility)")
    ap.add_argument("--pipelines", nargs="+", default=list(REGISTRY),
                    help="defender names to compare (default: all registered)")
    ap.add_argument("--out", default="frontier.json")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    if not any(r.get("clinical_spans") for r in corpus["records"]):
        raise SystemExit("corpus has no clinical_spans — regenerate with `--utility`.")

    points = [frontier_point(get_pipeline(name), corpus["records"]) for name in args.pipelines]
    report = {"track": "privacy-utility-frontier", "sample_size": len(corpus["records"]),
              "axes": {"privacy": "Safe Harbor coverage (Track 1)",
                       "utility": "clinical-span preservation"},
              "points": points}
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Privacy-utility frontier  (n={report['sample_size']})")
    print(f"  {'defender':20s} {'privacy':>9s} {'utility':>9s}")
    for p in points:
        print(f"  {p['pipeline']:20s} {p['privacy']:>9.3f} {p['utility']:>9.3f}")
    print("\n  privacy = Safe Harbor coverage (caught identifiers); "
          "utility = clinical content preserved.")
    print("  A defender that beats another on BOTH axes dominates; otherwise the choice "
          "is a tradeoff.\n")
    print(f"Full report -> {args.out}")


if __name__ == "__main__":
    main()
