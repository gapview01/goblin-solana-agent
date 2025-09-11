"""Slack bot that plans actions based on user goals."""
from __future__ import annotations

import os
from typing import Dict
from flask import Flask, request, jsonify
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from dotenv import load_dotenv

from planner.planner import plan

# Load environment variables
load_dotenv()

# In-memory store mapping user IDs to their latest goal
USER_GOALS: Dict[str, str] = {}

def create_app(token: str, signing_secret: str) -> Flask:
    """Create and return a Flask app wrapping the Slack Bolt app."""
    bolt_app = App(token=token, signing_secret=signing_secret)
    handler = SlackRequestHandler(bolt_app)
    flask_app = Flask(__name__)

    @flask_app.route("/ping")
    def ping():
        return "pong", 200


    # Route for Slack event subscriptions (e.g. verification + messages)
    @flask_app.route("/slack/events", methods=["POST"])
    def slack_events():
        data = request.get_json(force=True)
        if data and data.get("type") == "url_verification":
            return jsonify({"challenge": data["challenge"]})
        return handler.handle(request)

    # Slash command handler (e.g. /goblin)
    @bolt_app.command("/goblin")
    def handle_goblin(ack, respond, command):
        ack()
        user_id = command.get("user_id")
        text = command.get("text", "").strip()
        if user_id:
            USER_GOALS[user_id] = text
        try:
            plan_summary = plan(text)
        except Exception as exc:
            plan_summary = f"Planning failed: {exc}"
        respond(plan_summary)

    return flask_app
