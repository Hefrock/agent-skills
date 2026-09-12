# Claims ledger schema

A research-ledger JSON file has two top-level keys: `run` (metadata about the
research pass) and `claims` (every claim it surfaced, confirmed or not).

```json
{
  "schema": "research-ledger/v1",
  "run": {
    "topic": "what's new in AI explainability since trustyAI's 2022 release",
    "date": "2026-09-09",
    "skill": "deep-research",
    "sources_count": 23,
    "claims_count": 109,
    "claims_confirmed": 24,
    "ops_stats": {
      "agents": 105,
      "tokens_approx": 3700000,
      "wall_clock_minutes": 95,
      "note": "took 3 workflow runs: synthesis-stub bug, then a spend-limit interruption"
    }
  },
  "sources": [
    {
      "title": "On the Biology of a Large Language Model",
      "url": "https://transformer-circuits.pub/2025/attribution-graphs/biology.html",
      "fetch_status": "fetched",
      "warehouse_doc_id": "sha256:0a17caa271974ab39d618b7aa86a650984049bd8c4afd943607dd654314a4c73"
    }
  ],
  "claims": [
    {
      "id": 1,
      "text": "Anthropic's attribution graphs demonstrate genuine multi-hop internal reasoning via feature-swap interventions.",
      "verdict": "confirmed",
      "source_refs": ["On the Biology of a Large Language Model"],
      "votes": null,
      "granularity_note": "deep-research's output reported a pass/fail verdict only; per-vote reasoning was not exposed in the final text, so this field is null rather than invented.",
      "promoted_to": "Knowledge/AI/ai-explainability.md"
    },
    {
      "id": 47,
      "text": "IBM's AIX360 is the direct open-source successor to trustyAI.",
      "verdict": "rejected",
      "source_refs": [],
      "votes": {"confirm": 1, "refute": 2},
      "granularity_note": "vote split was stated explicitly in the run's output.",
      "promoted_to": null
    }
  ]
}
```

## Field notes

- **`verdict`**: one of `confirmed`, `rejected`, `split`, `unconfirmed`. Use
  `unconfirmed` (not `rejected`) when the run simply didn't reach a verdict, to
  avoid implying the claim was actively refuted.
- **`votes`**: only populate with real numbers/reasoning the run's output stated.
  `null` is the correct value when that detail wasn't exposed — see the Hard
  Limitation section in `SKILL.md`. Never estimate or reconstruct a vote split
  that wasn't actually reported.
- **`promoted_to`**: the vault path a confirmed claim ended up distilled into, if
  any — lets a future reader jump from "this claim was rejected" or "this claim
  fed into that page" without re-reading the whole ledger.
- **`source_refs`**: match against `sources[].title`, not URLs — URLs rot (see the
  C2PA source recovered on 2026-09-10), titles are the more stable join key within
  a single ledger.

## `intake.py` extraction support for `.json`

As of this skill's creation, `knowledge-warehouse/bin/intake.py`'s `extract()`
dispatch handles `.pdf`, `.txt`/`.md`/`.markdown`/`.text`, and `.html`/`.htm` — a
bare `.json` file falls through to the `unsupported-type` branch, which still
stores and hashes the original but leaves its `text_path` empty (no searchable
text). Either:

- Save the ledger with a `.md` extension containing the JSON inside a fenced code
  block (picks up the `plaintext` extraction path, keeps it searchable), or
- Add a one-line case to `extract()` treating `.json` like `.txt` (read directly,
  `"plaintext"` method) — the more correct long-term fix, since it keeps the raw
  file's extension honest about its actual type.

Check which is true in the warehouse repo's current `bin/intake.py` before
ingesting a ledger; don't assume either without looking.
