#!/usr/bin/env bash
# Usage: ./bin/setup-vault.sh /path/to/your/obsidian/vault
#
# Idempotent — safe to run on an existing vault. Uses mkdir -p and skips
# files that already exist. Never overwrites existing content.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
CONTEXT_TEMPLATE="$REPO_ROOT/skills/wiki-operator/assets/context.md"
TEMPLATES_SRC="$REPO_ROOT/templates"
CONSTITUTION_SRC="$REPO_ROOT/knowledge-os/constitution.md"

# ── Arg check ─────────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/your/obsidian/vault" >&2
  exit 1
fi

# Resolve to absolute path without requiring it to exist yet
if [[ "$1" = /* ]]; then
  VAULT="${1%/}"
else
  VAULT="$(pwd)/${1%/}"
fi

echo ""
echo "Setting up vault at: $VAULT"
echo ""

# ── Folder structure ──────────────────────────────────────────────────────────
echo "→ Folders"
mkdir -p "$VAULT/Knowledge/AI"
mkdir -p "$VAULT/Knowledge/Systems"
mkdir -p "$VAULT/Knowledge/Math"
mkdir -p "$VAULT/Knowledge/Engineering"
mkdir -p "$VAULT/Journal/Daily"
mkdir -p "$VAULT/Sources/raw"
mkdir -p "$VAULT/Sources/Papers"
mkdir -p "$VAULT/Sources/Books"
mkdir -p "$VAULT/Sources/Videos"
mkdir -p "$VAULT/Maps"
mkdir -p "$VAULT/Projects"
mkdir -p "$VAULT/System/templates"
echo "  OK  Knowledge/{AI,Systems,Math,Engineering}"
echo "  OK  Journal/Daily"
echo "  OK  Sources/{raw,Papers,Books,Videos}"
echo "  OK  Maps   Projects   System/templates"

# ── Hot cache ─────────────────────────────────────────────────────────────────
echo ""
echo "→ Maps/_context.md"
CONTEXT_DEST="$VAULT/Maps/_context.md"
if [[ -f "$CONTEXT_DEST" ]]; then
  echo "  SKIP  already exists"
else
  if [[ ! -f "$CONTEXT_TEMPLATE" ]]; then
    echo "  ERROR: template not found at $CONTEXT_TEMPLATE" >&2
    echo "         Run this script from the agent-skills repo directory." >&2
    exit 1
  fi
  TODAY=$(date +%Y-%m-%d)
  sed "s/YYYY-MM-DD/$TODAY/g" "$CONTEXT_TEMPLATE" > "$CONTEXT_DEST"
  echo "  OK  created (dated $TODAY)"
fi

# ── System/ — constitution + templates ───────────────────────────────────────
echo ""
echo "→ System/constitution.md"
CONSTITUTION_DEST="$VAULT/System/constitution.md"
if [[ -f "$CONSTITUTION_DEST" ]]; then
  echo "  SKIP  already exists"
else
  if [[ ! -f "$CONSTITUTION_SRC" ]]; then
    echo "  ERROR: constitution not found at $CONSTITUTION_SRC" >&2
    exit 1
  fi
  cp "$CONSTITUTION_SRC" "$CONSTITUTION_DEST"
  echo "  OK  created"
fi

echo ""
echo "→ System/templates/"
for tmpl in concept journal source map; do
  SRC="$TEMPLATES_SRC/$tmpl.md"
  DEST="$VAULT/System/templates/$tmpl.md"
  if [[ -f "$DEST" ]]; then
    echo "  SKIP  $tmpl.md already exists"
  elif [[ ! -f "$SRC" ]]; then
    echo "  WARN  template not found: $SRC" >&2
  else
    cp "$SRC" "$DEST"
    echo "  OK  $tmpl.md"
  fi
done

# ── Vault README ──────────────────────────────────────────────────────────────
echo ""
echo "→ VAULT_README.md"
VAULT_README="$VAULT/VAULT_README.md"
if [[ -f "$VAULT_README" ]]; then
  echo "  SKIP  already exists"
else
  cat > "$VAULT_README" << 'EOF'
# Wiki Vault

This vault is structured for a Karpathy-style personal knowledge system operated by Claude via the `obsidian-vault` MCP server and the wiki-operator, wiki-synthesizer, and wiki-librarian skills.

## Folder structure

| Folder | Purpose |
|---|---|
| `Knowledge/` | Concept pages — one durable idea per file |
| `Knowledge/AI/` | AI and machine learning concepts |
| `Knowledge/Systems/` | Systems design and distributed systems |
| `Knowledge/Math/` | Mathematics and statistics |
| `Knowledge/Engineering/` | Software engineering and architecture |
| `Journal/Daily/` | Daily journal entries (`YYYY-MM-DD.md`) |
| `Sources/raw/` | Drop raw material here — papers, articles, note dumps. wiki-synthesizer compiles and removes these. |
| `Sources/Papers/` | Compiled paper pages |
| `Sources/Books/` | Compiled book pages |
| `Sources/Videos/` | Compiled video/talk pages |
| `Maps/` | Index and overview pages |
| `Maps/_context.md` | Hot cache — Claude reads this first each session to orient quickly |
| `Projects/` | Active project notes |
| `.trash/` | Deleted notes (recoverable). Created automatically by delete_note. |

## Note schema

Every note carries this frontmatter:

```yaml
type: concept | journal | source | map | project
status: mature | draft | stale
confidence: high | medium | low
updated: YYYY-MM-DD
```

- `status: mature` — stable, clearly written, linked to at least two other pages
- `status: draft` — new or incomplete (default for anything just created)
- `status: stale` — not updated recently, few or no incoming links

## Skills

| Skill | Use for |
|---|---|
| **wiki-operator** | On-demand vault operations: add notes, update concepts, connect ideas, review quality, generate study prompts |
| **wiki-synthesizer** | Batch pipeline: promote flagged journal ideas → concept pages; compile `Sources/raw/` → source pages |
| **wiki-librarian** | Structural audits: broken links, orphan pages, stale notes, near-duplicates, contradictions |

All skills require the `obsidian-vault` MCP server connected. See [agent-skills](https://github.com/Hefrock/agent-skills) for setup.

## Workflow

1. Capture ideas in `Journal/Daily/YYYY-MM-DD.md` under `## Ideas to promote`
2. Drop raw sources (papers, articles) in `Sources/raw/`
3. After a learning session, run: `synthesize my notes` — wiki-synthesizer promotes ideas and compiles sources
4. Weekly: `audit my wiki` — wiki-librarian checks structural health
EOF
  echo "  OK  created"
fi

# ── MCP config snippet ────────────────────────────────────────────────────────
NODE_BIN=$(which node 2>/dev/null || echo "/path/to/node")

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Add this to ~/.claude.json under \"mcpServers\":"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
cat << MCP_SNIPPET
    "obsidian-vault": {
      "type": "stdio",
      "command": "$NODE_BIN",
      "args": ["$REPO_ROOT/mcp/obsidian-vault/dist/index.js"],
      "env": {
        "OBSIDIAN_VAULT_PATH": "$VAULT"
      }
    }
MCP_SNIPPET
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done. Restart Claude Code and run /mcp to verify (expect 10 tools)."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
