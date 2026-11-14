#!/usr/bin/env bash
# Compare a ZIP exported from Fleet with fleet/ in the repo, file by file.
# Fleet rewrites config.json and tools.json when you connect Slack or change approvals,
# so check every export before committing it or handing it in.
# Usage: scripts/check_fleet_export.sh path/to/export.zip
set -euo pipefail
export_zip="$1"
tmp="$(mktemp -d)"
unzip -q "$export_zip" -d "$tmp"
cd "$(dirname "$0")/../fleet"
status=0
while IFS= read -r f; do
  if [ ! -f "$tmp/$f" ]; then echo "only in repo:   $f"; status=1
  elif ! cmp -s "$f" "$tmp/$f"; then echo "differs:        $f"; status=1; fi
done < <(find . -type f ! -name '.DS_Store' | sed 's|^\./||' | sort)
(cd "$tmp" && find . -type f | sed 's|^\./||' | sort) | while IFS= read -r f; do
  [ -f "$f" ] || echo "only in export: $f"
done
rm -rf "$tmp"
[ "$status" -eq 0 ] && echo "Export matches fleet/"
exit "$status"
