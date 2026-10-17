"""A1 (v2): extra Quick-mode cases, with length scored as its own criterion.

Run from skills/review-and-plan/examples/:
    python scripts/v2.py build      # v2/cases.json + v2/prompts/*.txt (new cases, both arms)
    python scripts/v2.py judge      # v2/judge_input.txt + v2/judge_map.json (arm-blind, shuffled)
    python scripts/v2.py flatten    # v2/results_<arm>_<judge>.jsonl, reads v2/judge_A.json, v2/judge_B.json

The v2 set = 6 new cases (q3-q8) + the 3 v1 Quick-relevant cases (r3, q1, q2), re-judged under the
new rubric. v1 outputs are reused unchanged from outputs/.
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # examples/
V2 = os.path.join(ROOT, "v2")
SKILL = os.path.join(os.path.dirname(ROOT), "SKILL.md")
REUSED = ["r3", "q1", "q2"]
KEY = {"length-fit": "length_fit", "grounding": "grounding", "mode-selection": "mode_selection"}

NEW = [
    dict(id="q3", category="length-fit", user="Is it worth writing a script to rename my screenshots?",
         material="Your usage log has one relevant entry: 2026-10-14, 'renamed 12 screenshots by hand'. Nothing else.",
         expected="SHORT (2-4 sentences). Not yet: the need has shown up once; log it and revisit. No visible 7-step walk-through, no options table."),
    dict(id="q4", category="length-fit", user="Is it worth writing a script to rename my screenshots? Same question as last month.",
         material="Your usage log has two dated entries: 2026-09-12 'renamed 9 screenshots by hand' and 2026-10-14 'renamed 12 screenshots by hand'. Renaming is a one-off shell loop away; nothing outward-facing is involved.",
         expected="SHORT (2-4 sentences). Needed-twice is now met, so building is justified: say yes, keep it small. Must NOT over-refuse. No heavy checklist."),
    dict(id="q5", category="grounding", user="Premortem this migration plan before I run it.",
         material="Plan (full text): 'Friday 6pm: run migrate.sql against the production orders table (4M rows) to add a NOT NULL column `region` with default 'unknown', then backfill from the customers table in one UPDATE. No staging run is planned. Rollback: restore last night's backup.' No tools are available and no other reviewer exists.",
         expected="LONGER answer is right here. Each risk must trace to a sentence of the plan (no staging run, one UPDATE on 4M rows, Friday 6pm, rollback = last night's backup loses the day's orders). General patterns must be labeled as such. States the review was solo."),
    dict(id="q6", category="mode-selection", user="Which caching approach should I use for the product-catalog endpoint: in-process LRU, Redis, or HTTP cache headers behind the CDN?",
         material="Facts you have: 3 app instances behind a load balancer; catalog changes a few times a day via an admin tool; reads are ~2k/min; there is a CDN already in front; no Redis is deployed today. Nothing else is known.",
         expected="Quick mode with MECE options (the three are distinct and cover the space, or says what is missing), a pick with reasoning from the facts given, and what it did not know. Moderate length; options may be a short list/table. Not a 7-step lecture."),
    dict(id="q7", category="grounding", user="Premortem my fix: I added a 2-second sleep before the assertion in test_sync so it stops flaking. It passed locally 5 times, so I'm going to merge.",
         material="Material: test_sync starts a background thread and asserts on shared state; the failure is intermittent in CI only; the CI runner is slower than your laptop. Your local checkout runs the code in `src/`, but CI installs the package from a built wheel. You have not been given the wheel contents or CI logs.",
         expected="Grounded risks: sleep masks a race rather than fixing it; slower CI may still exceed 2s; local passes (5 runs) do not verify the CI path because CI runs the installed wheel, not src/ - verify the mechanism before merging. Does not claim the fix works. Moderate length."),
    dict(id="q8", category="length-fit", user="Should I turn my weekly 'check the broken links' routine into a CI step?",
         material="Your log has one entry: 2026-10-09 'ran link checker by hand, found 2 broken links'. No other mentions.",
         expected="SHORT (2-4 sentences). One occurrence so far: log it, do not formalize yet; maybe say what would trigger it. No visible checklist walk-through."),
]

INSTR = ("You have no tools. Do not read files or run commands. The only facts available are in MATERIAL below. "
         "Reply to the user exactly as you would in a real session.\n\n")


def build():
    skill = open(SKILL).read()
    os.makedirs(os.path.join(V2, "prompts"), exist_ok=True)
    os.makedirs(os.path.join(V2, "outputs"), exist_ok=True)
    json.dump(NEW, open(os.path.join(V2, "cases.json"), "w"), indent=1)
    for c in NEW:
        body = "%sMATERIAL:\n%s\n\nUSER REQUEST:\n%s\n" % (INSTR, c["material"], c["user"])
        open(os.path.join(V2, "prompts", "control_%s.txt" % c["id"]), "w").write(body)
        open(os.path.join(V2, "prompts", "skill_%s.txt" % c["id"]), "w").write(
            "You have this skill loaded and should follow it where it applies:\n<skill>\n%s\n</skill>\n\n%s" % (skill, body))
    print(len(NEW), "new cases;", 2 * len(NEW), "prompts")


def all_cases():
    v1 = {c["id"]: c for c in json.load(open(os.path.join(ROOT, "cases.json")))}
    cases = {c["id"]: c for c in NEW}
    # re-judge the v1 Quick-relevant cases under the new rubric: r3 is a length question, q1/q2 keep their category
    for cid in REUSED:
        c = dict(v1[cid])
        if cid == "r3":
            c["category"] = "length-fit"
            c["expected"] = ("SHORT (2-4 sentences). Not yet: the need has shown up once; log it. "
                             "No visible 7-step walk-through. (Same case as v1 r3, now scored on length-fit.)")
        cases[cid] = c
    return cases


def out_path(arm, cid):
    base = ROOT if cid in REUSED else V2
    return os.path.join(base, "outputs", "%s_%s.txt" % (arm, cid))


def judge():
    cases = all_cases()
    keys = [(a, i) for a in ("skill", "control") for i in cases]
    random.Random(20261017).shuffle(keys)
    mapping, parts = {}, []
    for n, (arm, cid) in enumerate(keys, 1):
        jid = "K%02d" % n
        mapping[jid] = {"arm": arm, "case": cid}
        c = cases[cid]
        parts.append("=== ITEM %s ===\nUSER REQUEST: %s\nMATERIAL GIVEN TO THE ASSISTANT:\n%s\nEXPECTED BEHAVIOR (reference notes): %s\nASSISTANT REPLY TO GRADE:\n%s\n"
                     % (jid, c["user"], c["material"], c["expected"], open(out_path(arm, cid)).read()))
    json.dump(mapping, open(os.path.join(V2, "judge_map.json"), "w"), indent=1)
    hdr = """You are an evaluator grading %d independent assistant replies. You are not the system being evaluated. Each item gives a user request, the only material the assistant had, reference notes on expected behavior, and the reply. Grade only what is on the page. Do not use tools other than the one Write call described in your task.

Score each reply 0.0-1.0 on three criteria:
- mode_selection: picked the right depth and structure for the request (skipped heavy process for trivial or reversible asks; used options/checklist structure where a real decision or review calls for it; applied the needed-twice rule correctly in BOTH directions - do not reward refusing to build when the need has shown up twice). Do NOT lower this score just because the reply is long; length is scored separately.
- grounding: every claim, risk or gap traces to the given material or is clearly labeled as general knowledge; no invented facts or numbers; does not claim something works that was not verified.
- length_fit: the length and visible structure are proportionate to the reference notes. A reply that is correct but walks through a visible multi-step checklist when a short answer was expected scores low; a reply that is too thin where the reference expects depth also scores low. Substance is not penalized here, only proportion.
Score each criterion independently. Grade strictly against the reference notes; do not reward length or polish. Give a one-sentence rationale per item (for the criterion most relevant to the reference notes).

Write ONLY valid JSON to the output path in your task: an object keyed by item id, each value {"mode_selection":x,"grounding":x,"length_fit":x,"rationale":"..."}.

""" % len(parts)
    open(os.path.join(V2, "judge_input.txt"), "w").write(hdr + "\n".join(parts))
    print(len(parts), "items")


def flatten():
    cases = all_cases()
    M = json.load(open(os.path.join(V2, "judge_map.json")))
    by = {}
    for j in "AB":
        J = json.load(open(os.path.join(V2, "judge_%s.json" % j)))
        for jid, m in M.items():
            c = cases[m["case"]]
            by.setdefault((j, m["arm"]), []).append(
                {"id": m["case"], "score": float(J[jid][KEY[c["category"]]]), "category": c["category"], "rationale": J[jid]["rationale"]})
    for (j, arm), rows in by.items():
        rows.sort(key=lambda r: r["id"])
        open(os.path.join(V2, "results_%s_%s.jsonl" % (arm, j)), "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
    print("wrote", len(by), "results files")


if __name__ == "__main__":
    {"build": build, "judge": judge, "flatten": flatten}[sys.argv[1]]()
