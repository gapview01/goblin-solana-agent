# Secrets

The planner and executor services need access to a Solana RPC endpoint and an agent secret without exposing them directly. Store these values in [GCP Secret Manager](https://cloud.google.com/secret-manager) and reference them during deployment.

## Store the values

```sh
# RPC URL
printf "%s" "https://api.devnet.solana.com" | gcloud secrets create RPC_URL --replication-policy=automatic --data-file=-

# Agent secret (base58)
printf "%s" "<base58-secret>" | gcloud secrets create AGENT_SECRET_B58 --replication-policy=automatic --data-file=-
```

If the secrets already exist, add new versions instead:

```sh
printf "%s" "$RPC_URL_VALUE" | gcloud secrets versions add RPC_URL --data-file=-
printf "%s" "$AGENT_SECRET_B58_VALUE" | gcloud secrets versions add AGENT_SECRET_B58 --data-file=-
```

## Deploying with `--set-secrets`

Before running the deploy scripts, set environment variables that point to the secret versions:

```sh
export RPC_URL="RPC_URL:latest"
export AGENT_SECRET_B58="AGENT_SECRET_B58:latest"
```

The deploy scripts include these references:

```sh
--set-secrets RPC_URL=$RPC_URL,AGENT_SECRET_B58=$AGENT_SECRET_B58
```

This wires the secrets into Cloud Run so the services read them at runtime without leaking values.
