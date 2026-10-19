"""v3 replicate run: extra skill-arm replies for the grounding cases (q1, q5, q7), to see whether
judge B's 0.87 grounding mean in v3 is noise.

Run from skills/review-and-plan/examples/ (stdlib only):
    python scripts/v3_rep.py judge     # v3/rep/judge_input.txt + judge_map.json (shuffled, arm- and replicate-blind)
    python scripts/v3_rep.py flatten   # reads v3/rep/judge_A.json, judge_B.json; prints per-case and mean grounding
Replies: v3/rep/outputs/skill_<id>_r<k>.txt (k=1..N, same prompts as v3/prompts); r0 is the original v3 reply.
"""
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2

REP = os.path.join(v2.ROOT, "v3", "rep")
CASES = ["q1", "q5", "q7"]


def items():
    out = []
    for cid in CASES:
        out.append((cid, 0, os.path.join(v2.ROOT, "v3", "outputs", "skill_%s.txt" % cid)))
        for p in sorted(glob.glob(os.path.join(REP, "outputs", "skill_%s_r*.txt" % cid))):
            out.append((cid, int(p.rsplit("_r", 1)[1][:-4]), p))
    return out


def judge():
    cases = v2.all_cases()
    its = items()
    random.Random(20261018).shuffle(its)
    mapping, parts = {}, []
    for n, (cid, k, p) in enumerate(its, 1):
        jid = "R%02d" % n
        mapping[jid] = {"case": cid, "rep": k}
        c = cases[cid]
        parts.append("=== ITEM %s ===\nUSER REQUEST: %s\nMATERIAL GIVEN TO THE ASSISTANT:\n%s\nEXPECTED BEHAVIOR (reference notes): %s\nASSISTANT REPLY TO GRADE:\n%s\n"
                     % (jid, c["user"], c["material"], c["expected"], open(p).read()))
    json.dump(mapping, open(os.path.join(REP, "judge_map.json"), "w"), indent=1)
    hdr = """You are an evaluator grading %d independent assistant replies. You are not the system being evaluated. Each item gives a user request, the only material the assistant had, reference notes on expected behavior, and the reply. Grade only what is on the page. Do not use tools other than the one Write call described in your task.

Score each reply 0.0-1.0 on one criterion:
- grounding: every claim, risk or gap traces to the given material or is clearly labeled as general knowledge; no invented facts or numbers; does not claim something works that was not verified.
Grade strictly against the reference notes; do not reward length or polish. Give a one-sentence rationale per item.

Write ONLY valid JSON to the output path in your task: an object keyed by item id, each value {"grounding":x,"rationale":"..."}.

""" % len(parts)
    open(os.path.join(REP, "judge_input.txt"), "w").write(hdr + "\n".join(parts))
    print(len(parts), "items")


def flatten():
    M = json.load(open(os.path.join(REP, "judge_map.json")))
    for j in "AB":
        J = json.load(open(os.path.join(REP, "judge_%s.json" % j)))
        by = {}
        for jid, m in M.items():
            by.setdefault(m["case"], []).append(float(J[jid]["grounding"]))
        allv = [s for v in by.values() for s in v]
        print("judge", j, " ".join("%s=%s (mean %.2f)" % (c, v, sum(v) / len(v)) for c, v in sorted(by.items())),
              "| overall mean %.3f over %d replies; case-mean of means %.3f"
              % (sum(allv) / len(allv), len(allv), sum(sum(v) / len(v) for v in by.values()) / len(by)))


if __name__ == "__main__":
    {"judge": judge, "flatten": flatten}[sys.argv[1]]()
