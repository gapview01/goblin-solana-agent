"""Slack bot that plans actions based on user goals."""
from __future__ import annotations

from typing import Dict

from slack_bolt import App

from planner.planner import plan

# In-memory store mapping user IDs to their latest goal
USER_GOALS: Dict[str, str] = {}


def create_app(token: str, signing_secret: str) -> App:
    """Create a Slack Bolt app that handles the /goblin slash command.

    Parameters
    ----------
    token: str
        Slack bot token.
    signing_secret: str
        Slack signing secret.

    Returns
    -------
    App
        Configured Slack Bolt application.
    """
    app = App(token=token, signing_secret=signing_secret)

    @app.command("/goblin")
    def handle_goblin(ack, respond, command):
        """Handle the /goblin slash command.

        The command text is stored as the user's goal and sent to the LLM
        planner. A summary of the plan is then posted back to Slack.
        """
        ack()
        user_id = command.get("user_id")
        text = command.get("text", "").strip()
        if user_id:
            USER_GOALS[user_id] = text
        try:
            plan_summary = plan(text)
        except Exception as exc:  # pragma: no cover - network errors
            plan_summary = f"Planning failed: {exc}"
        respond(plan_summary)

    return app
