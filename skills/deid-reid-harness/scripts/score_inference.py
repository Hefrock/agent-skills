#!/usr/bin/env python3
"""
Track 3 scorer — free-text inference. The attacker tries to recover a WITHHELD
diagnosis from a de-identified vignette that never names it; we score whether it
succeeded.

This is the AI-era threat the other two tracks structurally cannot see: the vignette
has no direct identifiers (Track 1 would pass it) and we are not counting quasi-
identifier equivalence classes (Track 2's job) — we are asking whether the surviving
clinical *context* still betrays a sensitive attribute. It frequently does.

Scoring is PROGRAMMATIC here because the target is drawn from a closed synthetic set:
a normalized string match against the manifest's ground-truth diagnosis is exact and
needs no judge. That keeps Track 3 verifiable offline, like Tracks 1-2. For an LLM
attacker producing free-text guesses ("a lysosomal storage disorder" for Fabry), swap
in agent-eval's LLM judge for semantic grading and confidence calibration — this scorer
already emits results in agent-eval's JSONL schema (see --eval-out).

Two things are reported, never one averaged number:
  * recovery — did the attacker name the withheld diagnosis? sliced by rare vs common
    and by diagnosis, because distinctive (rare) signatures leak far more than common
    overlapping ones — the same rare-diagnosis risk Track 2 flags as a quasi-identifier.
  * calibration — bucketed confidence vs actual accuracy. For the deterministic baseline
    this exercises the machinery; for an LLM attacker it answers "does a claimed 0.9
    mean 90% correct?" — the calibration agent-eval exists to check.

Usage:
    python score_inference.py --corpus corpus.json --attacker signature-match-v0 \
        --out inference_report.json --eval-out inference_eval.jsonl
    # then, optionally, aggregate/diff with agent-eval:
    python ../../agent-eval/scripts/score_eval.py inference_eval.jsonl
"""
from __future__ import annotations
import argparse, json
from collections import defaultdict
from inference_attackers import get_attacker


def normalize(s: str) -> str:
    return " ".join(s.lower().split()) if s else ""


def score_corpus(records, attacker):
    results = []
    for rec in records:
        case = rec["inference_case"]
        out = attacker.infer(case["note"])
        correct = normalize(out["guess"]) == normalize(case["target_value"])
        results.append({
            "record_id": rec["record_id"],
            "target": case["target_value"],
            "is_rare": case["is_rare"],
            "guess": out["guess"],
            "confidence": out["confidence"],
            "correct": correct,
            "abstained": out["guess"] is None,
            "rationale": out["rationale"],
        })
    return results


def calibration_bins(results, n_bins=5):
    """Bucket by stated confidence; compare mean confidence to actual accuracy."""
    bins = defaultdict(lambda: {"n": 0, "conf_sum": 0.0, "correct": 0})
    for r in results:
        idx = min(int(r["confidence"] * n_bins), n_bins - 1)  # 0..n_bins-1
        b = bins[idx]
        b["n"] += 1
        b["conf_sum"] += r["confidence"]
        b["correct"] += int(r["correct"])
    out = []
    for idx in sorted(bins):
        b = bins[idx]
        lo, hi = idx / n_bins, (idx + 1) / n_bins
        out.append({
            "bucket": f"{lo:.1f}-{hi:.1f}",
            "n": b["n"],
            "mean_confidence": round(b["conf_sum"] / b["n"], 3),
            "accuracy": round(b["correct"] / b["n"], 3),
        })
    return out


def rate(num, den):
    return round(num / den, 3) if den else None


def aggregate(results):
    n = len(results)
    correct = sum(r["correct"] for r in results)
    abstained = sum(r["abstained"] for r in results)
    by_class = defaultdict(lambda: {"n": 0, "recovered": 0})
    by_dx = defaultdict(lambda: {"n": 0, "recovered": 0})
    for r in results:
        cls = "rare" if r["is_rare"] else "common"
        by_class[cls]["n"] += 1
        by_class[cls]["recovered"] += int(r["correct"])
        by_dx[r["target"]]["n"] += 1
        by_dx[r["target"]]["recovered"] += int(r["correct"])

    def cov(d):
        return {k: {**v, "recovery": rate(v["recovered"], v["n"])}
                for k, v in sorted(d.items())}

    return {
        "track": "3-inference",
        "sample_size": n,
        "overall_recovery": rate(correct, n),
        "abstain_rate": rate(abstained, n),
        "by_class": cov(by_class),
        "by_diagnosis": cov(by_dx),
        "calibration": calibration_bins(results),
        "note": ("Recovery is the share of vignettes whose WITHHELD diagnosis the "
                 "attacker named from context alone. These notes carry no direct "
                 "identifiers — Safe Harbor (Track 1) passes them — yet the clinical "
                 "signature still leaks a sensitive attribute. That is inference risk, "
                 "which neither the checklist nor the linkage track can measure."),
        "per_record": results,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--attacker", default="signature-match-v0")
    ap.add_argument("--out", default="inference_report.json")
    ap.add_argument("--eval-out", default="inference_eval.jsonl",
                    help="per-case results in agent-eval's JSONL schema")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    records = [r for r in corpus["records"] if r.get("inference_case")]
    if not records:
        raise SystemExit(
            "corpus has no inference cases — regenerate with `--inference` so each "
            "record carries an inference_case vignette.")

    attacker = get_attacker(args.attacker)
    results = score_corpus(records, attacker)
    report = aggregate(results)
    report["attacker"] = attacker.name

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
    # agent-eval schema: {"id","score","category","rationale"} — feeds score_eval.py.
    with open(args.eval_out, "w") as f:
        for r in results:
            f.write(json.dumps({
                "id": r["record_id"],
                "score": 1.0 if r["correct"] else 0.0,
                "category": "rare" if r["is_rare"] else "common",
                "rationale": f"guessed {r['guess']!r} (conf {r['confidence']}) vs "
                             f"true {r['target']!r}",
            }) + "\n")

    print(f"Inference — Track 3  (attacker '{attacker.name}', n={report['sample_size']})")
    print(f"  overall recovery of withheld diagnosis: {report['overall_recovery']}  "
          f"(abstain rate {report['abstain_rate']})")
    for cls, d in report["by_class"].items():
        print(f"    {cls:7s} recovery={d['recovery']}  ({d['recovered']}/{d['n']})")
    print("  most-leaked diagnoses (highest recovery):")
    worst = sorted(report["by_diagnosis"].items(),
                   key=lambda kv: (kv[1]["recovery"] is not None, kv[1]["recovery"]),
                   reverse=True)[:4]
    for dx, d in worst:
        print(f"    {dx:32s} recovery={d['recovery']}  ({d['recovered']}/{d['n']})")
    print(f"\n  Full report -> {args.out}")
    print(f"  agent-eval rows -> {args.eval_out}  "
          f"(aggregate with agent-eval/scripts/score_eval.py)")


if __name__ == "__main__":
    main()
