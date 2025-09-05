# Architecture (Pilot)
**Loop (intended):**
1) Perceive: read state (wallet, programs, indexer)
2) Plan: LLM proposes steps toward a goal
3) Act: sign and send tx (devnet only during pilot)
4) Reflect: log outcome, adjust plan

**Adapters (future):**
- Solana RPC client (devnet)
- LLM provider adapter
- Policy guardrails (limits, allowlists)
