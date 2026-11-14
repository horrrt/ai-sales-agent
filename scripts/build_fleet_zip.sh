#!/usr/bin/env bash
# Zip fleet/ in Fleet's export layout, for Fleet's sidebar: + > Import.
# Import creates the agent exactly as written. It skips schedules, so add those by hand.
set -euo pipefail
cd "$(dirname "$0")/../fleet"
mkdir -p ../dist
rm -f ../dist/sales-assistant-fleet.zip
zip -r -X ../dist/sales-assistant-fleet.zip AGENTS.md config.json tools.json subagents skills -x '*.DS_Store'
echo "Built dist/sales-assistant-fleet.zip"
