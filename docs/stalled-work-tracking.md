# Stalled-work tracking

How tasks blocked on external human action — a DUA/DSA signature, an account
sign-up, a records-release email, an IRB response — get tracked as GitHub
issues instead of memory, and surfaced automatically.

## Opening one

Use the **🚧 Blocked on human action** or **📅 Dated follow-up** issue
template (top of "New Issue" on any repo here). The title tag is what the
weekly digest matches on:

- `[blocked-human] ...` — no date. Stays visible for as long as it's open.
- `[followup: YYYY-MM-DD] ...` — surfaces only once that date has passed.

## The digest

A weekly automated Routine discovers every public repo under this account,
scans open issues for the two title tags, and pushes a phone notification
for anything due. It does an exact title-prefix match via the GitHub API —
no labels, no per-repo setup required.

Browse current items (convenience only — GitHub's search UI tokenizes
brackets loosely, so these links aren't authoritative the way the digest's
own API match is):
[blocked](https://github.com/Hefrock/agent-skills/issues?q=is%3Aopen+is%3Aissue+%5Bblocked-human%5D+in%3Atitle) ·
[pending follow-ups](https://github.com/Hefrock/agent-skills/issues?q=is%3Aopen+is%3Aissue+%5Bfollowup%3A+in%3Atitle)

## Working with items

| Action | How |
|---|---|
| Resolve | Close the issue |
| Update | Edit it — the digest reads live state, nothing to sync |
| Postpone | Change the `YYYY-MM-DD` in a follow-up's title |

A `[blocked-human]` item has no date by design — it keeps resurfacing until
closed, since there's nothing to postpone to.
