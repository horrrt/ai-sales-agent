#!/usr/bin/env bash
# Point every Fleet tools.json at the deployed MCP server (tools and interrupt_config keys).
#   scripts/set_mcp_url.sh https://sales-tools-xxxx.a.run.app/mcp
# Fleet stores tools by URL, so do this once, in one PR, before anyone builds on the imported agent.
set -euo pipefail
NEW="${1:?usage: set_mcp_url.sh <url ending in /mcp>}"
OLD="${2:-https://sales-tools.invalid/mcp}"
cd "$(dirname "$0")/../fleet"
files=$(grep -rl --include=tools.json "$OLD" . || true)
[ -z "$files" ] && { echo "no tools.json contains $OLD"; exit 1; }
for f in $files; do
  python3 - "$f" "$OLD" "$NEW" <<'PY'
import json, sys
path, old, new = sys.argv[1:]
text = open(path).read().replace(old, new)
json.loads(text)  # still valid JSON
open(path, "w").write(text)
PY
  echo "updated fleet/${f#./}"
done
