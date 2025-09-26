#!/usr/bin/env bash
set -euo pipefail

# Simple canary: checks executor health and sends /ping to Telegram bot

fail() { echo "[canary] $*" >&2; exit 1; }

EXECUTOR_URL="${EXECUTOR_URL:-http://localhost:9000}"

echo "[canary] checking executor at ${EXECUTOR_URL}/health"
curl -fsS "${EXECUTOR_URL}/health" >/dev/null || fail "executor health failed"

TOKEN="${TELEGRAM_BOT_TOKEN:-$(gcloud secrets versions access latest --secret=telegram-bot-token | tr -d '\n' || true)}"
CHAT_ID="${TELEGRAM_CHAT_ID:-}" # set a dedicated chat id for canary
if [[ -n "$TOKEN" && -n "$CHAT_ID" ]]; then
  echo "[canary] sending /ping"
  curl -fsS -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
    -d chat_id="${CHAT_ID}" -d text="/ping" >/dev/null || fail "telegram send failed"
else
  echo "[canary] skipping telegram check (missing TELEGRAM_CHAT_ID or token)"
fi

echo "[canary] ok"


