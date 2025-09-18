"""Orchestrator entry point for Goblin Solana Agent."""
import os
from dotenv import load_dotenv

from chat.slack_agent import create_app
from planner.planner import plan
from wallet.solana_wallet import get_balance
from tools.defi_agent import fetch_opportunities

# Load .env for local dev; Cloud Run uses real env vars.
load_dotenv()

# Build the Flask application that Cloud Run/Gunicorn will serve.
application = create_app(
    os.getenv("SLACK_BOT_TOKEN", ""),
    os.getenv("SLACK_SIGNING_SECRET", "")
)

@application.route("/ping")
def ping():
    return "pong"

def main() -> None:
    # Optional: warm-up/log useful info on startup
    print("Planner says:", plan("Plan a DeFi strategy"))
    print("Balance:", get_balance("So11111111111111111111111111111111111111112"))
    print("Opportunities:", fetch_opportunities())

# Run a local server if executed directly. On Cloud Run, Gunicorn typically
# imports `telegram_main:application`, but this also works if you `python telegram_main.py`.
if __name__ == "__main__":
    main()
    application.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))