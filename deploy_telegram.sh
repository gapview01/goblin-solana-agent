#!/usr/bin/env bash
set -euo pipefail

# ---- fixed identifiers
REGION="australia-southeast1"
SERVICE="telegram-service"

# ---- optional non-secret config
# OPENAI_PROJECT can be set if you use OpenAI org projects; safe to leave unset
OPENAI_PROJECT="${OPENAI_PROJECT:-}"

# ---- executor service URL (this MUST be the executor, not telegram)
EXECUTOR_URL="https://executor-node-hwiba4dc7a-ts.a.run.app"
EXECUTOR_TOKEN="${EXECUTOR_TOKEN:-}"   # leave empty unless you actually use it

# ---- wallet + access
WALLET_ADDRESS="534sbVhF16EH8WoQumiawMy9gvbcLhGrXUroaSbyAFDv"
ALLOWED_TELEGRAM_USER_IDS="6149503319"

# ---- resolve Telegram service URL for webhook
SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
if [[ -z "$SERVICE_URL" ]]; then
  echo "Failed to resolve service URL for $SERVICE in $REGION"; exit 1
fi

echo "Using:"
echo "  SERVICE_URL  = $SERVICE_URL (telegram service)"
echo "  EXECUTOR_URL = $EXECUTOR_URL (executor-node)"

# ---- build envs in one go (non-secrets only)
ENV_VARS="WEBHOOK_BASE_URL=${SERVICE_URL},LOG_LEVEL=DEBUG,PLANNER_IMPL=llm,EXECUTOR_URL=${EXECUTOR_URL},WALLET_ADDRESS=${WALLET_ADDRESS},NETWORK=mainnet,DEFAULT_SLIPPAGE_BPS=100,ALLOWED_TELEGRAM_USER_IDS=${ALLOWED_TELEGRAM_USER_IDS},ALLOWED_TOKENS=*,MIN_TOKEN_MCAP_USD=15000000,USE_POLLING=1"
if [[ -n "$OPENAI_PROJECT" ]]; then
  ENV_VARS=",OPENAI_PROJECT=${OPENAI_PROJECT},${ENV_VARS}"
fi
SECRETS="OPENAI_API_KEY=openai-api-key:latest,TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,WEBHOOK_SECRET=telegram-webhook-secret:latest"
if [[ -n "$EXECUTOR_TOKEN" ]]; then
  ENV_VARS="${ENV_VARS},EXECUTOR_TOKEN=${EXECUTOR_TOKEN}"
fi

# ---- apply envs
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "$ENV_VARS" \
  --set-secrets "$SECRETS"

# ---- build & deploy from source
gcloud run deploy "$SERVICE" --region "$REGION" --source . --allow-unauthenticated --min-instances=1 --max-instances=3 --ingress all --cpu=1 --memory=512Mi

# ---- quick sanity: print the envs the service is actually running with
gcloud run services describe "$SERVICE" --region "$REGION" \
  --format='value(spec.template.spec.containers[0].env)'

# ---- confirm webhook (only if TELEGRAM_BOT_TOKEN is set in your shell)
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo" | python -m json.tool
else
  echo "Skipping Telegram webhook check (TELEGRAM_BOT_TOKEN not set locally)."
fi