#!/usr/bin/env python3
"""
Track 1 orchestrator: run a de-id DEFENDER over a corpus, emit run rows for the scorer.

This is the closed loop for the deterministically-scorable track:
    generate_corpus.py  ->  run_track1.py  ->  score_leakage.py

Each run row carries the ground-truth identifiers alongside the defender's output, so
the scorer needs nothing but this file.

Usage:
    python run_track1.py --corpus corpus.json --pipeline regex-baseline-v0 --out runs.jsonl
"""
from __future__ import annotations
import argparse, json
from deid_pipelines import get_pipeline

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--pipeline", default="regex-baseline-v0")
    ap.add_argument("--out", default="runs.jsonl")
    args = ap.parse_args()

    corpus = json.load(open(args.corpus))
    pipe = get_pipeline(args.pipeline)

    with open(args.out, "w") as f:
        for rec in corpus["records"]:
            scrubbed, redacted = pipe.scrub(rec["note_text"])
            f.write(json.dumps({
                "record_id": rec["record_id"],
                "pipeline": pipe.name,
                "identifiers": rec["identifiers"],
                "scrubbed_text": scrubbed,
                "redacted_spans": redacted,
            }) + "\n")
    print(f"Ran '{pipe.name}' over {len(corpus['records'])} records -> {args.out}")

if __name__ == "__main__":
    main()
