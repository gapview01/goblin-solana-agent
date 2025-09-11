from flask import Flask, request, jsonify
import os
import threading
import requests
from wallet.agent_wallet import stake_sol, unstake_sol
import time
import hmac
import hashlib
import json

from wallet.agent_wallet import swap_tokens

# OpenAI: current client pattern
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    use_new_client = True
except Exception:
    # Fallback if older SDK is present (still works, but prefer the new client)
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    use_new_client = False

app = Flask(__name__)

SECRET_APPROVAL_KEY = os.getenv("SECRET_APPROVAL_KEY", "dev").encode()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APPROVAL_CHANNEL = os.getenv("SLACK_APPROVAL_CHANNEL", "")
EXECUTOR_URL = os.getenv("EXECUTOR_URL", "http://localhost:5000")
APPROVAL_TOKEN_TTL = int(os.getenv("APPROVAL_TOKEN_TTL", "300"))


def _sign_payload(from_mint: str, to_mint: str, amount: float, expires: int) -> str:
    msg = json.dumps(
        {
            "from_mint": from_mint,
            "to_mint": to_mint,
            "amount": amount,
            "expires": expires,
        },
        separators=(",", ":"),
    )
    return hmac.new(SECRET_APPROVAL_KEY, msg.encode(), hashlib.sha256).hexdigest()


def _verify_payload(data: dict) -> bool:
    token = data.get("token")
    expires = data.get("expires")
    if not token or not expires:
        return False
    if time.time() > expires:
        return False
    expected = _sign_payload(
        data.get("from_mint"), data.get("to_mint"), data.get("amount"), expires
    )
    return hmac.compare_digest(expected, token)


def _post_slack_approval(from_mint: str, to_mint: str, amount: float) -> None:
    """Send an approval request to Slack."""
    expires = int(time.time()) + APPROVAL_TOKEN_TTL
    token = _sign_payload(from_mint, to_mint, amount, expires)
    payload = {
        "from_mint": from_mint,
        "to_mint": to_mint,
        "amount": amount,
        "expires": expires,
        "token": token,
    }
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Swap request: {amount} SOL\n{from_mint} → {to_mint}",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "value": json.dumps(payload),
                    "action_id": "approve",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Deny"},
                    "style": "danger",
                    "value": "deny",
                    "action_id": "deny",
                },
            ],
        },
    ]
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=headers,
        json={
            "channel": SLACK_APPROVAL_CHANNEL,
            "text": "Swap approval required",
            "blocks": blocks,
        },
        timeout=10,
    )

@app.route("/ping")
def ping():
    return "pong", 200


@app.route("/stake", methods=["POST"])
def stake_handler():
    data = request.get_json(force=True)
    protocol = data.get("protocol")
    amount_lamports = data.get("amountLamports")
    result = stake_sol(protocol, int(amount_lamports))
    return jsonify(result), 200


@app.route("/unstake", methods=["POST"])
def unstake_handler():
    data = request.get_json(force=True)
    protocol = data.get("protocol")
    amount_lamports = data.get("amountLamports")
    result = unstake_sol(protocol, int(amount_lamports))
    return jsonify(result), 200

@app.route("/slack/events", methods=["POST"])
def slack_events():
    # Slack URL verification (JSON)
    if request.is_json:
        data = request.get_json(silent=True)
        if data and data.get("type") == "url_verification":
            return jsonify({"challenge": data["challenge"]})

    # Slash commands are x-www-form-urlencoded
    if request.form.get("command") == "/goblin":
        user_text = (request.form.get("text") or "").strip()
        response_url = request.form.get("response_url")
        user_name = request.form.get("user_name") or "you"

        # Fire GPT in the background so we return within Slack’s 3s
        def run_llm():
            try:
                system_prompt = (
                    "You are Goblin, a witty GPT-5-class DeFi strategist. "
                    "Reason step-by-step internally, then give a concise plan. "
                    "State risks. Keep replies under 180 words. Use markdown."
                )
                if use_new_client:
                    resp = client.chat.completions.create(
                        model="gpt-5",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_text},
                        ],
                    )
                    reply = resp.choices[0].message.content
                else:
                    resp = openai.ChatCompletion.create(
                        model="gpt-5",
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_text},
                        ],
                    )
                    reply = resp["choices"][0]["message"]["content"]
            except Exception as e:
                reply = f"🤕 Error generating plan: {e}"

            # Post the final answer back to the channel via response_url
            try:
                requests.post(
                    response_url,
                    json={
                        "response_type": "in_channel",
                        "text": f"🧙 Goblin to @{user_name}:\n{reply}",
                    },
                    timeout=10,
                )
            except Exception:
                pass  # avoid crashing the worker on network error

        threading.Thread(target=run_llm, daemon=True).start()

        # Immediate ack so Slack doesn’t show “something’s wrong”
        return jsonify({
            "response_type": "ephemeral",
            "text": "🧠 Goblin is thinking… I’ll post the plan here shortly."
        }), 200

    return "ok", 200


@app.route("/swap", methods=["POST"])
def swap_route():
    data = request.get_json(force=True)
    from_mint = data.get("from_mint")
    to_mint = data.get("to_mint")
    amount = float(data.get("amount", 0))
    token = data.get("token")

    if token:
        if not _verify_payload(data):
            return jsonify({"error": "invalid or expired token"}), 403
        result = swap_tokens(from_mint, to_mint, amount, force=True)
        if "signature" in result:
            return jsonify({"txSignature": result["signature"]}), 200
        return jsonify({"error": "swap failed"}), 500

    if amount > 5:
        _post_slack_approval(from_mint, to_mint, amount)
        return jsonify({"requires_human_approval": True}), 202

    result = swap_tokens(from_mint, to_mint, amount)
    if "signature" in result:
        return jsonify({"txSignature": result["signature"]}), 200
    return jsonify(result), 400


@app.route("/slack/interactive", methods=["POST"])
def slack_interactive():
    payload_raw = request.form.get("payload", "{}")
    try:
        payload = json.loads(payload_raw)
    except Exception:
        return "", 400
    action = (payload.get("actions") or [{}])[0]
    value = action.get("value")
    response_url = payload.get("response_url")

    if value == "deny":
        if response_url:
            try:
                requests.post(
                    response_url,
                    json={"text": "❌ Swap denied", "replace_original": True},
                    timeout=10,
                )
            except Exception:
                pass
        return "", 200

    try:
        data = json.loads(value)
    except Exception:
        if response_url:
            try:
                requests.post(
                    response_url,
                    json={"text": "⚠️ Invalid payload", "replace_original": True},
                    timeout=10,
                )
            except Exception:
                pass
        return "", 200

    try:
        resp = requests.post(f"{EXECUTOR_URL}/swap", json=data, timeout=20)
        result = resp.json()
        if resp.status_code == 200 and result.get("txSignature"):
            text = f"✅ Swap executed. txSignature: {result['txSignature']}"
        else:
            text = f"⚠️ Swap failed: {result.get('error', 'unknown')}"
    except Exception as e:
        text = f"⚠️ Swap failed: {e}"

    if response_url:
        try:
            requests.post(
                response_url,
                json={"text": text, "replace_original": True},
                timeout=10,
            )
        except Exception:
            pass

    return "", 200
