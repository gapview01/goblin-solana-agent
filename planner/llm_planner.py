"""LLM-based planner that turns a natural language goal into a DeFi action plan."""
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def generate_plan(goal: str, wallet_state: dict, market_data: dict) -> dict:
    """Generate a DeFi plan to achieve ``goal`` using GPT-4.

    Parameters
    ----------
    goal: str
        Natural language objective for the portfolio, e.g. "turn 1 SOL into 10".
    wallet_state: dict
        Current holdings and positions of the user's wallet.
    market_data: dict
        Relevant on-chain or market information.

    Returns
    -------
    dict
        JSON structure describing a plan with timestamped actions.
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = (
        "You are a DeFi planning assistant. Given the user's goal, wallet state and market"
        " data, design a step-by-step plan using strategies such as staking, leveraging"
        " and swapping. Return the plan as JSON with an 'actions' list. Each action"
        " should include 'step', 'action', 'description', and 'timestamp' (ISO 8601 UTC)."
    )

    user_context = {
        "goal": goal,
        "wallet_state": wallet_state,
        "market_data": market_data,
    }

    response = client.responses.create(
        model="gpt-4.1",
        input=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(user_context)},
        ],
        response_format={"type": "json_object"},
    )

    plan_text = response.output[0].content[0].text
    plan = json.loads(plan_text)

    # Ensure each action has a timestamp
    now = datetime.now(timezone.utc).isoformat()
    for action in plan.get("actions", []):
        action.setdefault("timestamp", now)

    return plan
