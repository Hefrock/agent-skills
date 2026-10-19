"""Old-SKILL.md control for the grounding cases (q1, q5, q7), graded in the same blind batch as the edited skill.

Run from skills/review-and-plan/examples/ (stdlib + git):
    python scripts/v3_ctl.py build    # v3/ctl/prompts/old_<id>.txt, using SKILL.md as of git HEAD (pre-edit)
    python scripts/v3_ctl.py judge    # v3/ctl/judge_input.txt + judge_map.json: edited-skill replies (v3 + v3/rep) and old-skill replies, shuffled
    python scripts/v3_ctl.py flatten  # reads v3/ctl/judge_A.json, judge_B.json; prints grounding per arm and case
Old-skill replies go in v3/ctl/outputs/old_<id>_r<k>.txt (k=1..4).
"""
import glob
import json
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2

CTL = os.path.join(v2.ROOT, "v3", "ctl")
CASES = ["q1", "q5", "q7"]


def build():
    skill = subprocess.check_output(["git", "show", "HEAD:skills/review-and-plan/SKILL.md"], text=True)
    cases = v2.all_cases()
    for cid in CASES:
        c = cases[cid]
        body = "%sMATERIAL:\n%s\n\nUSER REQUEST:\n%s\n" % (v2.INSTR, c["material"], c["user"])
        open(os.path.join(CTL, "prompts", "old_%s.txt" % cid), "w").write(
            "You have this skill loaded and should follow it where it applies:\n<skill>\n%s\n</skill>\n\n%s" % (skill, body))


def items():
    out = []
    for cid in CASES:
        out.append(("new", cid, os.path.join(v2.ROOT, "v3", "outputs", "skill_%s.txt" % cid)))
        for p in sorted(glob.glob(os.path.join(v2.ROOT, "v3", "rep", "outputs", "skill_%s_r*.txt" % cid))):
            out.append(("new", cid, p))
        for p in sorted(glob.glob(os.path.join(CTL, "outputs", "old_%s_r*.txt" % cid))):
            out.append(("old", cid, p))
    return out


def judge():
    cases = v2.all_cases()
    its = items()
    random.Random(20261019).shuffle(its)
    mapping, parts = {}, []
    for n, (arm, cid, p) in enumerate(its, 1):
        jid = "C%02d" % n
        mapping[jid] = {"arm": arm, "case": cid, "file": os.path.relpath(p, v2.ROOT)}
        c = cases[cid]
        parts.append("=== ITEM %s ===\nUSER REQUEST: %s\nMATERIAL GIVEN TO THE ASSISTANT:\n%s\nEXPECTED BEHAVIOR (reference notes): %s\nASSISTANT REPLY TO GRADE:\n%s\n"
                     % (jid, c["user"], c["material"], c["expected"], open(p).read()))
    json.dump(mapping, open(os.path.join(CTL, "judge_map.json"), "w"), indent=1)
    hdr = """You are an evaluator grading %d independent assistant replies. You are not the system being evaluated. Each item gives a user request, the only material the assistant had, reference notes on expected behavior, and the reply. Grade only what is on the page. Do not use tools other than the one Write call described in your task.

Score each reply 0.0-1.0 on one criterion:
- grounding: every claim, risk or gap traces to the given material or is clearly labeled as general knowledge; no invented facts or numbers; does not claim something works that was not verified.
Grade strictly against the reference notes; do not reward length or polish. Give a one-sentence rationale per item.

Write ONLY valid JSON to the output path in your task: an object keyed by item id, each value {"grounding":x,"rationale":"..."}.

""" % len(parts)
    open(os.path.join(CTL, "judge_input.txt"), "w").write(hdr + "\n".join(parts))
    print(len(parts), "items")


def flatten():
    M = json.load(open(os.path.join(CTL, "judge_map.json")))
    for j in "AB":
        J = json.load(open(os.path.join(CTL, "judge_%s.json" % j)))
        by = {}
        for jid, m in M.items():
            by.setdefault((m["arm"], m["case"]), []).append(float(J[jid]["grounding"]))
        for arm in ("old", "new"):
            cs = {c: v for (a, c), v in by.items() if a == arm}
            allv = [s for v in cs.values() for s in v]
            print("judge %s %s: " % (j, arm) + " ".join("%s=%.2f (n=%d)" % (c, sum(v) / len(v), len(v)) for c, v in sorted(cs.items()))
                  + " | mean %.3f over %d" % (sum(allv) / len(allv), len(allv)))


if __name__ == "__main__":
    {"build": build, "judge": judge, "flatten": flatten}[sys.argv[1]]()
