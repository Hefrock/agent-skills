#!/usr/bin/env bash
# install_hook.sh — install scan_diff.py as a target repo's git pre-commit hook.
#
# Usage: ./install_hook.sh /path/to/target/repo [--block-on low|medium|high|none]
#
# Default --block-on high: a confirmed high-severity finding (Luhn-valid credit
# card, a matched secret pattern, confirmed EXIF GPS) blocks the commit; medium/low
# findings stay advisory. Pass --block-on none for a purely advisory hook that never
# blocks (this tool's behavior before 2026-09-16). Never overwrites an existing hook
# without confirmation.

set -euo pipefail

BLOCK_ON="high"
TARGET_REPO_ARG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --block-on)
      BLOCK_ON="$2"
      shift 2
      ;;
    *)
      if [ -n "$TARGET_REPO_ARG" ]; then
        echo "Usage: $0 /path/to/target/repo [--block-on low|medium|high|none]" >&2
        exit 1
      fi
      TARGET_REPO_ARG="$1"
      shift
      ;;
  esac
done

if [ -z "$TARGET_REPO_ARG" ]; then
  echo "Usage: $0 /path/to/target/repo [--block-on low|medium|high|none]" >&2
  exit 1
fi

case "$BLOCK_ON" in
  low|medium|high|none) ;;
  *)
    echo "Error: --block-on must be one of low, medium, high, none (got '$BLOCK_ON')." >&2
    exit 1
    ;;
esac

TARGET_REPO="$(cd "$TARGET_REPO_ARG" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN_SCRIPT="$SCRIPT_DIR/scan_diff.py"

if [ ! -d "$TARGET_REPO/.git" ]; then
  echo "Error: $TARGET_REPO is not a git repository (no .git directory)." >&2
  exit 1
fi

HOOK_PATH="$TARGET_REPO/.git/hooks/pre-commit"

if [ -e "$HOOK_PATH" ]; then
  echo "A pre-commit hook already exists at $HOOK_PATH."
  read -r -p "Overwrite it? [y/N] " REPLY
  case "$REPLY" in
    [yY]*) ;;
    *) echo "Aborted — existing hook left untouched."; exit 1 ;;
  esac
fi

if [ "$BLOCK_ON" = "none" ]; then
  SCAN_INVOCATION="python3 \"$SCAN_SCRIPT\""
  MODE_COMMENT="# Installed by privacy-linter's install_hook.sh — advisory only (never blocks)."
else
  SCAN_INVOCATION="python3 \"$SCAN_SCRIPT\" --block-on $BLOCK_ON"
  MODE_COMMENT="# Installed by privacy-linter's install_hook.sh — blocks the commit on any"
  MODE_COMMENT="$MODE_COMMENT
# finding at or above '$BLOCK_ON' severity."
fi

cat > "$HOOK_PATH" <<EOF
#!/usr/bin/env bash
$MODE_COMMENT
# Edit this file to change the --block-on threshold (low/medium/high), or remove
# the flag entirely for a purely advisory hook. The hook's exit code is whatever
# scan_diff.py exits with — don't append an unconditional "exit 0" below, or
# --block-on becomes a no-op (that was this file's own bug before 2026-09-16).
# Add --log-dir /path/to/log/dir to track findings over time (see
# scan_log_history.py) — off by default, since it writes to disk on every run.
$SCAN_INVOCATION
EOF

chmod +x "$HOOK_PATH"
echo "Installed pre-commit hook at $HOOK_PATH"
echo "It calls: $SCAN_INVOCATION"
if [ "$BLOCK_ON" = "none" ]; then
  echo "Advisory only — this hook never blocks a commit."
else
  echo "Blocks commits on any '$BLOCK_ON'-or-above finding. Edit the hook directly to"
  echo "change the threshold, or remove --block-on for advisory-only."
fi
echo "Add --log-dir /path/to/log/dir to track findings over time (see scan_log_history.py)."
