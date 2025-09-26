#!/usr/bin/env bash
set -euo pipefail

# Local runner: loads secrets from GCP and starts the executor on PORT (default 9000)

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
if [[ -z "${PROJECT}" ]]; then
  echo "GCP project not set. Run: gcloud config set project <project>" >&2
  exit 1
fi

PORT="${PORT:-9000}"

export RPC_URL="$(gcloud secrets versions access latest --secret=solana-rpc-url | tr -d '\n')"
export AGENT_SECRET_B58="$(gcloud secrets versions access latest --secret=solana-wallet-key | tr -d '\n')"

if [[ -z "${RPC_URL}" || -z "${AGENT_SECRET_B58}" ]]; then
  echo "Missing RPC_URL or AGENT_SECRET_B58 from Secret Manager" >&2
  exit 1
fi

# Ensure Node 20
if command -v nvm >/dev/null 2>&1; then
  source ~/.nvm/nvm.sh
  nvm install 20 >/dev/null
  nvm use 20 >/dev/null
fi

cd "$(dirname "$0")/../executor-node"
echo "Starting executor on :${PORT} (project=${PROJECT})"
PORT="${PORT}" node index.js 2>&1 | sed -e 's/^/[executor] /'


