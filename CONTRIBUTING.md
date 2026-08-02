# Contributing a new skill

This repo follows the open [Agent Skills standard](https://agentskills.io) — every skill is a self-contained folder with a `SKILL.md` file, kept flat under `skills/` (no category subfolders). Categorization lives in this README and in `.claude-plugin/marketplace.json`, not in the directory tree.

## Steps

1. **Copy the template.**
   ```bash
   cp -r skill-template skills/your-skill-name
   ```
2. **Write `SKILL.md`.**
   - `name` should match the folder name exactly.
   - `description` is the most important field — it's what Claude reads to decide whether to load the skill. Be specific about trigger conditions, not just what the skill does.
   - Keep the body under ~500 lines. If it needs to be longer, move detail into a `references/` subfolder and load it on demand instead of dumping everything into the trigger-time file.
3. **Add only the subfolders you need** (`scripts/`, `references/`, `assets/`) — don't scaffold empty ones.
4. **Register it for Claude Code installs.** Add an entry to `.claude-plugin/marketplace.json`:
   - To make it independently installable, add a new object to `plugins`.
   - To bundle it with an existing plugin instead, add its path to that plugin's `skills` array.
5. **Add a row to the table in `README.md`.**
6. **Test it** by pointing Claude Code or claude.ai at the folder and confirming it triggers on the example prompts you wrote.

## Editing an existing skill's content

Bump `metadata.version` in `.claude-plugin/marketplace.json` whenever a PR
changes content inside an already-installed skill (`SKILL.md`, a `scripts/`
or `references/` file, `knowledge-os/constitution.md`, etc.) — not just when
adding a new skill. Claude Code gates content refresh on that version number:
an edit that ships without a bump sits invisibly on GitHub while every
existing install keeps serving the old cached copy, `/plugin marketplace
update` included. This repo shipped a real governance-scope fix (Laws 6/7/8)
that sat live on `main` for two days without reaching an installed vault,
purely because this step was skipped — see `knowledge-os/sitrep.md`.

## Keep it portable

- Don't assume Claude Code-only mechanics in the instructions unless the skill is genuinely Claude-specific. The plain `SKILL.md` + `scripts/`/`references/`/`assets/` structure works unmodified across every platform that supports the standard (Codex, Gemini CLI, Cursor, GitHub Copilot, and others).
- Everything under `.claude-plugin/` is additive, Claude Code-only tooling. A skill must still work correctly with that folder deleted entirely.
