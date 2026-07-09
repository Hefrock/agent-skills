#!/usr/bin/env python3
"""
Track 2 scorer — Expert Determination re-identification risk. STATISTICAL, no judge.

Track 1 asks: "did a direct identifier survive the scrubber?" This scorer asks the
question Safe Harbor structurally cannot: even with every direct identifier PERFECTLY
removed, do the quasi-identifiers that must remain in a clinical note (age, sex, ZIP3,
a rare diagnosis) still single a patient out against a background population?

That divergence — a record can PASS Safe Harbor and FAIL Expert Determination — is the
whole reason the harness exists. This attacker is model-independent: it is the
load-bearing scorer whose result never depends on any LLM's blind spots, satisfying the
"attacker and defender must never share a base model" invariant for free.

Risk model — El Emam's three attackers, over generalized QI equivalence classes
(see qi_model.py for the generalization; see references/expert-determination.md):

  * Prosecutor  — attacker KNOWS the target is in the released sample.
                  risk_i = 1 / f_i, f_i = sample equivalence-class size.
                  k-anonymity is exactly this bound: the sample is k-anonymous iff
                  min_i f_i >= k.
  * Journalist  — attacker does NOT know whether the target is in the sample.
                  risk_i = 1 / F_i, F_i = population equivalence-class size. The
                  released sample is part of the population, so the sample is folded
                  into the population counts and F_i >= 1 always.
  * Marketer    — attacker wants to re-identify as many people as possible; the
                  aggregate expected success rate = mean_i(1 / F_i).

Output is the risk DISTRIBUTION and the k-anonymity verdict — never a single averaged
number, for the same reason Track 1 refuses to average: it hides the singletons.

Usage:
    python score_reid.py --corpus corpus.json --out reid_report.json
    # (population is read from the corpus's population_ref; override with --population)
"""
from __future__ import annotations
import argparse, json, os
from collections import Counter
from qi_model import generalize, QI_FIELDS, K_ANONYMITY_THRESHOLD


def load_population(path: str) -> list:
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def resolve_population_path(cli_path, corpus, corpus_path):
    path = cli_path or corpus.get("population_ref")
    if not path:
        raise SystemExit(
            "no background population: pass --population PATH, or regenerate the "
            "corpus with `--population N` so population_ref is set.")
    # population_ref is stored as a bare filename; resolve it next to the corpus.
    if not os.path.exists(path):
        cand = os.path.join(os.path.dirname(os.path.abspath(corpus_path)), path)
        if os.path.exists(cand):
            path = cand
    if not os.path.exists(path):
        raise SystemExit(f"population file not found: {path}")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--population", default=None,
                    help="population JSONL; defaults to the corpus's population_ref")
    ap.add_argument("--k", type=int, default=K_ANONYMITY_THRESHOLD,
                    help=f"k-anonymity threshold (default {K_ANONYMITY_THRESHOLD})")
    ap.add_argument("--out", default="reid_report.json")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    records = corpus["records"]
    population = load_population(
        resolve_population_path(args.population, corpus, args.corpus))

    # Sample (released data) classes: f_i. Population classes: F_i, with the sample
    # folded in so every released record contributes at least itself (F_i >= 1).
    sample_keys = [generalize(r["quasi_identifiers"]) for r in records]
    f = Counter(sample_keys)
    F = Counter(generalize(p) for p in population)
    F.update(sample_keys)

    per_record = []
    for r, key in zip(records, sample_keys):
        fi, Fi = f[key], F[key]
        per_record.append({
            "record_id": r["record_id"],
            "identity_key": r["identity_key"],
            "qi_key": list(key),
            "k_sample": fi,
            "k_population": Fi,
            "prosecutor_risk": round(1.0 / fi, 4),
            "journalist_risk": round(1.0 / Fi, 4),
        })

    n = len(per_record)
    prosecutor = [p["prosecutor_risk"] for p in per_record]
    journalist = [p["journalist_risk"] for p in per_record]
    below_k = [p for p in per_record if p["k_sample"] < args.k]
    singletons_sample = sum(1 for p in per_record if p["k_sample"] == 1)
    singletons_pop = sum(1 for p in per_record if p["k_population"] == 1)
    k_hist = dict(sorted(Counter(p["k_sample"] for p in per_record).items()))

    report = {
        "track": "2-expert-determination",
        "qi_fields": list(QI_FIELDS),
        "generalization": "age->5yr band (90+ capped); sex, zip3, rare_diagnosis raw",
        "population_size": len(population),
        "sample_size": n,
        "k_threshold": args.k,
        "k_anonymity": {
            "min_k_sample": min(p["k_sample"] for p in per_record),
            "records_below_k": len(below_k),
            "fraction_below_k": round(len(below_k) / n, 3),
            "sample_is_k_anonymous": len(below_k) == 0,
            "k_histogram_sample": {str(k): v for k, v in k_hist.items()},
        },
        "risk": {
            "prosecutor": {"max": max(prosecutor), "mean": round(sum(prosecutor) / n, 4)},
            "journalist": {"max": max(journalist), "mean": round(sum(journalist) / n, 4)},
            "marketer": {"mean_risk": round(sum(journalist) / n, 4)},
        },
        "safe_harbor_vs_expert_determination": {
            "sample_singletons": singletons_sample,
            "population_singletons": singletons_pop,
            "note": ("Even after a PERFECT Safe Harbor scrub removes every direct "
                     "identifier, these records remain unique on quasi-identifiers "
                     "that must stay in the note. Safe Harbor pass, Expert "
                     "Determination fail — the divergence the harness exists to show."),
        },
        "caveats": [
            "The v0 generator draws zip3 uniformly over ~999 values, so the QI space "
            "is far more dispersed than real geography (where a few hundred ZIP3s hold "
            "most of the population and clinical cohorts cluster locally). This inflates "
            "sample uniqueness — take the prosecutor/k-anonymity numbers as an upper "
            "bound. The population-side journalist and marketer risks are the "
            "load-bearing figures; swapping make_person for a Synthea driver gives "
            "realistic equivalence-class sizes.",
            "The released sample is folded into the population counts, so a record's "
            "population class size is at least 1 (it counts itself).",
        ],
        "per_record": per_record,
    }
    with open(args.out, "w") as fout:
        json.dump(report, fout, indent=2)

    ka = report["k_anonymity"]
    rk = report["risk"]
    print(f"Expert Determination — Track 2  (population N={len(population)}, sample n={n})")
    print(f"  QI set: {', '.join(QI_FIELDS)}   generalization: age 5-yr bands, 90+ capped")
    print(f"  k-anonymity (k>={args.k}): "
          f"{'PASS' if ka['sample_is_k_anonymous'] else 'FAIL'}   "
          f"min_k={ka['min_k_sample']}   "
          f"below-k={ka['records_below_k']}/{n} ({ka['fraction_below_k']*100:.0f}%)")
    print(f"  prosecutor risk  max={rk['prosecutor']['max']:.3f}  mean={rk['prosecutor']['mean']:.3f}")
    print(f"  journalist risk  max={rk['journalist']['max']:.3f}  mean={rk['journalist']['mean']:.3f}")
    print(f"  marketer risk    mean={rk['marketer']['mean_risk']:.3f}")
    print(f"  Safe-Harbor-clean but QI-unique: {singletons_sample}/{n} within sample, "
          f"{singletons_pop}/{n} against population")
    print(f"\nFull report -> {args.out}")


if __name__ == "__main__":
    main()
