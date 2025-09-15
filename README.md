# Goblin Solana Agent

Chat-first Solana agent composed of a Python planner (Flask) and a Node.js
executor. The planner accepts `/goblin` Slack commands and dispatches actions to
the executor which talks to Solana and Jupiter.

## Environment variables

See [`.env.example`](./.env.example) for the full set of variables expected by
both services. All secrets should be provided via environment variables or
Secret Manager when deploying.

## Deployment

Two helper scripts deploy the planner and executor to Cloud Run. They accept an
optional `SUFFIX` environment variable which appends to the service name so
branch deployments can live alongside production.

```bash
# deploy executor
SUFFIX="-mvp" RPC_URL="https://api.mainnet-beta.solana.com" \
AGENT_SECRET_B58="…" ./scripts/deploy-executor.sh

# deploy planner (uses EXECUTOR_URL from previous step)
EXECUTOR_URL="$(gcloud run services describe executor-node-mvp --region australia-southeast1 --format='value(status.url)')"
SUFFIX="-mvp" EXECUTOR_URL="$EXECUTOR_URL" OPENAI_API_KEY="…" \
SLACK_BOT_TOKEN="…" SLACK_SIGNING_SECRET="…" ./scripts/deploy-planner.sh
```

Omit `SUFFIX` when deploying to production.

## Testing

After deploying, basic executor checks:

```bash
curl -s "$EXECUTOR_URL/health"
curl -s "$EXECUTOR_URL/balance"
curl -s "$EXECUTOR_URL/token-balance?mint=BONK"
```

Slack: create a Slack app that points slash command `/goblin` to the planner
URL and try commands like `/goblin balance` or `/goblin quote SOL->USDC 0.1`.

For branch-specific instructions see [README-branch.md](./README-branch.md).

