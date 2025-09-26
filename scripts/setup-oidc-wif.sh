#!/usr/bin/env bash
set -euo pipefail

# Quick setup for GitHub OIDC → GCP Workload Identity Federation
# Usage:
#   PROJECT_ID=goblin-poc REPO="owner/repo" bash scripts/setup-oidc-wif.sh
# Optional overrides:
#   POOL_ID=github-pool PROVIDER_ID=github SA_NAME=gh-actions-ci

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REPO="${REPO:?set REPO as owner/repo}"
POOL_ID="${POOL_ID:-github-pool}"
PROVIDER_ID="${PROVIDER_ID:-github}"
SA_NAME="${SA_NAME:-gh-actions-ci}"

gcloud config set project "$PROJECT_ID" >/dev/null

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "[wif] creating service account: ${SA_EMAIL} (ok if exists)"
gcloud iam service-accounts create "$SA_NAME" --display-name "GitHub Actions CI" || true

echo "[wif] creating workload identity pool: ${POOL_ID} (ok if exists)"
gcloud iam workload-identity-pools create "$POOL_ID" \
  --location=global --display-name="GitHub OIDC" || true

POOL_FULL="projects/$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")/locations/global/workloadIdentityPools/${POOL_ID}"

echo "[wif] creating provider: ${PROVIDER_ID} for repo ${REPO} (ok if exists)"
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --workload-identity-pool="$POOL_ID" --location=global \
  --display-name="GitHub" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.actor=assertion.actor" \
  --issuer-uri="https://token.actions.githubusercontent.com" || true

PROVIDER_FULL="${POOL_FULL}/providers/${PROVIDER_ID}"

echo "[wif] allow GitHub repo to impersonate the service account"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_FULL}/attribute.repository/${REPO}" >/dev/null

echo "[wif] grant Secret Manager accessor"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null

echo "[wif] done"
echo "Provider: ${PROVIDER_FULL}"
echo "Service account: ${SA_EMAIL}"


