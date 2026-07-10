#!/usr/bin/env python3
"""
Cross-track synthesis — the harness's punchline in one report.

Each track answers its own question in isolation. This scorer joins them per record to
compute the claim the whole harness exists to demonstrate:

    A note can satisfy HIPAA Safe Harbor — every one of the 18 direct-identifier
    categories removed — and STILL be re-identifiable (Expert Determination) or leak a
    sensitive attribute (inference). The checklist certifies such a record as
    "de-identified." It is not.

So we assume the best case for the defender — a PERFECT Safe Harbor scrub, every direct
identifier gone (we know them all, by construction) — and then ask, of each record:

  * re-identifiable?  — is it a small-equivalence-class record against the background
                        population (journalist-style k_population < K)? Track 2's logic.
  * inferable?        — does the inference attacker still recover the withheld diagnosis
                        from context alone? Track 3's logic.

A record that is Safe-Harbor-clean yet true on EITHER axis is a checklist false-negative:
compliant and still vulnerable. That count is the headline.

This scorer reuses Track 2's linkage and Track 3's attacker directly (no duplicated
logic) and is fully deterministic — no LLM, no judge. Needs a corpus built with BOTH
--population (Track 2 denominator) and --inference (Track 3 vignettes).

Usage:
    python generate_corpus.py --n 50 --seed 20260101 --population 100000 --inference --out corpus.json
    python score_crosstrack.py --corpus corpus.json --out crosstrack_report.json
"""
from __future__ import annotations
import argparse, json
from collections import Counter, defaultdict
from qi_model import generalize, K_ANONYMITY_THRESHOLD
from score_reid import load_population, resolve_population_path
from inference_attackers import get_attacker
from score_inference import normalize


def require_corpus_ready(corpus: dict, population_arg) -> None:
    missing = [x for x, ok in (
        ("--population/population_ref", corpus.get("population_ref") or population_arg),
        ("--inference/inference_case", all(r.get("inference_case") for r in corpus["records"])))
        if not ok]
    if missing:
        raise SystemExit("cross-track needs a corpus built with --population AND --inference; "
                         f"missing: {', '.join(missing)}")


def compute_per_record(corpus: dict, population: list, attacker_name: str = "signature-match-v0",
                       k: int = K_ANONYMITY_THRESHOLD) -> list:
    """The reusable core: per-record {reidentifiable, inferable, ...} verdicts. Both
    score_crosstrack's CLI and score_stats.py's bootstrap CIs call this directly, so a
    bootstrap point estimate is guaranteed identical to the standalone report's numbers —
    the same parity discipline as cross-track's own reuse of Track 2/3 logic."""
    records = corpus["records"]
    # Track 2 linkage: population equivalence classes (journalist k_population)
    sample_keys = [generalize(r["quasi_identifiers"]) for r in records]
    F = Counter(generalize(p) for p in population)
    F.update(sample_keys)
    # Track 3 inference attacker
    attacker = get_attacker(attacker_name)

    per_record = []
    for rec, key in zip(records, sample_keys):
        k_pop = F[key]
        reidentifiable = k_pop < k
        case = rec["inference_case"]
        guess = attacker.infer(case["note"])["guess"]
        inferable = normalize(guess) == normalize(case["target_value"])
        per_record.append({
            "record_id": rec["record_id"],
            "is_rare": case["is_rare"],
            "direct_identifiers": len(rec["identifiers"]),  # what Safe Harbor removes
            "k_population": k_pop,
            "reidentifiable": reidentifiable,     # Track 2 (journalist k < K)
            "inferable": inferable,               # Track 3 (diagnosis recovered)
            "safe_harbor_but_vulnerable": reidentifiable or inferable,
        })
    return per_record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--population", default=None, help="defaults to the corpus's population_ref")
    ap.add_argument("--attacker", default="signature-match-v0")
    ap.add_argument("--k", type=int, default=K_ANONYMITY_THRESHOLD,
                    help=f"re-identifiable if k_population < K (default {K_ANONYMITY_THRESHOLD})")
    ap.add_argument("--out", default="crosstrack_report.json")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    require_corpus_ready(corpus, args.population)
    population = load_population(resolve_population_path(args.population, corpus, args.corpus))
    attacker = get_attacker(args.attacker)
    per_record = compute_per_record(corpus, population, args.attacker, args.k)
    n = len(per_record)

    def frac(pred, pop=per_record):
        c = sum(1 for r in pop if pred(r))
        return {"count": c, "of": len(pop), "fraction": round(c / len(pop), 3) if pop else None}

    rare = [r for r in per_record if r["is_rare"]]
    common = [r for r in per_record if not r["is_rare"]]
    report = {
        "track": "cross-track-synthesis",
        "premise": "every record is assumed perfectly Safe Harbor-scrubbed (all 18 "
                   "direct-identifier categories removed); the counts below are what "
                   "remains vulnerable DESPITE that.",
        "sample_size": n,
        "k_threshold": args.k,
        "attacker": attacker.name,
        "total_direct_identifiers_removed": sum(r["direct_identifiers"] for r in per_record),
        "reidentifiable_despite_safe_harbor": frac(lambda r: r["reidentifiable"]),
        "inferable_despite_safe_harbor": frac(lambda r: r["inferable"]),
        "vulnerable_on_either_axis": frac(lambda r: r["safe_harbor_but_vulnerable"]),
        "vulnerable_on_both_axes": frac(lambda r: r["reidentifiable"] and r["inferable"]),
        "by_class": {
            "rare": frac(lambda r: r["safe_harbor_but_vulnerable"], rare),
            "common": frac(lambda r: r["safe_harbor_but_vulnerable"], common),
        },
        "per_record": per_record,
    }
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    v = report["vulnerable_on_either_axis"]
    print(f"Cross-track synthesis  (n={n}, assuming a PERFECT Safe Harbor scrub)")
    print(f"  {report['total_direct_identifiers_removed']} direct identifiers removed across the corpus — "
          f"yet, of {n} 'de-identified' records:")
    print(f"    re-identifiable (pop k<{args.k}):   {report['reidentifiable_despite_safe_harbor']['count']}/{n}")
    print(f"    diagnosis inferable:              {report['inferable_despite_safe_harbor']['count']}/{n}")
    print(f"    VULNERABLE on either axis:        {v['count']}/{n} ({v['fraction']*100:.0f}%)")
    print(f"    vulnerable on both:               {report['vulnerable_on_both_axes']['count']}/{n}")
    print(f"  by diagnosis class (either axis):   "
          f"rare {report['by_class']['rare']['count']}/{report['by_class']['rare']['of']}, "
          f"common {report['by_class']['common']['count']}/{report['by_class']['common']['of']}")
    print(f"\n  A checklist false-negative is a record certified de-identified that is not.")
    print(f"Full report -> {args.out}")


if __name__ == "__main__":
    main()
