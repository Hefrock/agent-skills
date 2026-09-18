#!/usr/bin/env python3
"""
find_closing_references.py — the deterministic half of issue-reconciler.

Cross-references GitHub's own closing-keyword syntax ("Closes #6", "Fixes #12",
"Resolves #3") in merged PR bodies against the current list of still-open issues.
A match means a merged PR explicitly said it would close an issue, and that issue
is nonetheless still open -- GitHub's auto-close didn't fire (the keyword was added
after merge, a squash-merge edge case, a typo in the issue number) or the PR was
merged without the reference ever being noticed. Either way, it's a near-certain
oversight, not a judgment call -- which is exactly why this is a script and not
part of the skill's conversational methodology: no reasoning is needed to find it,
only pattern-matching over data the caller already has.

This does NOT call the GitHub API itself. It reads two JSON files the caller
already produced via whatever GitHub tooling the host provides (gh CLI, an MCP
server) -- consistent with this repo's "consume JSON someone else produced,
don't reimplement the fetch" convention (see privacy-linter's scan_log_history.py
reading scan_diff.py's --log-dir output rather than re-scanning). Reusing this
script across hosts means it never needs its own auth/rate-limit handling.

Usage:
    gh issue list --state open --json number,title,body,createdAt,labels > issues.json
    gh pr list --state merged --json number,title,body,mergedAt > merged_prs.json
    python find_closing_references.py --issues issues.json --prs merged_prs.json
    python find_closing_references.py --issues issues.json --prs merged_prs.json --json

Only catches the same-repo "#N" form GitHub recognizes for auto-close. Does NOT
handle the cross-repo "owner/repo#N" linking syntax -- a documented scope limit,
not a silent gap: cross-repo issue tracking is rare enough for a personal
skills repo that it wasn't worth the added regex complexity for a first version.
"""

import argparse
import json
import re
import sys

# The exact keyword set GitHub recognizes for issue auto-closing (close/closes/closed,
# fix/fixes/fixed, resolve/resolves/resolved), case-insensitive, followed by a same-repo
# "#N" reference. See https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue
CLOSING_KEYWORD_RE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b"
)


def find_referenced_issue_numbers(pr_body: str) -> list[int]:
    """Every issue number a PR body references via a GitHub closing keyword,
    in the order they appear. A body with no closing keyword returns []."""
    if not pr_body:
        return []
    return [int(n) for n in CLOSING_KEYWORD_RE.findall(pr_body)]


def find_stale_closing_references(issues: list[dict], merged_prs: list[dict]) -> list[dict]:
    """For every merged PR that references a still-open issue via a closing
    keyword, one finding: {issue_number, issue_title, pr_number, pr_title,
    pr_merged_at}. An issue referenced by more than one merged PR gets one
    finding per PR -- each is independent evidence, not deduplicated, since a
    reviewer deciding whether to close the issue benefits from seeing all of it.
    Pure function, no I/O -- fully unit-testable without real issue/PR data."""
    open_by_number = {issue["number"]: issue for issue in issues}
    findings = []
    for pr in merged_prs:
        for issue_number in find_referenced_issue_numbers(pr.get("body", "")):
            issue = open_by_number.get(issue_number)
            if issue is None:
                continue  # not open (already closed, or never existed) -- not a finding
            findings.append({
                "issue_number": issue_number,
                "issue_title": issue.get("title", ""),
                "pr_number": pr.get("number"),
                "pr_title": pr.get("title", ""),
                "pr_merged_at": pr.get("mergedAt") or pr.get("merged_at"),
            })
    findings.sort(key=lambda f: (f["issue_number"], f["pr_number"]))
    return findings


def print_report(findings: list[dict], json_out: bool) -> None:
    if json_out:
        print(json.dumps(findings, indent=2))
        return

    if not findings:
        print("issue-reconciler: no stale closing references found.")
        return

    print(f"issue-reconciler: {len(findings)} stale closing reference(s) found.")
    for f in findings:
        print(
            f'  Issue #{f["issue_number"]} ("{f["issue_title"]}") is still open, but '
            f'PR #{f["pr_number"]} ("{f["pr_title"]}", merged {f["pr_merged_at"]}) '
            f"said it would close it."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--issues", required=True, metavar="FILE",
                         help="JSON file of open issues (gh issue list --json number,title,body,...).")
    parser.add_argument("--prs", required=True, metavar="FILE",
                         help="JSON file of merged PRs (gh pr list --state merged --json number,title,body,mergedAt).")
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON instead of a report.")
    args = parser.parse_args()

    try:
        with open(args.issues, encoding="utf-8") as f:
            issues = json.load(f)
        with open(args.prs, encoding="utf-8") as f:
            merged_prs = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"issue-reconciler: could not read input: {e}", file=sys.stderr)
        return 2

    findings = find_stale_closing_references(issues, merged_prs)
    print_report(findings, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
