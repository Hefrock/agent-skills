# Agent Skills

[![CI](https://github.com/Hefrock/agent-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/Hefrock/agent-skills/actions/workflows/ci.yml)

A personal collection of [Agent Skills](https://agentskills.io) — portable, self-contained capabilities that any compatible AI agent can discover and load on demand. Built on the open standard originally published by Anthropic, now adopted across Claude, Codex CLI, Gemini CLI, GitHub Copilot, Cursor, and 25+ other platforms.

Each skill is a folder containing a `SKILL.md` file (instructions + metadata) and, where needed, supporting `scripts/`, `references/`, or `assets/`. Nothing here is Claude-specific unless explicitly noted — see [CONTRIBUTING.md](./CONTRIBUTING.md) for the portability rules this repo follows.

## Skills

| Skill | Category | Description |
|---|---|---|
| [`agent-eval`](./skills/agent-eval) | Agent Design | Designs and runs evaluations for LLM/agent outputs — rubrics, LLM-as-judge scoring, regression test sets, and pass-rate reporting with a runnable scoring script. |
| [`agent-redteam`](./skills/agent-redteam) | Agent Design | Generates adversarial test cases for safe-failure testing — refusals, hedging, graceful degradation. Pairs with agent-eval for scoring. |
| [`deid-reid-harness`](./skills/deid-reid-harness) | Agent Design | Adversarial de-identification ⟷ re-identification eval harness for clinical text — generates synthetic notes with ground-truth PHI spans, runs a de-id pipeline, and scores Safe Harbor leakage, Expert Determination re-id risk, and free-text inference across a privacy-utility frontier. Model-independent, verified offline with bootstrap CIs and significance tests. ([sample results](./skills/deid-reid-harness/RESULTS.md)) |
| [`wiki-operator`](./skills/wiki-operator) | Knowledge Management | On-demand vault operations — `/learn`, `/update`, `/connect`, `/review`, `/quiz`, `/map`, `/source`, `/clean`, `/health`. The primary interface for working with the wiki. Requires Obsidian MCP connected. |
| [`wiki-synthesizer`](./skills/wiki-synthesizer) | Knowledge Management | Batch compilation — automatically preprocesses unstructured journals, promotes ideas into concept pages, compiles `Sources/raw/` into source pages, updates the hot cache. Run after learning sessions. Requires Obsidian MCP connected. |
| [`wiki-librarian`](./skills/wiki-librarian) | Knowledge Management | Structural maintenance — audits broken links, orphans, stale notes, duplicates, and contradictions. Proposes fixes with confirmation. Run weekly. Requires Obsidian MCP connected. |

## Installing a skill

**Claude Code (plugin marketplace):**
```bash
/plugin marketplace add Hefrock/agent-skills
/plugin install agent-eval@hefrock-agent-skills
```

**Claude Code (manual, no plugin system):**
```bash
cp -r skills/agent-eval ~/.claude/skills/
```

**claude.ai:**
Zip the individual skill folder (e.g. `skills/agent-eval/`) and upload via Settings → Features → Custom Skills (requires a paid plan with code execution enabled).

**Other platforms (Codex, Gemini CLI, Cursor, etc.):**
Copy the skill folder into whatever directory that platform scans for skills — the `SKILL.md` format works unmodified.

## Repo structure

```
agent-skills/
├── .github/
│   └── workflows/ci.yml        # builds + tests the MCP server, runs the Python skill suites
├── .claude-plugin/
│   └── marketplace.json        # Claude Code-only install metadata — optional, additive
├── skills/                     # flat — one folder per skill, no category nesting
│   ├── agent-eval/             # rubric-based evals, LLM-as-judge, regression test sets
│   ├── agent-redteam/          # adversarial case generation, pairs with agent-eval
│   ├── deid-reid-harness/      # clinical de-id/re-id eval — scripts, refs, 31-test suite
│   ├── wiki-operator/          # on-demand vault operations
│   ├── wiki-synthesizer/       # journal preprocessing + concept page compilation
│   └── wiki-librarian/         # structural health audits
├── mcp/
│   └── obsidian-vault/         # MCP server required by wiki-operator
│       ├── src/index.ts        # 10 tools: search, read, write, append, patch, query, links, delete
│       ├── test/               # end-to-end STDIO tests — `npm test`
│       └── README.md           # setup and configuration guide
├── knowledge-os/
│   ├── constitution.md         # 10 laws Claude follows when operating the wiki
│   └── architecture.md         # component map, data flow, note lifecycle
├── templates/                  # note templates copied into vault by setup-vault.sh
│   ├── concept.md
│   ├── journal.md
│   ├── source.md
│   └── map.md
├── bin/
│   └── setup-vault.sh          # one-command vault bootstrap (creates folders, copies templates + constitution)
├── template/                   # starting point for a new skill
└── CONTRIBUTING.md             # how to add a skill, including portability rules
```

## Wiki system

The wiki skills (`wiki-operator`, `wiki-synthesizer`, `wiki-librarian`) form a complete personal knowledge system built around an Obsidian vault.

The operating layer lives in this repo:

| Path | Purpose |
|---|---|
| `knowledge-os/constitution.md` | 10 non-negotiable rules Claude follows when operating the wiki |
| `knowledge-os/architecture.md` | Component map, data flow, and note lifecycle reference |
| `templates/` | Note templates (concept, journal, source, map) — copied into `System/templates/` in your vault by `setup-vault.sh` |

Run `./bin/setup-vault.sh ~/path/to/vault` to bootstrap the vault structure, copy templates and the constitution into `System/`, and print the MCP config snippet.

## License

See [LICENSE](./LICENSE).
