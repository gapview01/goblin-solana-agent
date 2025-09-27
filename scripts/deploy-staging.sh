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

# Resolve project info and runtime service account
PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
if [[ -z "$PROJECT_ID" ]]; then
  echo "gcloud project not set; set it with: gcloud config set project <PROJECT_ID>"; exit 1
fi
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA_DEFAULT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
RUNTIME_SA="${RUNTIME_SA:-$RUNTIME_SA_DEFAULT}"
echo "Runtime Service Account: $RUNTIME_SA"

# Ensure runtime SA can access staging secrets BEFORE deploy (idempotent)
for S in ${SECRET_OPENAI:-openai-api-key-stg} ${SECRET_TELEGRAM:-telegram-bot-token-stg} ${SECRET_WEBHOOK:-telegram-webhook-secret-stg}; do
  echo "Granting secretAccessor on $S to $RUNTIME_SA (idempotent)…"
  gcloud secrets add-iam-policy-binding "$S" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null || true
done

# Resolve current URL if service exists
SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)' 2>/dev/null || true)"

# Use a provisional BASE_URL so the container starts an HTTP server on first boot
PROVISIONAL_BASE_URL="https://staging.invalid"
BASE_ENV="BASE_URL=${PROVISIONAL_BASE_URL},LOG_LEVEL=DEBUG,PLANNER_IMPL=llm,EXECUTOR_URL=${EXECUTOR_URL},WALLET_ADDRESS=${WALLET_ADDRESS},NETWORK=mainnet,DEFAULT_SLIPPAGE_BPS=100,ALLOWED_TELEGRAM_USER_IDS=${ALLOWED_TELEGRAM_USER_IDS},ALLOWED_TOKENS=*,MIN_TOKEN_MCAP_USD=15000000,PLANNER_TIMEOUT_SEC=${PLANNER_TIMEOUT_SEC}"
if [[ -n "$EXECUTOR_TOKEN" ]]; then
  BASE_ENV+=" ,EXECUTOR_TOKEN=${EXECUTOR_TOKEN}"
fi
if [[ -n "$OPENAI_PROJECT" ]]; then
  BASE_ENV="OPENAI_PROJECT=${OPENAI_PROJECT},${BASE_ENV}"
fi
# Do not override provisional before first deploy; we'll set the real BASE_URL after we know it

# staging secrets (use separate keys)
SECRETS="OPENAI_API_KEY=${SECRET_OPENAI:-openai-api-key-stg}:latest,TELEGRAM_BOT_TOKEN=${SECRET_TELEGRAM:-telegram-bot-token-stg}:latest,WEBHOOK_SECRET=${SECRET_WEBHOOK:-telegram-webhook-secret-stg}:latest"

echo "Applying envs to $SERVICE (may create revision)…"
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "$BASE_ENV" \
  --set-secrets "$SECRETS" \
  --service-account "$RUNTIME_SA" || true

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
  --set-secrets "$SECRETS" \
  --service-account "$RUNTIME_SA"

# Read the final URL and ensure BASE_URL is set accordingly
SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo "STAGING Service URL: $SERVICE_URL"

if [[ -n "$SERVICE_URL" ]]; then
  gcloud run services update "$SERVICE" --region "$REGION" \
    --update-env-vars "BASE_URL=${SERVICE_URL}" >/dev/null
fi

echo "Staging deploy complete. URL: $SERVICE_URL"

