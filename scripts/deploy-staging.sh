#!/usr/bin/env bash
set -euo pipefail

# Staging deployment for Telegram service (Cloud Run)
# Usage: ./scripts/deploy-staging.sh

REGION="australia-southeast1"
SERVICE="telegram-service-stg"

# Optional non-secret config (override via env if needed)
OPENAI_PROJECT="${OPENAI_PROJECT:-}"
EXECUTOR_URL="${EXECUTOR_URL:-https://executor-node-hwiba4dc7a-ts.a.run.app}"
EXECUTOR_TOKEN="${EXECUTOR_TOKEN:-}"
WALLET_ADDRESS="${WALLET_ADDRESS:-534sbVhF16EH8WoQumiawMy9gvbcLhGrXUroaSbyAFDv}"
ALLOWED_TELEGRAM_USER_IDS="${ALLOWED_TELEGRAM_USER_IDS:-6149503319}"
PLANNER_TIMEOUT_SEC="${PLANNER_TIMEOUT_SEC:-8}"

echo "Deploying STAGING service: $SERVICE ($REGION)"

# Resolve current URL if service exists
SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)' 2>/dev/null || true)"

BASE_ENV="LOG_LEVEL=DEBUG,PLANNER_IMPL=llm,EXECUTOR_URL=${EXECUTOR_URL},WALLET_ADDRESS=${WALLET_ADDRESS},NETWORK=mainnet,DEFAULT_SLIPPAGE_BPS=100,ALLOWED_TELEGRAM_USER_IDS=${ALLOWED_TELEGRAM_USER_IDS},ALLOWED_TOKENS=*,MIN_TOKEN_MCAP_USD=15000000,PLANNER_TIMEOUT_SEC=${PLANNER_TIMEOUT_SEC}"
if [[ -n "$EXECUTOR_TOKEN" ]]; then
  BASE_ENV+=" ,EXECUTOR_TOKEN=${EXECUTOR_TOKEN}"
fi
if [[ -n "$OPENAI_PROJECT" ]]; then
  BASE_ENV="OPENAI_PROJECT=${OPENAI_PROJECT},${BASE_ENV}"
fi
if [[ -n "$SERVICE_URL" ]]; then
  BASE_ENV="BASE_URL=${SERVICE_URL},${BASE_ENV}"
fi

# staging secrets (use separate keys)
SECRETS="OPENAI_API_KEY=${SECRET_OPENAI:-openai-api-key-stg}:latest,TELEGRAM_BOT_TOKEN=${SECRET_TELEGRAM:-telegram-bot-token-stg}:latest,WEBHOOK_SECRET=${SECRET_WEBHOOK:-telegram-webhook-secret-stg}:latest"

echo "Applying envs to $SERVICE (may create revision)…"
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "$BASE_ENV" \
  --set-secrets "$SECRETS" || true

echo "Building & deploying source to STAGING…"
gcloud run deploy "$SERVICE" \
  --region "$REGION" \
  --source . \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=2 \
  --ingress all \
  --cpu=1 \
  --memory=512Mi \
  --set-env-vars "$BASE_ENV" \
  --set-secrets "$SECRETS"

# Read the final URL and ensure BASE_URL is set accordingly
SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo "STAGING Service URL: $SERVICE_URL"

if [[ -n "$SERVICE_URL" ]]; then
  gcloud run services update "$SERVICE" --region "$REGION" \
    --update-env-vars "BASE_URL=${SERVICE_URL}" >/dev/null
fi

echo "Staging deploy complete. URL: $SERVICE_URL"

