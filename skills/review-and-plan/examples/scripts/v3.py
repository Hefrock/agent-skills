"""v3: rerun the skill arm of the v2 set against the edited Quick section of SKILL.md.

Run from skills/review-and-plan/examples/ (stdlib only; reuses v2.py's cases, rubric and shuffle seed):
    python scripts/v3.py build      # v3/prompts/skill_<id>.txt (current SKILL.md + the v2 control prompt body)
    python scripts/v3.py judge      # v3/judge_input.txt + v3/judge_map.json (arm-blind; control = unchanged v2 outputs)
    python scripts/v3.py flatten    # v3/results_<arm>_<judge>.jsonl, reads v3/judge_A.json, v3/judge_B.json
Skill replies go in v3/outputs/skill_<id>.txt. Baseline (v2/) is never overwritten.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2

V3 = os.path.join(v2.ROOT, "v3")
v2.V2 = V3  # judge()/flatten() write judge_map.json, judge_input.txt, results_* here


def out_path(arm, cid):
    if arm == "skill":
        return os.path.join(V3, "outputs", "skill_%s.txt" % cid)
    return os.path.join(v2.ROOT if cid in v2.REUSED else os.path.join(v2.ROOT, "v2"), "outputs", "control_%s.txt" % cid)


v2.out_path = out_path


def build():
    skill = open(v2.SKILL).read()
    os.makedirs(os.path.join(V3, "prompts"), exist_ok=True)
    os.makedirs(os.path.join(V3, "outputs"), exist_ok=True)
    for cid, c in v2.all_cases().items():
        body = "%sMATERIAL:\n%s\n\nUSER REQUEST:\n%s\n" % (v2.INSTR, c["material"], c["user"])
        open(os.path.join(V3, "prompts", "skill_%s.txt" % cid), "w").write(
            "You have this skill loaded and should follow it where it applies:\n<skill>\n%s\n</skill>\n\n%s" % (skill, body))
    print(len(v2.all_cases()), "prompts")


if __name__ == "__main__":
    {"build": build, "judge": v2.judge, "flatten": v2.flatten}[sys.argv[1]]()
