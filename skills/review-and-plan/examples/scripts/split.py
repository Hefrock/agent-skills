import json
for c in json.load(open("cases.json")):
    for arm,k in (("skill","prompt_skill"),("control","prompt_control")):
        open("prompts/%s_%s.txt" % (arm, c["id"]),"w").write(c[k])
