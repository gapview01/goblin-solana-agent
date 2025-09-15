# Branch Testing (`feature/codex-mvp`)

This branch deploys isolated `-mvp` Cloud Run services so you can test without
touching production.

## Deploy

```bash
git checkout feature/codex-mvp

# deploy executor with suffix
SUFFIX="-mvp" RPC_URL="https://api.mainnet-beta.solana.com" \
AGENT_SECRET_B58="…" ./scripts/deploy-executor.sh

# fetch executor URL for planner deployment
EXECUTOR_URL="$(gcloud run services describe executor-node-mvp --region australia-southeast1 --format='value(status.url)')"

# deploy planner that talks to the -mvp executor
SUFFIX="-mvp" EXECUTOR_URL="$EXECUTOR_URL" OPENAI_API_KEY="…" \
SLACK_BOT_TOKEN="…" SLACK_SIGNING_SECRET="…" ./scripts/deploy-planner.sh
```

## Curl tests

```bash
curl -s "$EXECUTOR_URL/health"
curl -s "$EXECUTOR_URL/balance"
curl -s "$EXECUTOR_URL/token-balance?mint=BONK"
curl -s -X POST "$EXECUTOR_URL/quote" -H "Content-Type: application/json" \
  --data '{"from":"SOL","to":"BONK","amount":0.2}'
```

## Slack

Create a separate Slack app (e.g. **GoblinBot-MVP**) whose slash command
`/goblin` points to the planner URL `https://goblin-slackbot-mvp-.../slack/events`
and interactive endpoint `/slack/interactive`. Try commands like:

```
/goblin balance
/goblin quote SOL->BONK 0.2
/goblin swap SOL->USDC 0.02
```

## Going to production

When merging to `main`, deploy without `SUFFIX` to update the live services:

```bash
RPC_URL=… AGENT_SECRET_B58=… ./scripts/deploy-executor.sh
EXECUTOR_URL=… OPENAI_API_KEY=… SLACK_BOT_TOKEN=… SLACK_SIGNING_SECRET=… ./scripts/deploy-planner.sh
```

