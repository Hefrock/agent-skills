import json
C = {c["id"]: c for c in json.load(open("cases.json"))}
M = json.load(open("judge_map.json"))
KEY = {"coverage-honesty":"coverage_honesty","mode-selection":"mode_selection","grounding":"grounding","outward-action":"outward_action"}
rows_by = {}
for j in "AB":
    J = json.load(open("judge_%s.json" % j))
    for jid, m in M.items():
        c = C[m["case"]]; s = J[jid][KEY[c["category"]]]
        rows_by.setdefault((j, m["arm"]), []).append({"id": m["case"], "score": float(s), "category": c["category"], "rationale": J[jid]["rationale"]})
for (j, arm), rows in rows_by.items():
    rows.sort(key=lambda r: r["id"])
    open("results_%s_%s.jsonl" % (arm, j), "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
# judge agreement
d = []
for jid, m in M.items():
    k = KEY[C[m["case"]]["category"]]
    a, b = json.load(open("judge_A.json"))[jid][k], json.load(open("judge_B.json"))[jid][k]
    d.append((m["arm"], m["case"], a, b))
print("per-item primary scores (arm case A B):")
for r in sorted(d, key=lambda x: (x[1], x[0])): print(" ", *r)
print("mean |A-B| =", sum(abs(r[2]-r[3]) for r in d)/len(d))
