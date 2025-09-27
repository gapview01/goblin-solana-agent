#!/usr/bin/env bash
set -euo pipefail

REGION="australia-southeast1"
SERVICE="telegram-service"

echo "Listing last 5 revisions for $SERVICE…"
gcloud run revisions list --region "$REGION" --service "$SERVICE" --limit 5 --format 'table(name,status,traffic,creationTimestamp)'

read -r -p "Enter revision name to roll back to: " REV
if [[ -z "$REV" ]]; then
  echo "No revision provided."; exit 1
fi

echo "Routing 100% traffic to $REV…"
gcloud run services update-traffic "$SERVICE" --region "$REGION" --to-revisions "$REV=100"
echo "Done."


