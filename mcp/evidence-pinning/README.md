# evidence-pinning MCP

MCP server that pins claims (assertions a piece of generated content makes) against
the source evidence that backs them, keeps an append-only provenance log, and checks
sources for decay — retraction, correction, or unreachability — over time. Built as
shared infrastructure: any skill that generates content and needs to enforce "distill,
don't invent" can be a consumer, not just one skill's private database.

## Tools

| Tool | Purpose |
|---|---|
| `register_source` | Register a URL (DOI/PMID auto-extracted, or forced via `id_hint`) as an evidence source. Idempotent — re-registering the same canonical source returns the existing `source_id`. |
| `pin_claim` | Register a claim against one or more already-registered sources, with the excerpt that backs it. Fails if any `source_id` isn't registered — an unsourced claim can't be pinned. Idempotent per `(run_id, text)`. |
| `get_claims` | List every claim pinned for a run, with backing sources (including current decay status) inlined — what a QA gate reads to check a draft against pinned evidence. |
| `verify_claim` | Look up a single claim by `claim_id`, sources inlined. |
| `flag_claim` | Mark a claim flagged (e.g. a QA gate found its excerpt doesn't actually support it). Flagged claims should not proceed downstream. |
| `check_source_decay` | Re-check a source's live status. DOI sources are checked against Crossref (CrossMark `update-to`) for retraction/correction; PMID sources against PubMed (`PublicationType`/`CommentsCorrections`) for the same; plain URLs only for reachability (no general retraction signal exists for arbitrary web content). If a source transitions to `retracted`, every currently-pinned claim citing it is auto-flagged. |
| `get_provenance` | Append-only log for a claim/source/run — which stage touched it, what action, when. |

## Network egress this server needs

`check_source_decay` makes outbound HTTPS calls to `api.crossref.org` (DOI checks) and
`eutils.ncbi.nlm.nih.gov` (PMID checks). In an environment with a default-deny egress
policy, both must be explicitly allowlisted or these checks always come back
`unreachable` — indistinguishable from a genuinely dead source, since a blocked
connection and a dead one look identical from inside the server. Every other tool
(`register_source`, `pin_claim`, `get_claims`, `verify_claim`, `flag_claim`,
`get_provenance`, and the retraction-cascade logic) needs no network access at all.

## Install

```bash
cd mcp/evidence-pinning
npm install
npm run build
```

## Configure (Claude Code)

**Recommended — via the CLI:**
```bash
claude mcp add evidence-pinning \
  -s user \
  -e EVIDENCE_STORE_PATH=/absolute/path/to/your/evidence-store \
  -- node /absolute/path/to/agent-skills/mcp/evidence-pinning/dist/index.js
```
`-s user` registers the server at the user level and writes to `~/.claude.json` for you.
If `node` isn't found on `PATH` when Claude Code runs it, use `which node`'s output as
the command instead of the bare `node`.

**Manual** — add this to `~/.claude.json` yourself:
```json
{
  "mcpServers": {
    "evidence-pinning": {
      "command": "node",
      "args": ["/absolute/path/to/agent-skills/mcp/evidence-pinning/dist/index.js"],
      "env": {
        "EVIDENCE_STORE_PATH": "/absolute/path/to/your/evidence-store"
      }
    }
  }
}
```

Then verify inside Claude Code:
```
/mcp
```

## Storage

Plain JSON files under `EVIDENCE_STORE_PATH`, created on first run — no database:

```
sources.json        # source_id -> Source record
claims.json         # claim_id  -> Claim record
provenance.jsonl     # append-only event log, one JSON object per line
```

Consistent with this repo's git-diffable-plain-files preference elsewhere
(`wiki-warehouse`'s `manifest.json`). Writes to the two JSON maps are atomic
(temp file + rename); the provenance log is append-only by design.

## Security

- Runs locally over STDIO — no inbound network exposure
- The only outbound calls are `check_source_decay`'s Crossref/PubMed/URL checks (see above)
- No path-traversal surface — this server doesn't touch arbitrary filesystem paths the way `obsidian-vault` does; all reads/writes are confined to the three files under `EVIDENCE_STORE_PATH`

## Known limitations

- **Crossref retraction detection is unverified against a live retracted DOI.** The
  classifier reads Crossref's documented CrossMark `update-to` schema, but hasn't been
  checked against a real retracted-paper response. Verify before trusting it in
  production — see the caveat in `src/decay.ts`.
- **PubMed XML parsing is regex-based, not a real XML parser** (no XML dependency in
  this repo yet). Works for PubMed's typical flat tag structure; would miss a
  namespaced or attribute-heavy variant of the same fields.
- **Plain-URL sources can only ever become `unreachable`, never `retracted`/`updated`**
  — there's no general-purpose "was this page retracted" signal for arbitrary web
  content, unlike DOI/PMID sources.
