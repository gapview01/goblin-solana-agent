#!/usr/bin/env bash
set -euo pipefail

# --- Where to deploy (change if you use another region or name)
: "${REGION:=australia-southeast1}"
: "${EXEC_SERVICE:=executor-node}"   # Cloud Run service name for the executor

# --- Where the executor source lives (this repo layout has /executor-node)
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/executor-node"

# --- REQUIRED secrets (Secret Manager)
: "${RPC_SECRET:=solana-rpc-url}"
: "${WALLET_SECRET:=solana-wallet-key}"

# --- Optional
: "${PRIORITY_FEE_MICRO_LAMPORTS:=0}"

echo "Deploying executor:"
echo "  REGION        = $REGION"
echo "  EXEC_SERVICE  = $EXEC_SERVICE"
echo "  SRC_DIR       = $SRC_DIR"
echo "  RPC_SECRET    = $RPC_SECRET"
echo "  WALLET_SECRET = $WALLET_SECRET"

# Build & deploy (no-traffic), wire secrets, keep 1 warm instance
TAG="canary-$(date +%s)"

gcloud run deploy "$EXEC_SERVICE" \
  --region "$REGION" \
  --source "$SRC_DIR" \
  --allow-unauthenticated \
  --no-traffic \
  --tag "$TAG" \
  --min-instances 1 \
  --set-secrets "RPC_URL=${RPC_SECRET}:latest,AGENT_SECRET_B58=${WALLET_SECRET}:latest" \
  --set-env-vars "PRIORITY_FEE_MICRO_LAMPORTS=$PRIORITY_FEE_MICRO_LAMPORTS"

# Health gate before shifting traffic
URL=$(gcloud run services describe "$EXEC_SERVICE" --region "$REGION" --format='value(status.url)')
echo "Health check: $URL/health"
for i in {1..10}; do
  if curl -fsS "$URL/health" >/dev/null; then
    echo "Health OK"; break
  fi
  echo "Waiting for healthy revision... ($i/10)"; sleep 3
done

# Shift traffic to latest
gcloud run services update-traffic "$EXEC_SERVICE" --region "$REGION" --to-latest

# Show health JSON
curl -s "$URL/health" | jq . || true

echo "Executor deployed and healthy at: $URL"