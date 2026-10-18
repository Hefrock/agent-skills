# Page Standard

*A format guide, not a law: the [[constitution]] holds the non-negotiable rules, and the wiki-operator skill (`skills/wiki-operator/SKILL.md`) defines the frontmatter schema. This page says how a page should look and how to show where its claims came from. Templates live in `Templates/` in the vault, copied from `skills/wiki-operator/assets/`.*

## Two principles

1. **Readable.** A smart person who doesn't know the topic should understand the opening paragraph. Short sentences. Jargon explained where it first appears. Detail comes after, for those who want it.
2. **Traceable.** A reader should be able to follow any non-trivial claim to where it came from, and see how well that source was checked.

Existing pages are not rewritten for this. They are brought up to it when they are revised.

## Which template for which page

| Page type | Template | Lives in |
|---|---|---|
| Concept | `Templates/Concept` | `Knowledge/` |
| Source | `Templates/Source` | `Sources/` |
| Project (live) | `Templates/Project` | `Projects/` |
| Journal | `Templates/Journal` | `Journal/Daily/` |
| Map | `Templates/Map` | `Maps/` |

Anything else kept in `Templates/` (for example a personal `Portfolio/` subfolder) is outside the knowledge graph.

## Frontmatter

The authoritative schema is in the wiki-operator skill. In short: `type`, `status`, `confidence`, `updated` on every note, plus `verification` and `verified_against` on any note making factual or identity claims.

- `confidence` is about accuracy, not polish.
- `verification` records how a claim was checked and caps `confidence`: only `vendor` (traced to the author's or standards body's own document) or `multi-source` (two or more independent sources agree) can support `high`.
- `verified_against` is the actual link or source name. A tier of `vendor` or `multi-source` with nothing here is an unbacked label.
- Don't retrofit `verification` onto older pages on sight, but set it on any new page or any edit that touches a factual claim.
- **Journals** record a day, not a verified claim, so they don't need `verification`. Set `confidence: medium` when the entry's findings rest on search results or secondary sources, and label each external source link "opened" or "search result only".

## Provenance: what to link

Use as many of these as apply. Each tells the reader something different.

1. **Where it came from in this vault.** `Captured from [[Journal/Daily/YYYY-MM-DD]]` on concept pages; the concept pages a source informed, on source pages (constitution Law 8).
2. **External sources, as readable links.** `[Title](URL)`, followed by **opened** or **search result only**. Never present a detail seen only in a search summary as if the page was read.
3. **Claims about code or a repo.** Link a **permalink at a specific commit** (`…/blob/<sha>/path` or `…/commit/<sha>`), not a branch, and say whether the docs or the code were actually checked.
4. **No source.** Say "general knowledge, unsourced" in plain words. Don't leave the reader guessing.
5. **Unverified numbers** quoted from a source stay attributed to it: "the repo reports…", not stated as fact.

## Body layout

- **Concept:** In plain language → Core idea → Why it matters → Key properties → Open questions → Related → Sources.
- **Source:** header block (type, author, link, read on, how read) → In plain language → Core argument → Key takeaways → Concepts referenced → Quotes worth keeping → Sources.
- **Project:** what done looks like → In plain language → Goal → Status → Key concepts → Decisions (dated, with the reason) → Open questions → Related → Sources.
- **Journal:** What happened today → What I learned → Decisions (optional) → Open questions → Still to do → Ideas to promote. On a quiet day keep only What happened today, What I learned and Ideas to promote. Other wiki mechanics (links added, fixes, map updates) stay out of the entry body. Run logs from synthesis, audit and governance runs go in `Journal/Runs/YYYY-MM-DD.md` (constitution Law 9), not in the daily note, which may link to the run log in one line.
- **Map:** In plain language → concepts by level → Sources → Active projects → Open questions → Sources for this map.

Every page with `confidence: low` or `verification: name-match` needs an Open questions section naming what is unverified.

## Typed relations in Related

Write each link with a relation and a reason: `is-a`, `uses`, `depends-on`, `extends`, `contrasts-with`, `instance-of`, `see-also`.

## When to change `updated`

Bump it when the **content or canon** changes: a claim added, corrected or removed. Do **not** bump it for adding a link, fixing a typo, or changing a heading, and never to clear a staleness flag. Otherwise a page looks freshly reviewed when only its scaffolding was touched.

## Exempt from this standard

System files under `System/`, the underscore maps (`_context`, `_ask_log`, `_gaps`), `Fiction/`, and `Templates/` — see the constitution's scope clauses for each. Templates are scaffolding, not knowledge.
