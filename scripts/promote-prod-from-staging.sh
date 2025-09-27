#!/usr/bin/env bash
set -euo pipefail

# Promote the exact container image running in STAGING to PRODUCTION (Cloud Run)
# Usage: ./scripts/promote-prod-from-staging.sh

REGION="${REGION:-australia-southeast1}"
STG_SERVICE="${STG_SERVICE:-telegram-service-stg}"
PROD_SERVICE="${PROD_SERVICE:-telegram-service}"

# Optional non-secret config overrides
OPENAI_PROJECT="${OPENAI_PROJECT:-}"
EXECUTOR_URL="${EXECUTOR_URL:-https://executor-node-hwiba4dc7a-ts.a.run.app}"
EXECUTOR_TOKEN="${EXECUTOR_TOKEN:-}"
WALLET_ADDRESS="${WALLET_ADDRESS:-534sbVhF16EH8WoQumiawMy9gvbcLhGrXUroaSbyAFDv}"
ALLOWED_TELEGRAM_USER_IDS="${ALLOWED_TELEGRAM_USER_IDS:-}" # generally empty in prod (multi-user)
PLANNER_TIMEOUT_SEC="${PLANNER_TIMEOUT_SEC:-8}"

# Prod secret names (override with env if different)
SECRET_OPENAI="${SECRET_OPENAI:-openai-api-key-prod}"
SECRET_TELEGRAM="${SECRET_TELEGRAM:-telegram-bot-token-prod}"
SECRET_WEBHOOK="${SECRET_WEBHOOK:-telegram-webhook-secret-prod}"

echo "Resolving latest STAGING revision…"
STG_REV=$(gcloud run services describe "$STG_SERVICE" --region "$REGION" --format='value(status.latestReadyRevisionName)')
if [[ -z "$STG_REV" ]]; then
  echo "Could not find staging revision for $STG_SERVICE"; exit 1
fi
IMAGE=$(gcloud run revisions describe "$STG_REV" --region "$REGION" --format='value(spec.containers[0].image)')
if [[ -z "$IMAGE" ]]; then
  echo "Could not resolve container image from revision $STG_REV"; exit 1
fi
echo "Promoting image: $IMAGE"

BASE_ENV="LOG_LEVEL=INFO,PLANNER_IMPL=llm,EXECUTOR_URL=${EXECUTOR_URL},WALLET_ADDRESS=${WALLET_ADDRESS},NETWORK=mainnet,DEFAULT_SLIPPAGE_BPS=100,ALLOWED_TOKENS=*,MIN_TOKEN_MCAP_USD=15000000,PLANNER_TIMEOUT_SEC=${PLANNER_TIMEOUT_SEC}"
if [[ -n "$ALLOWED_TELEGRAM_USER_IDS" ]]; then
  BASE_ENV=",ALLOWED_TELEGRAM_USER_IDS=${ALLOWED_TELEGRAM_USER_IDS},${BASE_ENV}"
fi
if [[ -n "$EXECUTOR_TOKEN" ]]; then
  BASE_ENV=",EXECUTOR_TOKEN=${EXECUTOR_TOKEN},${BASE_ENV}"
fi
if [[ -n "$OPENAI_PROJECT" ]]; then
  BASE_ENV="OPENAI_PROJECT=${OPENAI_PROJECT},${BASE_ENV}"
fi

SECRETS="OPENAI_API_KEY=${SECRET_OPENAI}:latest,TELEGRAM_BOT_TOKEN=${SECRET_TELEGRAM}:latest,WEBHOOK_SECRET=${SECRET_WEBHOOK}:latest"

echo "Deploying image to PRODUCTION service: $PROD_SERVICE…"
gcloud run deploy "$PROD_SERVICE" \
  --region "$REGION" \
  --image "$IMAGE" \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=10 \
  --ingress all \
  --cpu=1 \
  --memory=512Mi \
  --set-env-vars "$BASE_ENV" \
  --set-secrets "$SECRETS"

PROD_URL=$(gcloud run services describe "$PROD_SERVICE" --region "$REGION" --format='value(status.url)')
echo "Production service URL: $PROD_URL"

if [[ -n "$PROD_URL" ]]; then
  gcloud run services update "$PROD_SERVICE" --region "$REGION" \
    --update-env-vars "BASE_URL=${PROD_URL}" >/dev/null
fi

# Ensure runtime SA can read prod secrets (idempotent)
PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
RUNTIME_SA_DEFAULT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_SA:-$RUNTIME_SA_DEFAULT}"
for S in ${SECRET_OPENAI} ${SECRET_TELEGRAM} ${SECRET_WEBHOOK}; do
  echo "Granting secretAccessor on $S to $RUNTIME_SA (idempotent)…"
  gcloud secrets add-iam-policy-binding "$S" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null || true
done

echo "Promotion complete. To rollback: ./scripts/rollback-prod.sh"


