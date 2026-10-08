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
5. **Adding custom subagents alongside a skill (optional).** If the skill needs a dedicated `.claude/agents`-style subagent (e.g. for context isolation a skill's own instructions can't enforce on their own), add an `agents/*.md` folder next to the skill and register it in `marketplace.json` too — but two things are easy to get wrong here:
   - The `agents` field only accepts a list of individual `.md` file paths. It does **not** accept a directory, unlike `skills`.
   - Every plugin entry in this repo's `marketplace.json` sets `"source": "./skills"`, which makes the shared top-level `skills/` folder each plugin's *root* for path resolution — that's why `"skills": ["./your-skill-name"]` is written relative to `./skills`, not to the skill's own folder. `agents` paths follow the same rule: `"agents": ["./your-skill-name/agents/some-agent.md"]`, listed explicitly, one per file.
   - **Inside an agent's `.md` body, never hardcode a path like `skills/your-skill-name/references/foo.md`.** That only resolves when Claude Code is running directly inside this cloned repo — it breaks for anyone who actually installs the plugin, since an installed plugin doesn't live at a stable `skills/your-skill-name/...` path relative to any working directory. Use `${CLAUDE_PLUGIN_ROOT}/references/foo.md` instead — Claude Code substitutes it with the real install path at runtime, in skill, command, *and* agent content. `fiction-workshop`'s agent files shipped with the hardcoded form and had to be fixed after the fact — see its `SKILL.md` history for the concrete example.
6. **Add a row to the table in `README.md`.**
7. **Test it** by pointing Claude Code or claude.ai at the folder and confirming it triggers on the example prompts you wrote.

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

After bumping `metadata.version`, tag the commit that ships it:
`git tag vX.Y.Z && git push origin vX.Y.Z`. Without a tag, nothing durably
records which commit shipped which marketplace version — `v0.20.0` itself
was retroactively tagged for this reason once the gap was noticed.

## Keep it portable

- Don't assume Claude Code-only mechanics in the instructions unless the skill is genuinely Claude-specific. The plain `SKILL.md` + `scripts/`/`references/`/`assets/` structure works unmodified across every platform that supports the standard (Codex, Gemini CLI, Cursor, GitHub Copilot, and others).
- Everything under `.claude-plugin/` is additive, Claude Code-only tooling. A skill must still work correctly with that folder deleted entirely.
- **Exception: skills using the `agents/*.md` convention from step 5 above.** Their core instructions depend directly on Claude Code's subagent mechanism, not just on additive `.claude-plugin/` registration — the "still works with `.claude-plugin/` deleted" guarantee does not hold for them, since the dependency lives in `skills/<name>/agents/`, outside that folder. State this plainly in the skill's own `SKILL.md` (a `## What's NOT built here` line is enough — see `fiction-workshop` for the pattern) rather than leaving a platform-specific hard dependency undocumented.
