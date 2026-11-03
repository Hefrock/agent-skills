"""v4: decision-protocol edit (read current state, null/by-hand/existing options, reversal condition, declared skips).

Three arms in ONE blind batch, so batch-to-batch judge drift cannot masquerade as a skill effect
(v3 found identical control replies moving 0.59 -> 0.36 across batches):
    old      = SKILL.md at OLD_COMMIT (the skill before this edit)
    new      = SKILL.md at NEW_COMMIT (the edit; reverted afterwards because rule 1 fired, see v4/RESULT.md)
    control  = no skill
Cases: the 9 v2 cases (q1-q8, r3), the 4 frozen fresh cases (f1-f4, read only), and 3 new
"already exists" cases (a1-a3, frozen in v4/A_FREEZE.txt before any reply existed).

Run from skills/review-and-plan/examples/ (stdlib + git):
    python scripts/v4.py build [PROMPT_DIR]  # v4/a_cases.json + <arm>_<id>.txt prompts (default v4/prompts, gitignored)
    python scripts/v4.py judge               # v4/judge/{main,a}_input.txt + judge_map.json (arm-blind, shuffled)
    python scripts/v4.py flatten             # reads v4/judge/{main,a}_{A,B}.json; writes v4/results_<arm>_<judge>.jsonl + summary
Replies go in v4/outputs/<arm>_<id>.txt. Nothing under v2/ or v3/ is read for writing.
"""
import json
import os
import random
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v2

ROOT = v2.ROOT
V4 = os.path.join(ROOT, "v4")
JUDGE = os.path.join(V4, "judge")
FRESH = os.path.join(ROOT, "v3", "human", "fresh", "fresh_cases.json")
OLD_COMMIT = "5e222c6"
NEW_COMMIT = "6613327"  # the pre-registration commit, which carried the edited SKILL.md
ARMS = ("old", "new", "control")
SEED = 20261103
SHORT = ["q3", "q4", "q8", "r3", "f1", "f2", "f3"]  # length-fit cases where a short answer is right
GROUNDING = ["q1", "q5", "q7"]

A_NOTE = ("(Excerpts were embedded by the eval author from the real repository at the commit named; "
          "you cannot open other files.)")

A_CASES = [
    dict(id="a1", category="already-exists",
         user="Should agent-skills add a retrieval eval for vault search, inspired by DeepTutor's `kb eval`?",
         material=A_NOTE + """
Repo: Hefrock/agent-skills at commit 5e222c6. The user describes DeepTutor's `kb eval` as "a command that runs a set of questions against a knowledge base and reports retrieval quality".

`git ls-files mcp/obsidian-vault`:
.gitignore, README.md, package-lock.json, package.json, src/bm25.ts, src/chunker.ts, src/index.ts, src/paths.ts, src/vault-index.ts, src/vault-io.ts, src/warehouse.ts, test/retrieval-eval.mjs, test/server.test.mjs, test/warehouse.test.mjs, tsconfig.json

mcp/obsidian-vault/README.md headings: "# obsidian-vault MCP", "## Tools", "## Warehouse retrieval (optional)", "## Install", "## Configure (Claude Code)", "## Security", "## Vault structure expected by wiki-operator". First line: "MCP server for the wiki-operator skill: 10 tools to read, write, search, and maintain a local Obsidian vault, plus 2 read-only warehouse-retrieval tools when `WAREHOUSE_PATH` is set (12 total)."

mcp/obsidian-vault/package.json "scripts":
  "build": "tsc", "dev": "tsc --watch", "start": "node dist/index.js",
  "test": "npm run build && node test/server.test.mjs && node test/warehouse.test.mjs"

mcp/obsidian-vault/test/server.test.mjs, lines 1-5:
// End-to-end tests for the obsidian-vault MCP server.
// Drives the compiled server over its real STDIO JSON-RPC transport — no mocks —
// and asserts on write-mode semantics (create/update/upsert), pre-write backups,
// and the ReDoS fix in search. Run with `npm test` (builds first).

mcp/obsidian-vault/test/retrieval-eval.mjs, lines 1-12:
// Retrieval eval: recall@5 and MRR for search_notes against a REAL vault,
// using a hand-written query set with known-correct note paths. This is
// intentionally NOT run in CI and has no fixtures of its own — it exists to
// be run locally, by a human who can judge whether a result is actually
// right, against a real vault whose content nobody else has.
//
// Usage:
//   OBSIDIAN_VAULT_PATH=/path/to/real/vault node test/retrieval-eval.mjs /path/to/queries.json
//
// Run it once on a build from before a search_notes change and once after,
// and compare the two reports -- that comparison is the actual acceptance
// check (see the PR description this ships with), not anything this script

knowledge-os/sitrep.md, one table row (abridged):
| `obsidian-vault` MCP server | Shipped | 10 vault tools (real BM25F search) + 2 read-only warehouse passage-retrieval tools ... 74-test suite across two files. Retrieval eval run for the first time against a real vault (20 hand-written queries): recall@5 0.950 (19/20), MRR 0.932. This is a first baseline, not a before/after comparison |""",
         expected=("Says a retrieval eval ALREADY EXISTS (mcp/obsidian-vault/test/retrieval-eval.mjs: recall@5 and MRR over a "
                   "hand-written query set) with a recorded baseline in knowledge-os/sitrep.md (recall@5 0.950, MRR 0.932), and "
                   "recommends using or extending it (for example, the before/after run it was designed for, more queries, or a "
                   "specific DeepTutor feature it lacks) rather than adding a new one. Does not recommend building a new eval from "
                   "scratch. Short to moderate length; claims trace to the excerpts."),
         flag_terms=[r"retrieval-eval"]),
    dict(id="a2", category="already-exists",
         user="Should ste-prep add an attempt log and mastery path like DeepTutor's?",
         material=A_NOTE + """
Repo: Hefrock/STE-prep at commit 8bdfd46. The user describes DeepTutor's feature as "it logs every attempt a learner makes and moves them along a mastery path".

README.md layout line: "web/                      static item player, figure renderers, mastery rules, tests"

`git ls-files web`: web/index.html, web/js/app.js, web/js/figures.js, web/js/mastery.js, web/js/review.js, web/review.html, web/style.css, web/test/content-grading.test.mjs, web/test/e2e.mjs, web/test/figures.test.mjs, web/test/mastery.test.mjs, web/test/relative-paths.test.mjs, web/test/render-all.mjs

docs/TODO.md, line 77: "- Difficulty definitions (1-3) and mastery thresholds; tune after a pilot with a real student."

web/js/mastery.js, lines 1-8, 20-37 and 139-150:
// Mastery, level and item-selection rules. Pure functions over the LearnerState object
// (schema $defs/LearnerState, stored under STORAGE_KEY). No DOM access; unit-tested in web/test.
export const STORAGE_KEY = "ste.v1.state";
export const WINDOW = 5, MASTER_CORRECT = 4, MASTER_HARD = 2, STEP_DOWN_MISSES = 2, STEP_UP_HITS = 2;
export const DAY = 24 * 3600 * 1000;
export const REVIEW_DAYS = [7, 21];
...
export const emptyState = () => ({ schema: 1, standards: {}, queue: [], settings: {} });
const entry = (state, std) => (state.standards[std] ??= { recent: [], level: 1, mastered: false });
export function isMastered(recent) {
  if (recent.length < WINDOW) return false;
  const hits = recent.filter((a) => a.correct);
  return hits.length >= MASTER_CORRECT && hits.filter((a) => a.level >= 2).length >= MASTER_HARD;
}
// attempt: {item, correct, level, misconception?, itemMisconceptions?}. Mutates and returns state.
export function recordAttempt(state, std, attempt, now = Date.now()) {
  const e = entry(state, std);
  const { item, correct, level } = attempt;
  const rec = { item, correct, level, t: now };
  ...
  e.recent.push(rec);
  if (e.recent.length > WINDOW) e.recent.splice(0, e.recent.length - WINDOW);
...
export function loadState(storage) {
  try {
    const s = JSON.parse(storage.getItem(STORAGE_KEY));
    if (s && s.schema === 1 && s.standards && Array.isArray(s.queue)) return { settings: {}, ...s };
  } catch { /* unavailable or corrupt */ }
  return emptyState();
}
export function saveState(storage, state) {
  try { storage.setItem(STORAGE_KEY, JSON.stringify(state)); return true; } catch { return false; }
}""",
         expected=("Says mastery rules and an attempt record ALREADY EXIST (web/js/mastery.js: recordAttempt, isMastered, "
                   "levels, spaced reviews) and names the real gap rather than proposing a new log: history is trimmed to the "
                   "last 5 attempts per standard (WINDOW = 5, splice), and state lives only in browser storage with no export "
                   "seen in the excerpts (stated as not verified beyond them). Recommends extending what exists, not adding a "
                   "parallel system. Short to moderate length."),
         flag_terms=[r"mastery\.js", r"recordAttempt", r"WINDOW"]),
    dict(id="a3", category="already-exists",
         user="Should I install claude-mem?",
         material=A_NOTE + """
Repo: thedotmack/claude-mem at commit 02584c4 (2026-11-02). You work in Claude Code on several private repos.

README.md, line 126: "Claude-Mem seamlessly preserves context across sessions by automatically capturing tool usage observations, generating semantic summaries, and making them available to future sessions."
README.md Quick Start (abridged): "Install with a single command: npx claude-mem install ..."

Top-level listing: LICENSE, NOTICE, README.md, bunfig.toml, claude-mem-cursor, claude-mem-grok-bot, cowork, docker, docker-compose.yml, docs, dsh, hermes, install, ods, omp, openclaw, package.json, pi, plugin, scripts, services, src, tests, tsconfig.json

src/shared/SettingsDefaultsManager.ts, default values (lines 505-517 and 601-611, abridged):
    CLAUDE_MEM_IDLE_EXIT_SEC: '0',
    CLAUDE_MEM_SKIP_TOOLS: 'ListMcpResourcesTool,SlashCommand,Skill,TodoWrite,AskUserQuestion',
    CLAUDE_MEM_SKIP_BASH_PATTERNS: '',  // Regex matched against a shell command (Bash; PowerShell; Codex exec_command); when it matches, the observation is skipped. Empty = capture every command.
    CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS: 'false',
    CLAUDE_MEM_CAPTURE_ADVISOR_CALLS: 'false',
    CLAUDE_MEM_DELIVERABLES_ENABLED: 'true',
    CLAUDE_MEM_PROVIDER: 'claude',
    CLAUDE_MEM_MEDIA_CAPTURE_ENABLED: 'false',
    ...
    CLAUDE_MEM_OBSERVE_BARE_PROMPTS: 'false',
    CLAUDE_MEM_HOOK_FAIL_LOUD_THRESHOLD: '3',
    CLAUDE_MEM_REDACT_ENABLED: 'false',          // Opt-in auto-redaction of common secret patterns
    CLAUDE_MEM_REDACT_GENERIC_ENABLED: 'false',  // opt-in: also redact un-prefixed credentials (password=, *_TOKEN=, *_SECRET=, *_API_KEY=, Authorization: Bearer)
    CLAUDE_MEM_EXCLUDED_PROJECTS: '',  // Comma-separated glob patterns for excluded project paths
    CLAUDE_MEM_SEMANTIC_INJECT: 'false',""",
         expected=("Does NOT simply recommend installing. Names the defaults that matter, from the settings file: every shell "
                   "command is captured by default (CLAUDE_MEM_SKIP_BASH_PATTERNS empty) and secret redaction is off by default "
                   "(CLAUDE_MEM_REDACT_ENABLED 'false'), so secrets and private-repo content can land in memory. Offers a manual "
                   "or lower-risk alternative (hand-kept notes / CLAUDE.md first, or install only with redaction on, skip "
                   "patterns and excluded projects set). Short to moderate length; claims trace to the excerpts."),
         flag_terms=[r"SKIP_BASH|every (shell )?command|all (shell )?commands", r"REDACT|redact"]),
]
FLAG_ALL = {"a3"}  # a3 must name both risks; a1/a2 need any one term


def a_cases():
    return {c["id"]: {k: v for k, v in c.items() if k != "flag_terms"} for c in A_CASES}


def all_cases():
    cases = dict(v2.all_cases())
    cases.update({c["id"]: c for c in json.load(open(FRESH))})
    cases.update(a_cases())
    return cases


def skill_text(arm):
    commit = OLD_COMMIT if arm == "old" else NEW_COMMIT
    return subprocess.check_output(["git", "show", "%s:skills/review-and-plan/SKILL.md" % commit], text=True)


def prompt(arm, c):
    body = "%sMATERIAL:\n%s\n\nUSER REQUEST:\n%s\n" % (v2.INSTR, c["material"], c["user"])
    if arm == "control":
        return body
    return "You have this skill loaded and should follow it where it applies:\n<skill>\n%s\n</skill>\n\n%s" % (skill_text(arm), body)


def build(pdir=None):
    pdir = pdir or os.path.join(V4, "prompts")
    os.makedirs(pdir, exist_ok=True)
    os.makedirs(os.path.join(V4, "outputs"), exist_ok=True)
    json.dump(list(a_cases().values()), open(os.path.join(V4, "a_cases.json"), "w"), indent=1)
    cases = all_cases()
    for arm in ARMS:
        for cid, c in cases.items():
            open(os.path.join(pdir, "%s_%s.txt" % (arm, cid)), "w").write(prompt(arm, c))
    print(len(cases), "cases x", len(ARMS), "arms =", len(cases) * len(ARMS), "prompts in", pdir)


def v2_header():
    # The v2 rubric, verbatim, so the main batch is graded exactly as v2/v3 were.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "v2.py")).read()
    return src.split('hdr = """', 1)[1].split('""" % len(parts)', 1)[0]


A_EXTRA = """Additionally, for every item score:
- flags_existing: 1 if the reply identifies the existing tool, feature or default-setting risk that the reference notes say a good answer names (citing it from the material), else 0. Naming it only in passing while still recommending a parallel build or a plain install scores 0.
Add "flags_existing":x to each item's JSON object.

"""


def batches():
    cases = all_cases()
    main = [cid for cid in cases if not cid.startswith("a")]
    return {"main": main, "a": sorted(a_cases())}


def judge():
    os.makedirs(JUDGE, exist_ok=True)
    cases = all_cases()
    mapping = {}
    for name, ids in batches().items():
        keys = [(a, i) for a in ARMS for i in ids]
        random.Random(SEED).shuffle(keys)
        parts = []
        for n, (arm, cid) in enumerate(keys, 1):
            jid = "%s%02d" % ("M" if name == "main" else "A", n)
            mapping[jid] = {"arm": arm, "case": cid, "batch": name}
            c = cases[cid]
            parts.append("=== ITEM %s ===\nUSER REQUEST: %s\nMATERIAL GIVEN TO THE ASSISTANT:\n%s\nEXPECTED BEHAVIOR (reference notes): %s\nASSISTANT REPLY TO GRADE:\n%s\n"
                         % (jid, c["user"], c["material"], c["expected"], open(os.path.join(V4, "outputs", "%s_%s.txt" % (arm, cid))).read()))
        hdr = v2_header() % len(parts)
        if name == "a":
            hdr = hdr.replace("\nWrite ONLY valid JSON", "\n" + A_EXTRA + "Write ONLY valid JSON", 1)
            hdr = hdr.replace('"length_fit":x,"rationale"', '"length_fit":x,"flags_existing":x,"rationale"')
        open(os.path.join(JUDGE, "%s_input.txt" % name), "w").write(hdr + "\n".join(parts))
        print(name, len(parts), "items")
    json.dump(mapping, open(os.path.join(JUDGE, "judge_map.json"), "w"), indent=1)


def mech_flag(cid, text):
    terms = next(c["flag_terms"] for c in A_CASES if c["id"] == cid)
    hits = [bool(re.search(t, text, re.I)) for t in terms]
    return all(hits) if cid in FLAG_ALL else any(hits)


def flatten():
    cases = all_cases()
    M = json.load(open(os.path.join(JUDGE, "judge_map.json")))
    by, flags = {}, {}
    for j in "AB":
        J = {}
        for name in ("main", "a"):
            J.update(json.load(open(os.path.join(JUDGE, "%s_%s.json" % (name, j)))))
        for jid, m in M.items():
            c = cases[m["case"]]
            crit = "grounding" if c["category"] == "already-exists" else v2.KEY[c["category"]]
            by.setdefault((j, m["arm"]), []).append(
                {"id": m["case"], "score": float(J[jid][crit]), "category": c["category"], "rationale": J[jid]["rationale"]})
            if m["batch"] == "a":
                flags[(j, m["arm"], m["case"])] = int(J[jid]["flags_existing"])
    for (j, arm), rows in by.items():
        rows.sort(key=lambda r: r["id"])
        open(os.path.join(V4, "results_%s_%s.jsonl" % (arm, j)), "w").write("\n".join(json.dumps(r) for r in rows) + "\n")
    summary(by, flags)


def summary(by, flags):
    def mean(j, arm, ids):
        s = {r["id"]: r["score"] for r in by[(j, arm)]}
        return sum(s[i] for i in ids) / len(ids)
    out = ["| category (n) | old A / B | new A / B | control A / B |", "|---|---|---|---|"]
    for label, ids in (("length-fit, short (7)", SHORT), ("length-fit, depth f4 (1)", ["f4"]),
                       ("grounding (3: q1, q5, q7)", GROUNDING), ("mode-selection (2: q2, q6)", ["q2", "q6"]),
                       ("a-cases, grounding (3)", ["a1", "a2", "a3"])):
        out.append("| %s | %s |" % (label, " | ".join("%.2f / %.2f" % (mean("A", a, ids), mean("B", a, ids)) for a in ARMS)))
    out += ["", "| case | flags_existing old A/B | new A/B | control A/B | mechanical (old/new/control) |", "|---|---|---|---|---|"]
    for cid in ("a1", "a2", "a3"):
        mech = "/".join(str(int(mech_flag(cid, open(os.path.join(V4, "outputs", "%s_%s.txt" % (a, cid))).read()))) for a in ARMS)
        out.append("| %s | %s | %s |" % (cid, " | ".join("%d/%d" % (flags[("A", a, cid)], flags[("B", a, cid)]) for a in ARMS), mech))
    drops = []
    for cid in sorted({r["id"] for r in by[("A", "old")]} - {"a1", "a2", "a3"}):
        d = [dict((r["id"], r["score"]) for r in by[(j, "new")])[cid] - dict((r["id"], r["score"]) for r in by[(j, "old")])[cid] for j in "AB"]
        if d[0] < 0 and d[1] < 0:
            drops.append("%s (A %+.2f, B %+.2f)" % (cid, d[0], d[1]))
    out += ["", "Cases where new < old on BOTH judges: " + (", ".join(drops) or "none")]
    words = {a: {cid: len(open(os.path.join(V4, "outputs", "%s_%s.txt" % (a, cid))).read().split()) for cid in all_cases()} for a in ARMS}
    out += ["", "| arm | median words, short cases | words per case |", "|---|---|---|"]
    for a in ARMS:
        w = sorted(words[a][c] for c in SHORT)
        out.append("| %s | %d | %s |" % (a, w[len(w) // 2], ", ".join("%s %d" % (c, words[a][c]) for c in sorted(words[a]))))
    text = "\n".join(out) + "\n"
    open(os.path.join(V4, "SUMMARY.md"), "w").write(text)
    print(text)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "build":
        build(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        {"judge": judge, "flatten": flatten}[cmd]()
