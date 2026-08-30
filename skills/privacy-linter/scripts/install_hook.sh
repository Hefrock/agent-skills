#!/usr/bin/env bash
# install_hook.sh — install scan_diff.py as a target repo's git pre-commit hook.
#
# Usage: ./install_hook.sh /path/to/target/repo
#
# Writes .git/hooks/pre-commit to call this skill's scan_diff.py with no gate
# (advisory-only, matching the linter's default). Never overwrites an existing
# hook without confirmation.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 /path/to/target/repo" >&2
  exit 1
fi

TARGET_REPO="$(cd "$1" && pwd)"
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

cat > "$HOOK_PATH" <<EOF
#!/usr/bin/env bash
# Installed by privacy-linter's install_hook.sh — advisory only (always exits 0).
# Edit this file to add --block-on high (or medium/low) to make it blocking.
python3 "$SCAN_SCRIPT"
exit 0
EOF

chmod +x "$HOOK_PATH"
echo "Installed advisory pre-commit hook at $HOOK_PATH"
echo "It calls: python3 $SCAN_SCRIPT"
echo "Edit the hook directly to add --block-on high if you want it to block commits."
