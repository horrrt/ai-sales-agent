#!/usr/bin/env bash
# Deploy the sandbox MCP tool server to Google Cloud Run and print its /mcp URL.
#   scripts/deploy_mcp.sh [gcp-project] [region]
# Cloud Run builds the Dockerfile at the repo root. One instance max: the store is in memory,
# so a second instance would see different leads. State resets when the instance restarts.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${1:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${2:-europe-north1}"
SERVICE=sales-tools
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project "$PROJECT"
gcloud run deploy "$SERVICE" --source . --project "$PROJECT" --region "$REGION" \
  --allow-unauthenticated --min-instances 0 --max-instances 1 --memory 512Mi --quiet
URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')/mcp"
echo "MCP server: $URL"
echo "Next: scripts/set_mcp_url.sh $URL"
