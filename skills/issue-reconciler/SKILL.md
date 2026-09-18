---
name: issue-reconciler
description: Finds GitHub issues that are actually already resolved but were never closed — either because a merged PR said "Closes #N" and the auto-close didn't fire, or because the thing the issue asked for now exists in the repo (a skill got built, a bug got fixed) without anyone ever linking the PR that did it. Two passes, not one: a deterministic scan (scripts/find_closing_references.py) for explicit-but-failed closing keywords, then a judgment pass over what's left for thematic matches the mechanical scan structurally can't catch. Never closes an issue without presenting the evidence and getting confirmation first. Deliberately excludes issues that self-identify as intentionally open (a "[blocked-human]" title prefix, or a "Status: deferred/blocked" section) — those are tracked on purpose, not stale. Use when the user wants to clean up a stale or growing issue backlog, asks "which of my issues are actually done," "check if any issues are already resolved," "close issues that got fixed," or wants their tracker kept honest without manually cross-checking every issue against every PR. Triggers on "check my issues," "reconcile issues," "clean up the issue tracker," "issue-reconciler," and the script find_closing_references.py.
---

# Issue Reconciler

An open issue is a claim: "this still needs doing." Like any claim, it can go stale —
the thing gets done, but nobody tells the tracker. This skill finds the gap between
what your issues say is still open and what your repo's actual state and PR history
say really happened.

## Why this is two passes, not one

**Pass 1 (mechanical, `scripts/find_closing_references.py`)** catches the unambiguous
case: a merged PR's body used GitHub's own closing-keyword syntax ("Closes #6", "Fixes
#12", "Resolves #3") and the issue is nonetheless still open. That's not a judgment
call — GitHub's auto-close didn't fire (the keyword was added after merge, a squash
edge case) or nobody noticed. Pure pattern-matching, zero reasoning needed, which is
exactly why it's a script and not part of the conversational methodology.

**Pass 2 (judgment, conversational)** catches everything Pass 1 structurally can't:
an issue whose ask was fulfilled without anyone ever using a closing keyword at all.
This is the more common case in practice, not the edge case — confirmed empirically
against this repo's own history: running Pass 1 against every open issue and the last
100 merged PRs here found **zero** matches, because not one of those PRs ever used
"Closes #N" syntax. And yet issue #6 ("Build wiki-teacher skill," filed months ago) is
still open despite `skills/wiki-teacher/` existing, fully built, right now. Pass 1
alone would never find that — it has no PR to point to, because the PR that built it
never said it was closing anything. Only a judgment pass, checking the issue's actual
ask against actual repo state, catches it.

## How this works

```bash
gh issue list --state open --json number,title,body,createdAt,labels > issues.json
gh pr list --state merged --json number,title,body,mergedAt > merged_prs.json
python scripts/find_closing_references.py --issues issues.json --prs merged_prs.json
```

(No GitHub API client lives in this skill — it consumes JSON that whatever GitHub
tooling the host already has produces, `gh` CLI or an MCP server's issue/PR list
calls. Same "consume data, don't reimplement the fetch" convention
`privacy-linter`'s `scan_log_history.py` already uses. One real quirk hit building
this: at least one GitHub MCP tool's PR-list response left its own `merged` boolean
field false on every row while `merged_at` was populated correctly — the script reads
`mergedAt`/`merged_at` directly rather than trusting a `merged` flag, so it isn't
fooled by a host tool with this same quirk.)

1. **Run Pass 1.** Deterministic, cheap, always run first — it eliminates the
   obvious cases before any conversational effort goes into the harder ones.
2. **For every open issue Pass 1 didn't already flag, check two things:**
   - **Is this issue meant to stay open?** Skip it from staleness consideration
     entirely if the title starts with `[blocked-human]`, or the body has a
     `## Status` section saying deferred/blocked/not being worked on. These are
     tracked on purpose — age alone is not evidence of staleness for them.
   - **Does its actual ask match current reality?** For an issue that names a
     specific skill, file, or feature, check whether that thing exists now
     (`skills/<name>/`, a described flag or function in a named script). For an
     issue describing a bug or gap, check whether recent merged PRs — even ones
     that never used a closing keyword — describe fixing the same thing.
3. **Report findings in two tiers**, never as one flat list:
   - **High confidence** — a Pass 1 finding (explicit closing keyword, issue still
     open), or a named artifact that now unambiguously exists exactly as the issue
     described it.
   - **Worth a look** — a thematic match to a merged PR without an explicit link,
     where a human should confirm the PR actually addressed the issue's real ask
     before closing it.
4. **Propose, never auto-close.** Present the evidence (which PR, which file, which
   line) and wait for confirmation — closing an issue is a visible, public action,
   the same "propose redaction, never auto-apply" discipline `privacy-linter` and
   `wiki-privacy-audit` already hold for their own visible actions. On confirmation,
   close with a reason (`completed`, not the bare default) and a comment naming the
   evidence, so the closed issue still carries an audit trail for anyone who finds it
   later.
5. **Going forward, not just retroactively:** suggest using `Closes #N`/`Fixes #N` in
   new PR descriptions when they really do resolve a tracked issue — free, native,
   and it's what would have caught issue #6 immediately instead of months later.

## What's NOT built here

- **Cross-repo issue linking.** `find_closing_references.py` only recognizes the
  same-repo `#N` form, not `owner/repo#N`. Rare enough for a personal skills repo
  that it wasn't worth the regex complexity yet — a documented scope limit, not a
  silent gap.
- **Any form of auto-closing.** Every close is proposed with evidence and confirmed
  by a human, every time, no exceptions, no `--yes` flag.
- **Fuzzy/semantic matching as code.** Pass 2's "does this PR's actual change address
  this issue's actual ask" judgment is deliberately conversational, not a string-
  similarity script — the same reasoning `repo-pincer` uses for why its own
  reconciliation is a documented methodology, not a classifier.

## Pairing

- **repo-pincer** — the closest sibling: both reconcile a claim against reality and
  classify the discrepancy. The boundary: `repo-pincer` audits whether *documentation*
  matches *code*, run deliberately and occasionally for onboarding or vendor
  evaluation. This skill audits whether the *issue tracker* matches *reality*, meant
  to run often and stay lightweight — routine hygiene, not a deep audit. Different
  cadence, different artifact pair, kept separate rather than folded into one skill
  for the same reason MECE and pre-mortem stayed separate from `agent-eval`.

## Files

| Path | What it is |
|---|---|
| `scripts/find_closing_references.py` | Pass 1 — deterministic closing-keyword cross-reference |
| `scripts/test_find_closing_references.py` | Unit + CLI test suite (stdlib unittest) |
