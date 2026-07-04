# Agent Skills

A personal collection of [Agent Skills](https://agentskills.io) — portable, self-contained capabilities that any compatible AI agent can discover and load on demand. Built on the open standard originally published by Anthropic, now adopted across Claude, Codex CLI, Gemini CLI, GitHub Copilot, Cursor, and 25+ other platforms.

Each skill is a folder containing a `SKILL.md` file (instructions + metadata) and, where needed, supporting `scripts/`, `references/`, or `assets/`. Nothing here is Claude-specific unless explicitly noted — see [CONTRIBUTING.md](./CONTRIBUTING.md) for the portability rules this repo follows.

## Skills

| Skill | Category | Description |
|---|---|---|
| [`skill-gap-tracker`](./skills/skill-gap-tracker) | Learning / Productivity | Flags unfamiliar languages, frameworks, or techniques encountered while building a project and logs them to a personal learning backlog. |
| [`agent-eval`](./skills/agent-eval) | Agent Design | Designs and runs evaluations for LLM/agent outputs — rubrics, LLM-as-judge scoring, regression test sets, and pass-rate reporting with a runnable scoring script. |
| [`wiki-operator`](./skills/wiki-operator) | Knowledge Management | Operates a Karpathy-style personal knowledge wiki via Obsidian MCP — searches before writing, updates existing concept pages over creating duplicates, and maintains links. Requires Obsidian MCP connected. |

*(More skills coming — see the [project board / roadmap] for what's planned: project-management skills like sprint planning and risk registers, agent-design skills like a skill validator and multi-agent scaffolder.)*

## Installing a skill

**Claude Code (plugin marketplace):**
```bash
/plugin marketplace add Hefrock/agent-skills
/plugin install skill-gap-tracker@agent-skills
```

**Claude Code (manual, no plugin system):**
```bash
cp -r skills/skill-gap-tracker ~/.claude/skills/
```

**claude.ai:**
Zip the individual skill folder (e.g. `skills/skill-gap-tracker/`) and upload via Settings → Features → Custom Skills (requires a paid plan with code execution enabled).

**Other platforms (Codex, Gemini CLI, Cursor, etc.):**
Copy the skill folder into whatever directory that platform scans for skills — the `SKILL.md` format works unmodified.

## Repo structure

```
agent-skills/
├── .claude-plugin/
│   └── marketplace.json   # Claude Code-only install metadata — optional, additive
├── skills/                # flat — one folder per skill, no category nesting
│   └── skill-gap-tracker/
├── template/               # starting point for a new skill
└── CONTRIBUTING.md         # how to add a skill, including portability rules
```

## License

See [LICENSE](./LICENSE).
