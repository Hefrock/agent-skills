# Agent Skills

A personal collection of [Agent Skills](https://agentskills.io) — portable, self-contained capabilities that any compatible AI agent can discover and load on demand. Built on the open standard originally published by Anthropic, now adopted across Claude, Codex CLI, Gemini CLI, GitHub Copilot, Cursor, and 25+ other platforms.

Each skill is a folder containing a `SKILL.md` file (instructions + metadata) and, where needed, supporting `scripts/`, `references/`, or `assets/`. Nothing here is Claude-specific unless explicitly noted — see [CONTRIBUTING.md](./CONTRIBUTING.md) for the portability rules this repo follows.

## Skills

| Skill | Category | Description |
|---|---|---|
| [`agent-eval`](./skills/agent-eval) | Agent Design | Designs and runs evaluations for LLM/agent outputs — rubrics, LLM-as-judge scoring, regression test sets, and pass-rate reporting with a runnable scoring script. |
| [`wiki-operator`](./skills/wiki-operator) | Knowledge Management | Operates a Karpathy-style personal knowledge wiki via Obsidian MCP — searches before writing, updates existing concept pages over creating duplicates, and maintains links. Requires Obsidian MCP connected. |

## Installing a skill

**Claude Code (plugin marketplace):**
```bash
/plugin marketplace add Hefrock/agent-skills
/plugin install agent-eval@agent-skills
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
│   └── wiki-operator/
├── mcp/
│   └── obsidian-vault/         # MCP server required by wiki-operator
│       ├── src/index.ts        # 8 tools: search, read, write, patch, query, links
│       └── README.md           # setup and configuration guide
├── template/                   # starting point for a new skill
└── CONTRIBUTING.md             # how to add a skill, including portability rules
```

## License

See [LICENSE](./LICENSE).
