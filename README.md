# Agent Skills

[![CI](https://github.com/Hefrock/agent-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/Hefrock/agent-skills/actions/workflows/ci.yml)

A personal collection of [Agent Skills](https://agentskills.io) — portable, self-contained capabilities that any compatible AI agent can discover and load on demand. Built on the open standard originally published by Anthropic, now adopted across Claude, Codex CLI, Gemini CLI, GitHub Copilot, Cursor, and 25+ other platforms.

Each skill is a folder containing a `SKILL.md` file (instructions + metadata) and, where needed, supporting `scripts/`, `references/`, or `assets/`. Nothing here is Claude-specific unless explicitly noted — see [CONTRIBUTING.md](./CONTRIBUTING.md) for the portability rules this repo follows.

> [!IMPORTANT]
> **Stalled-work tracking.** Tasks blocked on external human action (a DUA signature, a sign-up, a records request) are tracked as GitHub issues and surfaced weekly as a phone notification — see [`docs/stalled-work-tracking.md`](./docs/stalled-work-tracking.md) for how to open and resolve one.

**Jump to:** [Skills](#skills) · [Installing](#installing-a-skill) · [Repo structure](#repo-structure) · [Wiki system](#wiki-system)

## Skills

| Skill | Category | Description |
|---|---|---|
| [`agent-eval`](./skills/agent-eval) | Agent Design | Designs and runs evaluations for LLM/agent outputs — rubrics, LLM-as-judge scoring, regression test sets, and pass-rate reporting with a runnable scoring script. |
| [`agent-redteam`](./skills/agent-redteam) | Agent Design | Generates adversarial test cases for safe-failure testing — refusals, hedging, graceful degradation. Pairs with agent-eval for scoring. |
| [`deid-reid-harness`](./skills/deid-reid-harness) | Agent Design | Adversarial de-identification ⟷ re-identification eval harness for clinical text — generates synthetic notes with ground-truth PHI spans, runs a de-id pipeline, and scores Safe Harbor leakage, Expert Determination re-id risk, and free-text inference across a privacy-utility frontier. Model-independent, verified offline with bootstrap CIs and significance tests. ([sample results](./skills/deid-reid-harness/RESULTS.md)) |
| [`repo-pincer`](./skills/repo-pincer) | Agent Design | Reverse-engineers a codebase by reconciling top-down claims (docs, README, API surface) against bottom-up reality (actual implementation) — classifies every claim as Confirmed, Drift, Aspirational, or Silent. Runs standalone; optionally compiles into a wiki vault. |
| [`wiki-operator`](./skills/wiki-operator) | Knowledge Management | On-demand vault operations — `/learn`, `/update`, `/connect`, `/ask`, `/review`, `/quiz`, `/map`, `/source`, `/clean`, `/health`. The primary interface for working with the wiki. Requires Obsidian MCP connected. |
| [`wiki-synthesizer`](./skills/wiki-synthesizer) | Knowledge Management | Batch compilation — automatically preprocesses unstructured journals, promotes ideas into concept pages, compiles `Sources/raw/` into source pages, updates the hot cache. Run after learning sessions. Requires Obsidian MCP connected. |
| [`wiki-librarian`](./skills/wiki-librarian) | Knowledge Management | Structural maintenance — audits broken links, orphans, stale notes, duplicates, and contradictions. Proposes fixes with confirmation. Run weekly. Requires Obsidian MCP connected. |
| [`wiki-governor`](./skills/wiki-governor) | Knowledge Management | Self-governing maintenance loop — orchestrates the librarian and synthesizer, then adds a constitution-compliance audit, a tracked health score, and a knowledge-gap queue. Keeps the vault accountable to its own rules. Requires Obsidian MCP connected. |
| [`wiki-warehouse`](./skills/wiki-warehouse) | Knowledge Management | Cold storage for raw documents — ingests PDFs/ebooks/scans into a separate private GitHub repo (`intake.py`: hash → extract text, OCR fallback for scans → manifest), then writes a lean content-hash pointer note into the vault. Keeps originals and full text out of the vault. `/ingest`, `/warehouse-audit`. Requires Obsidian MCP + the warehouse repo cloned. |

## Installing a skill

**Claude Code (plugin marketplace):**
```bash
/plugin marketplace add Hefrock/agent-skills
/plugin install agent-eval@hefrock-agent-skills
```
Install any other skill the same way — swap `agent-eval` for the plugin name from the table above (e.g. `/plugin install wiki-governor@hefrock-agent-skills`).

**Updating after new skills are added to this repo:**
```bash
/plugin marketplace update hefrock-agent-skills
```
Run this before installing anything added since your last update — it refreshes the marketplace's list of *available* plugins. Already-installed plugins pick up content changes (new commands, edited `SKILL.md`) automatically; a plugin that's new to the marketplace still needs its own `/plugin install <name>@hefrock-agent-skills` afterward. If a fresh plugin still comes back "not found" right after `marketplace update`, restart the CLI session and retry.

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
│   ├── agent-eval/             # rubric-based evals, LLM-as-judge, regression test sets
│   ├── agent-redteam/          # adversarial case generation, pairs with agent-eval
│   ├── deid-reid-harness/      # clinical de-id/re-id eval — scripts, refs, 31-test suite
│   ├── repo-pincer/            # codebase reverse-engineering — claims vs. reality reconciliation
│   ├── wiki-operator/          # on-demand vault operations
│   ├── wiki-synthesizer/       # journal preprocessing + concept page compilation
│   ├── wiki-librarian/         # structural health audits
│   ├── wiki-governor/          # maintenance loop + constitution compliance + health score
│   └── wiki-warehouse/         # raw-document cold storage (external repo) + vault pointers
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

</details>

## Wiki system

The wiki skills (`wiki-operator`, `wiki-synthesizer`, `wiki-librarian`, `wiki-governor`, `wiki-warehouse`) form a complete personal knowledge system built around an Obsidian vault. `wiki-warehouse` adds a separate private "cold storage" repo for raw documents, keeping originals out of the vault while indexing them by content-hash pointer.

The operating layer lives in this repo:

| Path | Purpose |
|---|---|
| `knowledge-os/constitution.md` | 10 non-negotiable rules Claude follows when operating the wiki |
| `knowledge-os/architecture.md` | Component map, data flow, and note lifecycle reference |
| `knowledge-os/sitrep.md` | Living status + gap analysis for the wiki system — what's shipped, what's untested, what's next |
| `templates/` | Note templates (concept, journal, source, map) — copied into `System/templates/` in your vault by `setup-vault.sh` |

Run `./bin/setup-vault.sh ~/path/to/vault` to bootstrap the vault structure, copy templates and the constitution into `System/`, and print the MCP config snippet.

## License

See [LICENSE](./LICENSE).
