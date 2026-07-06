# Agent Skills

A personal collection of [Agent Skills](https://agentskills.io) — portable, self-contained capabilities that any compatible AI agent can discover and load on demand. Built on the open standard originally published by Anthropic, now adopted across Claude, Codex CLI, Gemini CLI, GitHub Copilot, Cursor, and 25+ other platforms.

Each skill is a folder containing a `SKILL.md` file (instructions + metadata) and, where needed, supporting `scripts/`, `references/`, or `assets/`. Nothing here is Claude-specific unless explicitly noted — see [CONTRIBUTING.md](./CONTRIBUTING.md) for the portability rules this repo follows.

## Skills

| Skill | Category | Description |
|---|---|---|
| [`agent-eval`](./skills/agent-eval) | Agent Design | Designs and runs evaluations for LLM/agent outputs — rubrics, LLM-as-judge scoring, regression test sets, and pass-rate reporting with a runnable scoring script. |
| [`wiki-operator`](./skills/wiki-operator) | Knowledge Management | On-demand vault operations — `/learn`, `/update`, `/connect`, `/review`, `/quiz`, `/map`, `/source`, `/clean`, `/health`. The primary interface for working with the wiki. Requires Obsidian MCP connected. |
| [`wiki-synthesizer`](./skills/wiki-synthesizer) | Knowledge Management | Batch compilation — promotes flagged journal ideas into concept pages, compiles `Sources/raw/` into source pages, updates the hot cache. Run after learning sessions. Requires Obsidian MCP connected. |
| [`wiki-librarian`](./skills/wiki-librarian) | Knowledge Management | Structural maintenance — audits broken links, orphans, stale notes, duplicates, and contradictions. Proposes fixes with confirmation. Run weekly. Requires Obsidian MCP connected. |
| [`wiki-journal-processor`](./skills/wiki-journal-processor) | Knowledge Management | Journal preprocessing — extracts durable knowledge fragments from free-form notes, structures them into `## Ideas to promote`, and flags open questions. Run before wiki-synthesizer when journals are messy. Requires Obsidian MCP connected. |

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
├── .claude-plugin/
│   └── marketplace.json        # Claude Code-only install metadata — optional, additive
├── skills/                     # flat — one folder per skill, no category nesting
│   ├── agent-eval/
│   ├── wiki-operator/          # on-demand vault operations
│   ├── wiki-synthesizer/       # journal → concept page compilation
│   ├── wiki-librarian/         # structural health audits
│   └── wiki-journal-processor/ # journal preprocessing before synthesis
├── mcp/
│   └── obsidian-vault/         # MCP server required by wiki-operator
│       ├── src/index.ts        # 10 tools: search, read, write, append, patch, query, links, delete
│       └── README.md           # setup and configuration guide
├── bin/
│   └── setup-vault.sh          # one-command vault bootstrap
├── template/                   # starting point for a new skill
└── CONTRIBUTING.md             # how to add a skill, including portability rules
```

## Wiki system

The wiki skills (`wiki-operator`, `wiki-synthesizer`, `wiki-librarian`, `wiki-journal-processor`) form a complete personal knowledge system. The operating system layer — constitution, architecture docs, and templates — lives in a companion repo:

**[Hefrock/claude-knowledge-os](https://github.com/Hefrock/claude-knowledge-os)** — HOW Claude behaves. This repo is WHAT the skills do.

## License

See [LICENSE](./LICENSE).
