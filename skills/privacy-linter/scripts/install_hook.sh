#!/usr/bin/env bash
# install_hook.sh — install scan_diff.py as a target repo's git pre-commit hook.
#
# Usage: ./install_hook.sh /path/to/target/repo [--block-on low|medium|high|none] [--log-dir /abs/path] [--runner "CMD"]
#
# --runner "CMD" (for machines where python3 is not on PATH at commit time, e.g. NixOS
# or a version-manager shell): the hook runs the whole scan command as ONE quoted
# argument appended to CMD, e.g. --runner "nix-shell -p python3 git --run" installs a
# hook that calls: nix-shell -p python3 git --run 'python3 ".../scan_diff.py" ...'.
# Without --runner the installer requires python3 on PATH now and fails with a hint
# if it is missing, instead of writing a hook that exits 127 and blocks every commit.
# CMD is written into the hook as-is: only pass a runner you trust.
#
# --log-dir (opt-in, off by default) makes the installed hook pass --log-dir to
# scan_diff.py, so every commit-time scan appends a findings-only record (never the
# scanned content) for scan_log_history.py's trend bridge. The path must be absolute
# (a leading ~/ is expanded here, once, at install time).
#
# Default --block-on high: a confirmed high-severity finding (Luhn-valid credit
# card, a matched secret pattern, confirmed EXIF GPS) blocks the commit; medium/low
# findings stay advisory. Pass --block-on none for a purely advisory hook that never
# blocks (this tool's behavior before 2026-09-16). Never overwrites an existing hook
# without confirmation.

set -euo pipefail

BLOCK_ON="high"
LOG_DIR=""
RUNNER=""
TARGET_REPO_ARG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --block-on)
      BLOCK_ON="${2:-}"
      [ $# -ge 2 ] && shift 2 || shift
      ;;
    --log-dir)
      LOG_DIR="${2:-}"
      if [ -z "$LOG_DIR" ]; then
        echo "Error: --log-dir needs a directory path." >&2
        exit 1
      fi
      shift 2
      ;;
    --runner)
      RUNNER="${2:-}"
      if [ -z "$RUNNER" ]; then
        echo "Error: --runner needs a command prefix, e.g. --runner \"nix-shell -p python3 git --run\"." >&2
        exit 1
      fi
      shift 2
      ;;
    *)
      if [ -n "$TARGET_REPO_ARG" ]; then
        echo "Usage: $0 /path/to/target/repo [--block-on low|medium|high|none] [--log-dir /abs/path] [--runner \"CMD\"]" >&2
        exit 1
      fi
      TARGET_REPO_ARG="$1"
      shift
      ;;
  esac
done

if [ -z "$TARGET_REPO_ARG" ]; then
  echo "Usage: $0 /path/to/target/repo [--block-on low|medium|high|none] [--log-dir /abs/path] [--runner \"CMD\"]" >&2
  exit 1
fi

case "$BLOCK_ON" in
  low|medium|high|none) ;;
  *)
    echo "Error: --block-on must be one of low, medium, high, none (got '$BLOCK_ON')." >&2
    exit 1
    ;;
esac

# Fail now, loudly, rather than install a hook that exits 127 (python3: command not
# found) on every commit and so blocks all of them.
if [ -z "$RUNNER" ] && ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is not on PATH, so the installed hook could not run." >&2
  echo "Pass --runner with a command that provides it, e.g." >&2
  echo "  --runner \"nix-shell -p python3 git --run\"" >&2
  exit 1
fi

# Expand a leading ~/ once, now: the generated hook quotes the path, and a quoted ~
# is never expanded by bash at commit time. A relative path would resolve against
# whatever directory git runs the hook from, so require an absolute one.
if [ -n "$LOG_DIR" ]; then
  case "$LOG_DIR" in
    "~") LOG_DIR="$HOME" ;;
    "~/"*) LOG_DIR="$HOME/${LOG_DIR#"~/"}" ;;
  esac
  case "$LOG_DIR" in
    /*) ;;
    *)
      echo "Error: --log-dir must be an absolute path (got '$LOG_DIR')." >&2
      exit 1
      ;;
  esac
fi

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

LOG_ARGS=""
if [ -n "$LOG_DIR" ]; then
  # %q makes the path safe to embed in the generated bash file (spaces, quotes).
  LOG_ARGS=" --log-dir $(printf '%q' "$LOG_DIR")"
fi

if [ "$BLOCK_ON" = "none" ]; then
  SCAN_INVOCATION="python3 \"$SCAN_SCRIPT\"$LOG_ARGS"
  MODE_COMMENT="# Installed by privacy-linter's install_hook.sh — advisory only (never blocks)."
else
  SCAN_INVOCATION="python3 \"$SCAN_SCRIPT\" --block-on $BLOCK_ON$LOG_ARGS"
  MODE_COMMENT="# Installed by privacy-linter's install_hook.sh — blocks the commit on any"
  MODE_COMMENT="$MODE_COMMENT
# finding at or above '$BLOCK_ON' severity."
fi

if [ -n "$RUNNER" ]; then
  # The whole scan command goes to the runner as ONE argument (%q-quoted).
  SCAN_INVOCATION="$RUNNER $(printf '%q' "$SCAN_INVOCATION")"
fi

if [ -n "$LOG_DIR" ]; then
  LOG_COMMENT="# Logging is ON (--log-dir): findings only, never the scanned content. See
# scan_log_history.py for the trend bridge."
else
  LOG_COMMENT="# Add --log-dir /abs/path/to/log/dir to track findings over time (see
# scan_log_history.py) — off by default, since it writes to disk on every run."
fi

cat > "$HOOK_PATH" <<EOF
#!/usr/bin/env bash
$MODE_COMMENT
# Edit this file to change the --block-on threshold (low/medium/high), or remove
# the flag entirely for a purely advisory hook. The hook's exit code is whatever
# scan_diff.py exits with — don't append an unconditional "exit 0" below, or
# --block-on becomes a no-op (that was this file's own bug before 2026-09-16).
$LOG_COMMENT
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
if [ -z "$LOG_DIR" ]; then
  echo "Re-run with --log-dir /abs/path/to/log/dir to track findings over time (see scan_log_history.py)."
fi
