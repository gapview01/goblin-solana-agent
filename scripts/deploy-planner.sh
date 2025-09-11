#!/usr/bin/env bash
set -euo pipefail

VARS=(OPENAI_API_KEY SLACK_BOT_TOKEN SLACK_SIGNING_SECRET EXECUTOR_URL RPC_URL AGENT_SECRET_B58)
missing=false
for var in "${VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "Missing environment variable: $var"
    missing=true
  fi
done
if [ "$missing" = true ]; then
  exit 1
fi

REGION="${REGION:-us-central1}"

# Deploy planner service
gcloud run deploy planner \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars OPENAI_API_KEY="$OPENAI_API_KEY",SLACK_BOT_TOKEN="$SLACK_BOT_TOKEN",SLACK_SIGNING_SECRET="$SLACK_SIGNING_SECRET",EXECUTOR_URL="$EXECUTOR_URL" \
  --set-secrets RPC_URL=$RPC_URL,AGENT_SECRET_B58=$AGENT_SECRET_B58

