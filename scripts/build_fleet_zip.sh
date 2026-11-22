#!/usr/bin/env bash
# Zip fleet/ as it is in the repo (placeholder tool URL), e.g. to compare with an export.
# For an upload-ready zip with the deployed server's URL: python3 scripts/package_fleet.py --mcp-url https://.../mcp
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/package_fleet.py --sandbox "$@" | grep -v '^warning' || exit 1
