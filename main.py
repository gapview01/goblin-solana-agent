"""Orchestrator entry point for Goblin Solana Agent."""
import os
from dotenv import load_dotenv

from chat.slack_agent import create_app
from planner.planner import plan
from wallet.solana_wallet import get_balance
from tools.defi_agent import fetch_opportunities


def main() -> None:
    load_dotenv()
    slack_token = os.getenv("SLACK_BOT_TOKEN", "")
    slack_secret = os.getenv("SLACK_SIGNING_SECRET", "")
    app = create_app(slack_token, slack_secret)
    print("Planner says:", plan("Plan a DeFi strategy"))
    print("Balance:", get_balance("So11111111111111111111111111111111111111112"))
    print("Opportunities:", fetch_opportunities())
    # app.start(...) would be executed in a real environment.


if __name__ == "__main__":
    main()
