#!/usr/bin/env bash
# cron_check.sh -- unattended wrapper for check_vault_privacy.py, for an OS-level
# scheduler (systemd user timer, launchd, cron) rather than an interactive Claude
# session. check_vault_privacy.py itself needs no MCP or Claude -- it's a plain
# directory scan -- so this can run fully detached from any session.
#
# This is a detector, not a fixer: it logs and (best-effort) notifies, but never
# proposes or applies a fix -- that's /privacy-audit's step 3, which genuinely
# needs a live Claude session with the obsidian-vault MCP server connected to
# hand off to wiki-operator. Run /privacy-audit interactively once notified.
#
# Usage: cron_check.sh /path/to/vault [/path/to/log/file]
#
# The log line is JSON-per-line (timestamp, blocked, and check_vault_privacy.py's
# own --json output verbatim). check_vault_privacy.py's findings never contain the
# actual matched PII/secret text (only a label like "AWS access key: aws_access_key"
# and a file:line location) -- confirmed in scan_diff.py's Finding construction --
# so this log can't become a second copy of whatever triggered a finding.

set -uo pipefail

VAULT_PATH="${1:?Usage: $0 /path/to/vault [/path/to/log/file]}"
LOG_FILE="${2:-$HOME/.wiki-privacy-audit.log}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_SCRIPT="$SCRIPT_DIR/check_vault_privacy.py"

if OUTPUT="$(python3 "$CHECK_SCRIPT" "$VAULT_PATH" --block-on high --json)"; then
  BLOCKED=false
else
  BLOCKED=true
fi

TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# check_vault_privacy.py --json pretty-prints (indent=2, multi-line) -- compact it
# so the log stays one JSON object per line, not one object spread across many.
COMPACT_OUTPUT="$(printf '%s' "$OUTPUT" | python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin)))')"
printf '{"timestamp": "%s", "blocked": %s, "result": %s}\n' "$TIMESTAMP" "$BLOCKED" "$COMPACT_OUTPUT" >> "$LOG_FILE"

if [ "$BLOCKED" = true ]; then
  MESSAGE="wiki-privacy-audit: high-severity finding(s) in vault -- run /privacy-audit"
  # Best-effort notification only -- never the finding's own text, just "go look."
  # Under a plain cron job (no DISPLAY/DBUS session), neither of these will fire;
  # the log file is the reliable signal there. Under a systemd --user timer or
  # launchd agent running in a real login session, one of these should work.
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "Privacy Audit" "$MESSAGE" || true
  elif command -v osascript >/dev/null 2>&1; then
    osascript -e "display notification \"$MESSAGE\" with title \"Privacy Audit\"" || true
  else
    echo "$MESSAGE" >&2
  fi
fi

# Always exit 0: the scheduler's own success/failure semantics are "did this run,"
# not "did it find something" -- that distinction lives in the log and the
# notification, not in whether systemd/cron reports this run as failed.
exit 0
