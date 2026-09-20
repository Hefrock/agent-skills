# Agent Skills

[![CI](https://github.com/Hefrock/agent-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/Hefrock/agent-skills/actions/workflows/ci.yml)

A personal collection of [Agent Skills](https://agentskills.io) — self-contained capabilities that any compatible AI agent can load on demand. Skills are built on the open standard Anthropic originally published, now supported by Claude, Codex CLI, Gemini CLI, GitHub Copilot, Cursor, and 25+ other platforms, so nothing here is locked to one tool.

Each skill is just a folder: a `SKILL.md` file with instructions, plus scripts/references/assets where needed. See [CONTRIBUTING.md](./CONTRIBUTING.md) if you want to add one or keep it portable across platforms.

> [!IMPORTANT]
> **Stalled-work tracking.** Tasks blocked on someone else (a signature, a sign-up, a records request) are tracked as GitHub issues and surfaced weekly — see [`docs/stalled-work-tracking.md`](./docs/stalled-work-tracking.md).

**Jump to:** [Skills](#skills) · [Installing](#installing-a-skill) · [Repo structure](#repo-structure) · [Wiki system](#wiki-system)

## Skills

### Agent design — build and evaluate other agents

| Skill | What it does |
|---|---|
| [`agent-eval`](./skills/agent-eval) | Turns "does this agent actually work?" into a repeatable score: rubrics, LLM-as-judge grading, and regression sets that catch when a prompt change made things worse. |
| [`agent-redteam`](./skills/agent-redteam) | Generates adversarial test cases to check that an agent fails safely — refuses, hedges, or degrades gracefully — instead of confidently getting it wrong. Pairs with `agent-eval` for scoring. |
| [`repo-pincer`](./skills/repo-pincer) | Reverse-engineers a codebase: reads what the docs claim, reads what the code actually does, and reports exactly where they disagree. |
| [`issue-reconciler`](./skills/issue-reconciler) | Finds GitHub issues that are actually already resolved but never got closed — a merged PR that said "Closes #N" but didn't take effect, or a skill that quietly got built with nobody linking the PR. Proposes closures with evidence; never closes anything on its own. |

### Privacy — catch leaks before they happen

| Skill | What it does |
|---|---|
| [`privacy-linter`](./skills/privacy-linter) | Scans a git diff before you commit for leaked emails, SSNs, credit cards, API keys, and photo GPS metadata — entirely offline, no model call. Can strip EXIF metadata outright, dig through commit history for old leaks, track your leak rate over time, and block a commit on high-severity findings (the installed pre-commit hook does this by default). |
| [`deid-reid-harness`](./skills/deid-reid-harness) | Stress-tests a clinical de-identification pipeline by trying to re-identify the patients afterward, across three attack types plus a privacy/utility tradeoff score. Runs offline with real statistical confidence intervals. Moved here from Agent design — once real DUA-governed patient data is in scope (see the open safety-hardening issue), this is part of the privacy-sensitive data chain, not just an eval harness that happens to score PHI. ([sample results](./skills/deid-reid-harness/RESULTS.md)) |
| [`privacy-threat-oracle`](./skills/privacy-threat-oracle) | A rule-based second opinion on "is it safe to share this?" — weighs which identity of yours it's coming from, who could actually see it, and what's in it against a threat model of real adversaries, and gives a clear proceed / modify / decline. Plugs directly into `privacy-linter`'s output. |
| [`style-obfuscator`](./skills/style-obfuscator) | Computes a stylometric fingerprint of a draft (function-word frequency, punctuation habits, sentence stats) and scores its similarity against your own known writing — so you know what a matcher would key on before posting something you don't want traced back to you. Flags only; never rewrites. |

### Knowledge management — a personal wiki that maintains itself

| Skill | What it does |
|---|---|
| [`wiki-operator`](./skills/wiki-operator) | Your everyday interface to the vault — `/learn`, `/update`, `/connect`, `/ask`, `/review`, `/quiz`, `/map`, `/source`, `/clean`, `/health`. |
| [`wiki-synthesizer`](./skills/wiki-synthesizer) | Turns raw journal entries and saved sources into proper, linked knowledge pages. Run after a learning session. |
| [`wiki-privacy-audit`](./skills/wiki-privacy-audit) | Runs `privacy-linter` across your entire Obsidian vault instead of one diff, so a stray SSN or API key pasted into a journal entry doesn't sit there forever. Has a wrapper for unattended/scheduled runs. |
| [`wiki-librarian`](./skills/wiki-librarian) | Weekly housekeeping — finds broken links, orphaned notes, duplicates, and contradictions, and proposes fixes for you to confirm. |
| [`wiki-governor`](./skills/wiki-governor) | Runs the librarian, synthesizer, and warehouse on a schedule, then grades the vault's own health against its own rules and tracks a knowledge-gap queue. |
| [`wiki-teacher`](./skills/wiki-teacher) | A weekly `/checkin` that looks across several ongoing projects and surfaces the 1-2 that genuinely need your attention — never a dump of everything at once. |
| [`wiki-warehouse`](./skills/wiki-warehouse) | Archives PDFs, ebooks, and scans in a separate private repo, leaving only a lightweight pointer note in the vault so it stays fast. |
| [`research-ledger`](./skills/research-ledger) | Saves a deep-research run's full receipts — every claim and how it was verified — before that detail gets compressed away during synthesis. |

### Content automation

| Skill | What it does |
|---|---|
| [`broadcast`](./skills/broadcast) | Produces a daily healthcare-AI news audio briefing: pulls from eleven registered sources, dedupes and ranks stories, pins every claim to its source through a real evidence-pinning MCP server, writes a script, synthesizes audio, and publishes a podcast feed + vault note. Requires `GEMINI_API_KEY` and a locally-built evidence-pinning-mcp server. |

Most knowledge-management and privacy-vault skills require the `obsidian-vault` MCP server — see [Wiki system](#wiki-system) below for setup. Full flag-by-flag documentation for any skill lives in its own `SKILL.md`, not here.

## Installing a skill

**Claude Code (plugin marketplace):**
```bash
/plugin marketplace add Hefrock/agent-skills
/plugin install agent-eval@hefrock-agent-skills
```
Install any other skill the same way — swap `agent-eval` for the plugin name from the table above.

**Getting updates after this repo changes:**
```bash
/plugin marketplace update hefrock-agent-skills
```
This pulls the latest manifest, but an already-installed skill only picks up a content change (an edited `SKILL.md`, a bug fix) once `.claude-plugin/marketplace.json`'s shared `metadata.version` has been bumped past what you have. It is not automatic just because the file changed on GitHub — if a skill's behavior doesn't match what its `SKILL.md` currently says, check whether the version was actually bumped for that change before assuming the update didn't take.

A plugin new to the marketplace still needs its own `/plugin install <name>@hefrock-agent-skills` afterward. If it comes back "not found" right after updating, restart the CLI session and retry.

**Claude Code (manual, no plugin system):**
```bash
git pull origin main   # if you already have the repo cloned
cp -r skills/agent-eval ~/.claude/skills/
```

**claude.ai:**
Zip the individual skill folder (e.g. `skills/agent-eval/`) and upload via Settings → Features → Custom Skills (requires a paid plan with code execution enabled).

**Other platforms (Codex, Gemini CLI, Cursor, etc.):**
Copy the skill folder into whatever directory that platform scans for skills — the `SKILL.md` format works unmodified.

## Repo structure

<details>
<summary>Expand directory tree</summary>

```
agent-skills/
├── .github/
│   ├── workflows/ci.yml        # builds + tests the MCP server, runs the Python skill suites
│   └── ISSUE_TEMPLATE/         # blocked-human / dated-followup templates for stalled-work tracking
├── .claude-plugin/
│   └── marketplace.json        # Claude Code-only install metadata — optional, additive
├── docs/
│   └── stalled-work-tracking.md # how the blocked-human / dated-followup convention works
├── skills/                     # flat — one folder per skill, no category nesting
│   ├── broadcast/              # daily healthcare AI audio briefing pipeline — scripts/orchestrate.py, 563-test suite
│   ├── agent-eval/              # rubric-based evals, LLM-as-judge, regression test sets
│   ├── agent-redteam/           # adversarial case generation, pairs with agent-eval
│   ├── deid-reid-harness/       # clinical de-id/re-id eval — scripts, refs, 50-test suite
│   ├── repo-pincer/             # codebase reverse-engineering — claims vs. reality reconciliation, scripts/check_structural_claims.py + track_findings.py, test suite
│   ├── issue-reconciler/        # GitHub issues vs. reality reconciliation — scripts/find_closing_references.py, test suite
│   ├── privacy-linter/          # pre-disclosure PII/secrets/metadata scanner — scripts/scan_diff.py, test suite
│   ├── privacy-threat-oracle/   # rule-based compartment/adversary decision engine — scripts/oracle.py, test suite
│   ├── style-obfuscator/        # stylometric fingerprint + reference-corpus similarity — scripts/fingerprint.py, test suite
│   ├── wiki-operator/           # on-demand vault operations
│   ├── wiki-synthesizer/        # journal preprocessing + concept page compilation
│   ├── wiki-librarian/          # structural health audits — scripts/check_vault.py, 28-test regression suite
│   ├── wiki-governor/           # maintenance loop + compliance + health score — scripts/health_score.py, 18-test regression suite
│   ├── wiki-teacher/            # /checkin (project accountability) — scripts/wiki_teacher.py, 37-test regression suite
│   ├── wiki-warehouse/          # raw-document cold storage (external repo) + vault pointers
│   ├── wiki-privacy-audit/      # vault-wide PII/secret audit — reuses privacy-linter's scanner
│   └── research-ledger/         # deep-research claim ledger, warehoused ahead of synthesis
├── mcp/
│   ├── evidence-pinning/       # MCP server required by broadcast — durable claim/source provenance log
│   └── obsidian-vault/         # MCP server required by wiki-operator
│       ├── src/index.ts        # 10 tools: search, read, write, append, patch, query, links, delete
│       ├── test/               # end-to-end STDIO tests — `npm test`
│       └── README.md           # setup and configuration guide
├── knowledge-os/
│   ├── constitution.md         # 10 laws Claude follows when operating the wiki
│   ├── architecture.md         # component map, data flow, note lifecycle
│   └── sitrep.md               # living status + gap analysis for the wiki system
├── templates/                  # note templates copied into vault by setup-vault.sh
│   ├── concept.md
│   ├── journal.md
│   ├── source.md
│   └── map.md
├── bin/
│   └── setup-vault.sh          # one-command vault bootstrap (creates folders, copies templates + constitution)
├── skill-template/              # starting point for a new skill
└── CONTRIBUTING.md             # how to add a skill, including portability rules
```

</details>

## Wiki system

The wiki skills (`wiki-operator`, `wiki-synthesizer`, `wiki-librarian`, `wiki-governor`, `wiki-teacher`, `wiki-warehouse`, `wiki-privacy-audit`) form a complete personal knowledge system built around an Obsidian vault:

- **`wiki-warehouse`** adds a separate private repo for raw documents, so originals stay out of the vault while still being indexed by a content-hash pointer.
- **`wiki-teacher`** adds project accountability on top of your project portfolio — orthogonal to the rest, which are about the knowledge graph itself.
- **`wiki-privacy-audit`** pairs with `privacy-linter` instead of reinventing detection — it's the one wiki skill whose "audit" is fully mechanical, so it runs the linter's scanner directly against vault notes rather than reasoning over each one.
- **`research-ledger`** slots in just ahead of `wiki-synthesizer` — it warehouses a deep-research run's full claim ledger (including what got rejected) before synthesis only promotes the confirmed subset into `Knowledge/`.

**Every wiki skill requires the `obsidian-vault` MCP server connected — nothing works without it.** To enable it:

1. **Build it once:**
   ```bash
   cd mcp/obsidian-vault && npm install && npm run build
   ```
2. **Point it at your vault.** Three ways, easiest first:
   - **Claude Code CLI (recommended):**
     ```bash
     claude mcp add obsidian-vault \
       -s user \
       -e OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault \
       -- node /absolute/path/to/agent-skills/mcp/obsidian-vault/dist/index.js
     ```
     `-s user` registers it at the user level — available in every project, which is how this server is meant to run — and edits `~/.claude.json` for you, avoiding the occasional weirdness of hand-editing that file while a running Claude Code process has it open.
   - **`setup-vault.sh`** — run `./bin/setup-vault.sh ~/path/to/vault`; it bootstraps the folder structure, copies templates and the constitution into `System/`, and prints a config snippet with your paths filled in, for the manual route below.
   - **Manual** — add this to `~/.claude.json` yourself:
     ```json
     {
       "mcpServers": {
         "obsidian-vault": {
           "command": "node",
           "args": ["/absolute/path/to/agent-skills/mcp/obsidian-vault/dist/index.js"],
           "env": { "OBSIDIAN_VAULT_PATH": "/absolute/path/to/your/vault" }
         }
       }
     }
     ```
3. **Restart Claude Code and verify:** run `/mcp` — expect `obsidian-vault` connected with 10 tools. Full tool reference: [`mcp/obsidian-vault/README.md`](./mcp/obsidian-vault/README.md).

This is a **per-device** setup step — the server is a local process, so a new machine needs its own build and its own `~/.claude.json` entry (with paths for *that* machine), even if it's pointed at the same synced vault.

The operating layer lives in this repo:

| Path | Purpose |
|---|---|
| `knowledge-os/constitution.md` | 10 non-negotiable rules Claude follows when operating the wiki |
| `knowledge-os/architecture.md` | Component map, data flow, and note lifecycle reference |
| `knowledge-os/sitrep.md` | Living status + gap analysis for the wiki system — what's shipped, what's untested, what's next |
| `templates/` | Note templates (concept, journal, source, map) — copied into `System/templates/` in your vault by `setup-vault.sh` |

## License

See [LICENSE](./LICENSE).
