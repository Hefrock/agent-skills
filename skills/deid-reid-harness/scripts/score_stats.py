#!/usr/bin/env python3
"""
Statistical rigor for the harness's headline numbers.

Every number reported so far (0.449 Track 1 coverage, the frontier's two corners, 0.94
Track 3 recovery, 49/50 cross-track) was a single point estimate on one corpus, one
seed, n=50. That invites the obvious reviewer question: is a gap real, or noise? This
script answers it two ways:

  1. BOOTSTRAP (one corpus): a 95% confidence interval on each headline metric, using a
     cluster bootstrap over RECORDS (see bootstrap.py for why records, not spans, are
     the resampling unit). Plus a PAIRED bootstrap test of whether two defenders' privacy
     (or utility) genuinely differ on the SAME records — the correct comparison here,
     since both pipelines score the identical corpus.
  2. SEED SWEEP (many corpora): regenerate the corpus from scratch across K independent
     seeds and report how much each metric moves. This checks a different thing than the
     bootstrap CI — it asks whether the RESULT is an artifact of one particular
     synthetic-generation draw, not just sampling noise within one draw.

All bootstrap randomness is seeded (see bootstrap.py) so results reproduce exactly.

Usage:
    python generate_corpus.py --n 50 --seed 20260101 --population 100000 --inference --utility \
        --out corpus.json --population-out population.jsonl
    python score_stats.py --corpus corpus.json --out stats.json
    python score_stats.py --corpus corpus.json --seeds 10 --out stats_sweep.json
"""
from __future__ import annotations
import argparse, json, os

from deid_pipelines import get_pipeline, REGISTRY
import score_leakage
import score_utility
from score_inference import score_corpus as inference_score_corpus, normalize
from inference_attackers import get_attacker
import score_crosstrack
from score_reid import load_population, resolve_population_path
from bootstrap import bootstrap_ratio_ci, paired_bootstrap_diff, DEFAULT_N_BOOT, DEFAULT_BOOT_SEED
import generate_corpus as gc


# --- Per-record (numerator, denominator) extraction, one pair per record/pipeline -----

def _leakage_pairs(records, pipe):
    pairs = []
    for rec in records:
        scrubbed, redacted = pipe.scrub(rec["note_text"])
        results = score_leakage.score_record({"identifiers": rec["identifiers"]}, scrubbed, redacted)
        caught = sum(1 for r in results if not r["leaked"])
        pairs.append((caught, len(results)))
    return pairs


def _utility_pairs(records, pipe):
    pairs = []
    for rec in records:
        cs = rec.get("clinical_spans") or []
        if not cs:
            continue
        scrubbed, redacted = pipe.scrub(rec["note_text"])
        results = score_utility.score_record(cs, scrubbed, redacted)
        preserved = sum(1 for r in results if r["preserved"])
        pairs.append((preserved, len(results)))
    return pairs


def _inference_pairs(records, attacker_name):
    attacker = get_attacker(attacker_name)
    results = inference_score_corpus(records, attacker)
    return [(1 if r["correct"] else 0, 1) for r in results]


def _crosstrack_pairs(corpus, population, attacker_name, k):
    per_record = score_crosstrack.compute_per_record(corpus, population, attacker_name, k)
    return [(1 if r["safe_harbor_but_vulnerable"] else 0, 1) for r in per_record]


# --- Mode 1: bootstrap over one corpus -------------------------------------------------

def bootstrap_report(corpus: dict, corpus_path: str, pipelines: list, attacker_name: str,
                     population_path: str, n_boot: int, boot_seed: int) -> dict:
    records = corpus["records"]
    report = {"mode": "bootstrap", "sample_size": len(records),
              "n_boot": n_boot, "boot_seed": boot_seed, "privacy": {}, "utility": {}}

    pairs_by_pipe = {}
    for name in pipelines:
        pipe = get_pipeline(name)
        leak_pairs = _leakage_pairs(records, pipe)
        pairs_by_pipe[name] = {"leakage": leak_pairs}
        report["privacy"][name] = bootstrap_ratio_ci(leak_pairs, n_boot, boot_seed)
        util_pairs = _utility_pairs(records, pipe)
        if util_pairs:
            pairs_by_pipe[name]["utility"] = util_pairs
            report["utility"][name] = bootstrap_ratio_ci(util_pairs, n_boot, boot_seed)

    if len(pipelines) == 2:
        a, b = pipelines
        report["privacy_diff"] = paired_bootstrap_diff(
            pairs_by_pipe[a]["leakage"], pairs_by_pipe[b]["leakage"], n_boot, boot_seed)
        if "utility" in pairs_by_pipe[a] and "utility" in pairs_by_pipe[b]:
            report["utility_diff"] = paired_bootstrap_diff(
                pairs_by_pipe[a]["utility"], pairs_by_pipe[b]["utility"], n_boot, boot_seed)

    if all(r.get("inference_case") for r in records):
        inf_pairs = _inference_pairs(records, attacker_name)
        report["inference_recovery"] = bootstrap_ratio_ci(inf_pairs, n_boot, boot_seed)

    if corpus.get("population_ref") or population_path:
        try:
            pop_path = resolve_population_path(population_path, corpus, corpus_path)
            pop = load_population(pop_path)
        except SystemExit:
            pop = None
        if pop is not None and all(r.get("inference_case") for r in records):
            xt_pairs = _crosstrack_pairs(corpus, pop, attacker_name, k=5)
            report["crosstrack_vulnerable"] = bootstrap_ratio_ci(xt_pairs, n_boot, boot_seed)

    return report


# --- Mode 2: seed sweep — regenerate the corpus across independent seeds --------------

def seed_sweep_report(n_records: int, seeds: list, pipelines: list, attacker_name: str,
                      with_population: int, with_inference: bool, with_utility: bool) -> dict:
    per_seed = []
    for seed in seeds:
        pop_path = None
        corpus = gc.generate(n_records, seed, with_inference=with_inference, with_utility=with_utility)
        row = {"seed": seed}
        for name in pipelines:
            pipe = get_pipeline(name)
            leak_pairs = _leakage_pairs(corpus["records"], pipe)
            caught = sum(p[0] for p in leak_pairs); total = sum(p[1] for p in leak_pairs)
            row[f"privacy::{name}"] = round(caught / total, 4) if total else None
            if with_utility:
                util_pairs = _utility_pairs(corpus["records"], pipe)
                if util_pairs:
                    pres = sum(p[0] for p in util_pairs); tot = sum(p[1] for p in util_pairs)
                    row[f"utility::{name}"] = round(pres / tot, 4) if tot else None
        if with_inference:
            inf_pairs = _inference_pairs(corpus["records"], attacker_name)
            c = sum(p[0] for p in inf_pairs)
            row["inference_recovery"] = round(c / len(inf_pairs), 4) if inf_pairs else None
        if with_population:
            pop = gc.generate_population(with_population, seed + 1)
            xt_pairs = _crosstrack_pairs(corpus, pop, attacker_name, k=5) if with_inference else None
            if xt_pairs:
                c = sum(p[0] for p in xt_pairs)
                row["crosstrack_vulnerable"] = round(c / len(xt_pairs), 4) if xt_pairs else None
        per_seed.append(row)

    def summarize(key):
        vals = [r[key] for r in per_seed if r.get(key) is not None]
        if not vals:
            return None
        return {"mean": round(sum(vals) / len(vals), 4), "min": round(min(vals), 4),
                "max": round(max(vals), 4), "n_seeds": len(vals)}

    keys = sorted({k for r in per_seed for k in r if k != "seed"})
    return {"mode": "seed-sweep", "n_records_per_seed": n_records, "seeds": seeds,
            "per_seed": per_seed, "summary": {k: summarize(k) for k in keys}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", help="required for --mode bootstrap (default mode)")
    ap.add_argument("--population", default=None)
    ap.add_argument("--pipelines", nargs="+", default=list(REGISTRY))
    ap.add_argument("--attacker", default="signature-match-v0")
    ap.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    ap.add_argument("--boot-seed", type=int, default=DEFAULT_BOOT_SEED)
    ap.add_argument("--seeds", type=int, default=0,
                    help="if >0, run a seed sweep instead: regenerate the corpus this "
                         "many times (independent seeds) and report cross-seed stability")
    ap.add_argument("--seed-start", type=int, default=20260101)
    ap.add_argument("--n", type=int, default=50, help="records per corpus (seed-sweep mode)")
    ap.add_argument("--population-size", type=int, default=100000, help="seed-sweep mode")
    ap.add_argument("--out", default="stats_report.json")
    args = ap.parse_args()

    if args.seeds > 0:
        seeds = [args.seed_start + i * 100000 for i in range(args.seeds)]
        report = seed_sweep_report(args.n, seeds, args.pipelines, args.attacker,
                                   with_population=args.population_size,
                                   with_inference=True, with_utility=True)
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Seed sweep ({args.seeds} independent corpora, n={args.n} records each)")
        for key, s in report["summary"].items():
            if s:
                print(f"  {key:28s} mean={s['mean']:.3f}  range=[{s['min']:.3f}, {s['max']:.3f}]  "
                      f"(over {s['n_seeds']} seeds)")
        print(f"\nFull report -> {args.out}")
        return

    if not args.corpus:
        raise SystemExit("--corpus is required in bootstrap mode (or pass --seeds N for a sweep)")
    corpus = json.load(open(args.corpus))
    report = bootstrap_report(corpus, args.corpus, args.pipelines, args.attacker, args.population,
                              args.n_boot, args.boot_seed)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Bootstrap CIs  (n={report['sample_size']} records, {report['n_boot']} resamples, "
          f"95% percentile CI)")
    print("  privacy (Safe Harbor coverage):")
    for name, ci in report["privacy"].items():
        print(f"    {name:20s} {ci['point']:.3f}  [{ci['ci_lo']:.3f}, {ci['ci_hi']:.3f}]")
    if report["utility"]:
        print("  utility (clinical preservation):")
        for name, ci in report["utility"].items():
            print(f"    {name:20s} {ci['point']:.3f}  [{ci['ci_lo']:.3f}, {ci['ci_hi']:.3f}]")
    if "privacy_diff" in report:
        d = report["privacy_diff"]
        sig = "SIGNIFICANT" if d["significant_at_0.05"] else "not significant"
        print(f"  privacy diff (A-B): {d['diff']:+.3f}  [{d['ci_lo']:+.3f}, {d['ci_hi']:+.3f}]  "
              f"p={d['p_value']:.4f}  ({sig} at alpha=0.05)")
    if "utility_diff" in report:
        d = report["utility_diff"]
        sig = "SIGNIFICANT" if d["significant_at_0.05"] else "not significant"
        print(f"  utility diff (A-B): {d['diff']:+.3f}  [{d['ci_lo']:+.3f}, {d['ci_hi']:+.3f}]  "
              f"p={d['p_value']:.4f}  ({sig} at alpha=0.05)")
    if "inference_recovery" in report:
        ci = report["inference_recovery"]
        print(f"  Track 3 recovery: {ci['point']:.3f}  [{ci['ci_lo']:.3f}, {ci['ci_hi']:.3f}]")
    if "crosstrack_vulnerable" in report:
        ci = report["crosstrack_vulnerable"]
        print(f"  cross-track vulnerable: {ci['point']:.3f}  [{ci['ci_lo']:.3f}, {ci['ci_hi']:.3f}]")
    print(f"\nFull report -> {args.out}")


if __name__ == "__main__":
    main()
