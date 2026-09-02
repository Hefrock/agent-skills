---
name: broadcast
description: Produces a daily healthcare AI news audio briefing — ingests from ten registered sources (PubMed, arXiv, medRxiv, FDA guidance, regulations.gov, ONC/ASTP, CMS, and industry RSS feeds), dedupes and ranks stories, pins every claim to its source via a real evidence-pinning MCP server, generates a spoken script, rewrites each story with AI narration (grounded, with an automatic fallback to plain text if the narration can't be verified against its source), runs a QA gate, synthesizes audio via Gemini TTS, and produces GitHub-Pages-ready distribution artifacts (podcast RSS feed + Obsidian vault note). Use this skill when the user wants to run, generate, produce, or publish a healthcare AI briefing episode or podcast; asks about the status of a broadcast episode or run; wants to debug a failed or partial episode (QA failures, narration failures, TTS rate limits); wants to add/remove/tune an ingest source; or asks how the broadcast pipeline works. Requires GEMINI_API_KEY and a locally-built evidence-pinning-mcp server (see Prerequisites).
---

# Broadcast — Healthcare AI News Briefing

An automated pipeline that turns the day's healthcare AI news into one spoken-word audio episode, end to end: ingest → dedup/rank → evidence-pinning → script generation → AI narration → QA gate → audio synthesis → distribution artifacts. Every stage is a standalone, independently-tested Python module in `scripts/`; `orchestrate.py` wires them into one real run.

Read this whole file before running anything live — several of the operational risks below were only discovered by actually running the pipeline against real APIs, and repeating those mistakes wastes real (rate-limited, sometimes shared) API quota.

## Prerequisites

- Python 3.12 (stdlib only — no `pip install` needed for any broadcast script).
- Node 22, and the evidence-pinning-mcp server built once (`orchestrate.py` spawns it as a subprocess automatically — it does not need to already be running, just built):
  ```bash
  cd mcp/evidence-pinning && npm ci && npm run build
  ```
- `GEMINI_API_KEY` set in the environment. Used for embeddings (dedup/ranking), AI narration, and text-to-speech — all three stages fail without it.

## Running one episode

```bash
python skills/broadcast/scripts/orchestrate.py --data-dir ~/.broadcast-data \
  [--date YYYY-MM-DD]                    # default: today
  [--max-results-per-source N]           # default: 10 — keep low for a quick/cheap test run
  [--synth-delay-seconds N]              # default: 6.0 — pacing between TTS calls, see risks below
  [--no-narration]                       # skip AI narration, ship plain mechanical text
  [--narration-success-threshold N]      # default: 0.7 — episode-level narration fallback threshold
```

**Use `~/.broadcast-data` as the default `--data-dir` unless the user asks for somewhere else.** It holds persistent state (`dedup_store.json`, `evidence_store/`) and every run's output (`episodes/<date>/{report.json,script.json,episode.wav}`) — deduplication and evidence provenance only accumulate meaningfully if the *same* directory is reused run over run, and nothing else in this repo establishes a canonical location. Don't invent a different path per session; that silently defeats the whole story-continuity design. `orchestrate.py` creates the directory itself if it doesn't exist yet — no setup needed beforehand.

There's also a manually-triggered GitHub Actions workflow, `.github/workflows/broadcast-live-smoke-test.yml` (`workflow_dispatch` only, never on push/PR — it makes real external API calls). It has two jobs: a smoke test of the ingest adapters + embeddings, and a full real episode run via `orchestrate.py`. Triggering it always runs *both* jobs — there is no way to trigger just one.

## Reading the result — what "success" actually looks like

`report.json` (also printed to stdout) is the thing to read, not just the process exit code (which is 1 whenever `episode_produced` is `false`, even though everything upstream may have worked correctly):

- **`qa_passed`** — structural + grounding checks on the script itself (intro/outro present, every story traces to a real pinned claim, no leaked excluded stories). Nothing to do with audio.
- **`episode_produced`** — `true` only if *every* segment's audio synthesized. One segment failing withholds the whole episode's audio (deliberate — a partial episode isn't assembled with a silent gap). Check `synth_failed` for which segments and why.
- **`narration_attempted` / `narration_succeeded` / `narration_success_rate` / `narration_episode_level_fallback` / `narration_failures`** — the AI narration layer's own results. A narration failure is **not a pipeline failure** — it's a best-effort enhancement with an automatic two-tier fallback (per-segment, then whole-episode) to the original mechanical text, by design. A low success rate or `episode_level_fallback: true` means that day's episode shipped with plainer prose, not that anything is broken. These fields are all `null` when `--no-narration` was passed (distinguishable from a real 0/0 result).
- **`ingest_failed`** — per-source ingest failures. `healthcare_it_news` failing with `HTTP 403` is expected every run (see below), not a bug to chase.

## Debugging a failed or partial episode

Work through `report.json` in this order — nearly every failure traces to one of these, not a new regression:

1. **`qa_passed: false`** — look at `qa_checks` for entries with `passed: false`; each one's `detail` names the exact structural problem (a missing intro/outro, a story segment missing `claim_id`, etc.). This should essentially never happen from an unmodified `orchestrate.py` run — `script_gen.py` already enforces these invariants when it builds the script, and `qa_gate.py` only re-checks them as defense in depth. A failure here points to a real upstream bug, not something to route around or re-run past.
2. **`episode_produced: false` but `qa_passed: true`** — the script was fine; audio synthesis failed for at least one segment. Check `synth_failed`: if every entry reads `HTTP Error 429: Too Many Requests`, that's the known Gemini TTS rate limit (see Known operational risks below) — wait for quota to recover before doing anything else, don't immediately re-run. Any *other* error string is a real, new failure worth investigating on its own terms.
3. **`ingest_failed` non-empty** — `healthcare_it_news` failing with `HTTP 403` is expected every run. Any *other* source failing is new: check the error string first — a previously-working source returning a *different* error than before usually means the feed URL or endpoint changed upstream, not a bug in this pipeline.
4. **Low `narration_success_rate` or `narration_episode_level_fallback: true`** — not a failure at all. This is the narration layer's own designed circuit breaker, working as intended; `narration_failures` lists which stories fell back and the specific grounding-check reason (an ungrounded span, a dropped hedge, an out-of-range length ratio). The episode still ships — just with plainer prose for that story or that day.

## Publishing an episode

```bash
python skills/broadcast/scripts/distribute.py --data-dir ~/.broadcast-data --date YYYY-MM-DD \
  --publish-dir <dir> --base-url <public-url> --feed-link <url> \
  [--feed-title "..."] [--feed-description "..."]
```

This reads the episode `orchestrate.py` already produced and writes GitHub-Pages-ready files to `--publish-dir`: the audio file, an RSS `feed.xml`, and a matching Obsidian vault-note markdown file. **It does not push, host, or deploy anything** — that's a deliberate, separate, human-driven step. The RSS feed's URLs are only real once `--publish-dir`'s contents are actually deployed to `--base-url`.

The vault note is markdown output only, not a vault write — landing it in a real Obsidian vault requires a live session with the `wiki-operator` skill's `/source` command and a connected `obsidian-vault` MCP server. `distribute.py` deliberately never writes to a vault directly.

## Adding, removing, or tuning an ingest source

Every source lives in `config/sources.json`, validated at load time by `source_registry.validate_registry()` — a malformed entry raises a specific `RegistryValidationError` there rather than surfacing as a confusing `KeyError` three stages downstream.

- **Adding an RSS-backed source (no code changes needed):** add an entry with `"key"`, `"name"`, `"category"` (an existing category, or a new one under `"categories"` — see "tuning" below), and `"feed_url"`. Any source with a `feed_url` field is automatically dispatched by `orchestrate.py`'s `_fetch_for_source()` to `ingest.fetch_rss()`, the same generic parser already handling five different feeds' real-world quirks (non-RFC-822 `pubDate` formats, a corrupted `<link>` field, etc. — see `ingest.py`'s `_parse_rss_pubdate()` if a new feed's dates come back wrong). Leave `"feed_url_verified": false` until you've actually confirmed it live with `live_smoke_test.py` (see below) — every currently-verified source in this file was confirmed that way, never assumed correct from the URL alone; `healthcare_it_news`'s entry documents exactly what a real, permanent block looks like when verification fails.
- **Adding a query-based or fixed-endpoint source** (like `pubmed`/`arxiv`/`regulations_gov`, or `medrxiv`/`fda_guidance`) needs real code, not just config: a new `fetch_*()` function in `ingest.py` (see those six functions for the two existing patterns) *and* a new dispatch branch in `orchestrate.py`'s `_fetch_for_source()` keyed on the source's `"key"`. Without both, that source raises `ValueError` at ingest time.
- **Removing a source:** delete its entry from `"sources"` — nothing else references sources by a fixed list, `ingest_all()` just iterates whatever's currently in the registry.
- **Tuning relevance scoring:** `authority_floor` (0–1) and `half_life_days` (>0) are set per-*category*, not per-source (see `source_registry.py`'s docstring for what they control — a higher floor and longer half-life age a story more slowly). Every category and query in `config/sources.json` already carries a `*_note` field marking it as "a working default, not a validated measurement" — change these against real episode output, not intuition, and update the note to record why.
- **After any change**, run `python skills/broadcast/scripts/live_smoke_test.py` — it's cheap (real ingest + embedding calls, no TTS or narration spend) and will confirm the new or changed source actually returns real items before it's trusted in a full, expensive episode run.

## Known operational risks — read before running live

- **Gemini TTS rate-limits under realistic call volume**, confirmed live more than once. Never retrigger a failed or in-flight run back-to-back — wait for it to fully finish first. A blind retry is itself another request competing for the same already-exhausted quota; this made a real rate-limit situation *worse* in this project's own history, not better. `synthesize_text()`'s retry policy (`gemini_retry.py`) already honors the server's `Retry-After` header; `--synth-delay-seconds` adds further pacing. If you see a wall of `HTTP 429` in `synth_failed`, wait — don't immediately re-run.
- **`healthcare_it_news` is permanently blocked** (`HTTP 403` on every fetch) — a documented, accepted ingest failure, not something to debug.
- **`narrate.py`'s pinned model was chosen from live reconnaissance**, not assumed — see its module docstring for the full account (a deprecated model, two overloaded newer ones, then a working one). If narration starts failing broadly, check whether that model id is still available/healthy before assuming a code regression.

## What this skill does not do yet

- **No scheduling.** Every run is manual — direct script invocation or a manually-triggered `workflow_dispatch`. Nothing produces an episode automatically on a cadence.
- **No real hosting.** `distribute.py`'s output must be manually deployed (e.g. to GitHub Pages) for its feed to be reachable at `--base-url`.
- **No `itunes:` RSS namespace tags** — no cover art, category, explicit flag, or subtitle. The feed is valid RSS 2.0 but likely won't display well in a real podcast app yet.
- **No cross-story "digest" synthesis.** Narration is strictly per-story, isolated to that story's own text — a deliberate scope decision made after research into hallucination risk in broader-context AI summarization, not an oversight. See `narrate.py`'s docstring for the reasoning.
- **No cost/budget tracking** on Gemini API usage across embeddings, narration, and TTS.
- **No retention policy** — `dedup_store.json` and `episodes/<date>/` accumulate indefinitely in `--data-dir`.

## Output discipline

- Always report `qa_passed`, `episode_produced`, and the narration/synth failure buckets together when summarizing a run — a summary that omits them can make a partially-failed episode look clean.
- Never say an episode was "published" unless `distribute.py` has run *and* its `--publish-dir` output has actually been deployed somewhere reachable at `--base-url`. Writing `feed.xml` locally is not publishing.
- A low narration success rate is informational, not an error — don't treat it with the same urgency as a QA or synthesis failure.
- If `synth_failed` shows `HTTP 429`, say so plainly and recommend waiting — don't immediately re-run the episode (see Known operational risks).
