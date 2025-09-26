#!/usr/bin/env bash
set -euo pipefail

# Local runner: polling mode Telegram service with executor at localhost:9000

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
if [[ -z "${PROJECT}" ]]; then
  echo "GCP project not set. Run: gcloud config set project <project>" >&2
  exit 1
fi

export TELEGRAM_BOT_TOKEN="$(gcloud secrets versions access latest --secret=telegram-bot-token | tr -d '\n')"
export WEBHOOK_SECRET="$(gcloud secrets versions access latest --secret=telegram-webhook-secret | tr -d '\n')"
export OPENAI_API_KEY="${OPENAI_API_KEY:-$(gcloud secrets versions access latest --secret=openai-api-key | tr -d '\n' || true)}"

if [[ -z "${TELEGRAM_BOT_TOKEN}" || -z "${WEBHOOK_SECRET}" ]]; then
  echo "Missing telegram secrets from Secret Manager" >&2
  exit 1
fi

export EXECUTOR_URL="${EXECUTOR_URL:-http://localhost:9000}"
export USE_POLLING="1"
export LOG_LEVEL="DEBUG"

cd "$(dirname "$0")/.."
echo "Starting Telegram service (polling) with EXECUTOR_URL=${EXECUTOR_URL}"
python3 telegram_service/server.py 2>&1 | sed -e 's/^/[telegram] /'


