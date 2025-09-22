#!/usr/bin/env bash
set -euo pipefail

# --- Where to deploy (change if you use another region or name)
: "${REGION:=australia-southeast1}"
: "${EXEC_SERVICE:=executor-node}"   # Cloud Run service name for the executor

# --- Where the executor source lives (this repo layout has /executor-node)
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/executor-node"

# --- REQUIRED runtime config for the executor
# Set this in your terminal BEFORE running the script, e.g.:
#   export RPC_URL="https://YOUR_SOLANA_RPC"
: "${RPC_URL:?Set RPC_URL to your Solana RPC HTTPS URL before running (export RPC_URL=...)}"

# --- Optional
: "${PRIORITY_FEE_MICRO_LAMPORTS:=0}"

echo "Deploying executor:"
echo "  REGION       = $REGION"
echo "  EXEC_SERVICE = $EXEC_SERVICE"
echo "  SRC_DIR      = $SRC_DIR"

# Build & deploy the executor
# - RPC_URL is a plain env var (must be exported before running)
# - AGENT_SECRET_B58 comes from Secret Manager secret "solana-wallet-key"
gcloud run deploy "$EXEC_SERVICE" \
  --region "$REGION" \
  --source "$SRC_DIR" \
  --allow-unauthenticated \
  --set-env-vars "RPC_URL=$RPC_URL,PRIORITY_FEE_MICRO_LAMPORTS=$PRIORITY_FEE_MICRO_LAMPORTS" \
  --set-secrets "AGENT_SECRET_B58=solana-wallet-key:latest"

# Get the public URL of the executor and sanity-check /health
EXECUTOR_URL="$(gcloud run services describe "$EXEC_SERVICE" --region "$REGION" --format='value(status.url)')"
echo ""
echo "Executor deployed at: $EXECUTOR_URL"
echo ""
echo "Health check:"
curl -s "$EXECUTOR_URL/health" | python -m json.tool || true

echo ""
echo "Next:"
echo "1) Put this URL into your Telegram deploy script as EXECUTOR_URL:"
echo "     EXECUTOR_URL=\"$EXECUTOR_URL\""
echo "2) Re-deploy Telegram with ./deploy_telegram.sh"