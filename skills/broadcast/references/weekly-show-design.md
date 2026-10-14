# Weekly show: design record

A daily text archive plus a weekly two-host audio show built from it. This file records decisions and what is verified. Update it when the build changes.

## Settled decisions

- **Daily output is text only.** No daily narration or TTS; the only Gemini calls are per-item embeddings. Volume: the same 3 + 7 ranking as the episode.
- **Daily entry is facts only:** the source's own headline and summary, link, DOI/PMID, category, `claim_id`, `canonical_id`, `relevance_score`, date. No model-written words. It is written by `archive.py` from ranked items and pinned claims, not from `script.json` (which holds narrated text).
- **Weekly show: 10-12 minutes.** At an assumed 150 spoken words per minute (not measured) that is about 1,500-1,800 words, roughly 8-9 stories after intro and outro, chosen from the week's ~70 by the existing relevance score. Measure real Gemini speaking pace with one short sample before fixing counts.
- **Two discussants, grounded turns only.** Factual turns are generated one story at a time, each checked against that story's excerpt, and stitched with a small set of fixed hand-written link and reaction turns. No single free-running conversation. Rationale: `narrate.py` records that cross-story synthesis was kept out on purpose; this is the mitigation for reversing it.
- **Source depth:** feed summaries for news, abstracts for PubMed/arXiv/medRxiv. No full-text fetching (WAF blocks and rights questions).
- **Provider for the weekly text is undecided** (Gemini or Claude). The generation function is injectable; decide after the grounding contract exists and the first week is measured.

## Build state

| Piece | State |
|---|---|
| Hedge matching on word boundaries (`narrate.py`) | Built, tested |
| DOI extraction, sticky `retracted`, `pin_claim` conflict (`mcp/evidence-pinning`) | Built, tested. Sticky status is wired in `index.ts` but covered end to end only by a pure-function test |
| `--text-only`, `call_counts`, archive writer | Built, tested, and run once against the real evidence server with fake ingest |
| Per-turn grounding contract (`weekly_grounding.py`) | Built, tested. Not yet called by anything |
| Week-pack builder, weekly generator, chunked two-voice synthesis | Not built |

## What the first design got wrong (independent review, verified)

- `script.json` is written after narration, so an archive built from it is not source text.
- The 14-day dedup window only drops same-day duplicates. Earlier-day repeats are `rolling_followup` and still selected, and each day's repeat gets a new `claim_id` (it hashes `run_id|text`). The week pack must group by `canonical_id` itself.
- `check_narration_grounded` could not be reused per turn (single `source_text`, length ratio, spans not persisted). `weekly_grounding.py` replaces it for the weekly show.
- `run_id` is the bare ISO date, so one `get_claims` per date can rebuild a week, but claims lack category, DOI/PMID and date, and stories without a summary have no claim. The archive entries carry those fields so the pack does not depend on the evidence store.
- `synthesize_dialogue` makes one call per invocation and has only run on 2-turn inputs. Whether one call can carry a long script is unverified; chunking per story multiplies 429 exposure.

## Unverified

- The Crossref retraction field. The classifier reads `update-to`, which as far as I understand belongs on the retraction notice, not the retracted paper (`updated-by`). Not checked against a live response.
- Live TTS behavior for the weekly script, and real speaking pace.
- Whether `check_source_decay` is wired to anything (nothing in the Python pipeline calls it).
- Live ingest from every source, from an environment with open egress.

## Parked, with the trigger that would justify building it

| Idea | Build when |
|---|---|
| Archive web pages and weekly index in `AI_Stylet/docs` | A reader needs to check a story, or week 2 starts |
| MP3 output with WAV fallback | The first publish is rejected by a validator or app, or the repo grows noticeably |
| Decay check as a pipeline step | The manual check in the first weekly prototype is run in two weeks |
| Per-source cap in the week pack | The prototype pack's mix is wrong in two weeks |
| Scheduled daily job | Two manual daily runs succeed and network access is open |
| Claude-vs-Gemini comparison | Gemini's weekly text fails review twice |
| File lock in the evidence server | Two processes overlap |
| Number/entity check in the grounding contract | A paraphrase with a wrong figure passes the span check |

## Dropped

Firecrawl coverage enrichment, "also reported by" clustering, an LLM relevance gate and LLM ranking, a coverage-first summary prompt (there is no full text to cover), a text cache, an audit job that opens issues, and server-side substring verification of excerpts.
