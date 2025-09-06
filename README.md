# Goblin Solana Agent

Basic Python project scaffolding for an autonomous Solana agent. It includes
modules for Slack integration, an OpenAI planner, Solana wallet utilities,
search tools for on-chain data, and a Streamlit dashboard for performance
tracking.

## Project Structure

```
chat/       # Slack bot integration
planner/    # OpenAI planner logic
wallet/     # Solana wallet transaction functions
tools/      # On-chain search and DeFi opportunities agent
dashboard/  # Performance tracking dashboard (Streamlit)
main.py     # Orchestrator entry point
.env        # API keys (OpenAI, Solana, Slack)
```

## Setup

1. Create a virtual environment and install dependencies:

```bash
pip install -r requirements.txt
```

2. Provide API keys in `.env`.
3. Run the main script:

```bash
python main.py
```
