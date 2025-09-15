#!/usr/bin/env bash
set -euo pipefail

REGION=${REGION:-australia-southeast1}
SUFFIX=${SUFFIX:-}
SERVICE="executor-node${SUFFIX}"

gcloud run deploy "$SERVICE" \
  --source ./executor-node \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars RPC_URL="${RPC_URL?}",AGENT_SECRET_B58="${AGENT_SECRET_B58?}",ALLOWED_MINTS="${ALLOWED_MINTS:-SOL,USDC,JITOSOL,BONK}",PRICE_IMPACT_MAX_PCT="${PRICE_IMPACT_MAX_PCT:-2}"

