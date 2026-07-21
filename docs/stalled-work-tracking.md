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
scans open issues for the two title tags, and pushes a phone/browser
notification for anything due. It does an exact title-prefix match via the
GitHub API — no labels, no per-repo setup required.

The notification is a nudge, not the record — it's short (mobile OSes
truncate long messages) and easy to miss if you're logged out or the tab
isn't open. Two durable ways to check regardless of whether you saw it:

**Live, always current — GitHub search, across every repo:**
[blocked](https://github.com/issues?q=is%3Aopen+is%3Aissue+user%3AHefrock+%5Bblocked-human%5D+in%3Atitle) ·
[pending follow-ups](https://github.com/issues?q=is%3Aopen+is%3Aissue+user%3AHefrock+%5Bfollowup%3A+in%3Atitle)
(convenience only — GitHub's search UI tokenizes brackets loosely, so these
links aren't authoritative the way the digest's own exact-match API call
is; if this and GitHub ever disagree, GitHub is right).

**Snapshot, redeployed weekly — the [stalled-work dashboard](https://claude.ai/code/artifact/3652c9f9-5bd1-45fa-ba7f-343245d4e375):**
a bookmarkable page grouping open items by repo with a one-line summary and
an age indicator per item. Accurate as of the last Monday scan, not live —
useful for a quick read without leaving the browser tab, but the search
links above are the ones to trust if it's gone stale.

## Working with items

| Action | How |
|---|---|
| Resolve | Close the issue |
| Update | Edit it — the digest reads live state, nothing to sync |
| Postpone | Change the `YYYY-MM-DD` in a follow-up's title |

A `[blocked-human]` item has no date by design — it keeps resurfacing until
closed, since there's nothing to postpone to.
