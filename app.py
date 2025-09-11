from flask import Flask, request, jsonify
import os
import threading
import requests
from wallet.agent_wallet import stake_sol, unstake_sol
import json
import re
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
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    use_new_client = False

app = Flask(__name__)
EXECUTOR_URL = (os.getenv("EXECUTOR_URL") or "").rstrip("/")

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

    if request.form.get("command") == "/goblin":
        user_text = (request.form.get("text") or "").strip()
        response_url = request.form.get("response_url")
        user_name = request.form.get("user_name") or "you"
        lower_text = user_text.lower()

        if lower_text.startswith("balance"):
            def run_balance():
                try:
                    resp = requests.post(f"{EXECUTOR_URL}/balance", timeout=10)
                    data = resp.json()
                    sol = data.get("SOL") or data.get("sol") or data
                    reply = f"Balance: {sol} SOL"
                except Exception as e:
                    reply = f"Error fetching balance: {e}"
                try:
                    requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
                except Exception:
                    pass
            threading.Thread(target=run_balance, daemon=True).start()
            return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking…"}), 200

        if lower_text.startswith("quote"):
            m = re.match(r"quote\s+([A-Za-z0-9]+)->([A-Za-z0-9]+)\s+([0-9.]+)", user_text)
            def run_quote():
                try:
                    if not m:
                        raise ValueError("Could not parse quote command")
                    frm, to, amount = m.groups()
                    payload = {"from": frm, "to": to, "amount": float(amount)}
                    resp = requests.post(f"{EXECUTOR_URL}/quote", json=payload, timeout=10)
                    reply = "Best route:\n```" + json.dumps(resp.json(), indent=2) + "```"
                except Exception as e:
                    reply = f"Error fetching quote: {e}"
                try:
                    requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
                except Exception:
                    pass
            threading.Thread(target=run_quote, daemon=True).start()
            return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking…"}), 200

        if lower_text.startswith("swap"):
            m = re.match(r"swap\s+([A-Za-z0-9]+)->([A-Za-z0-9]+)\s+([0-9.]+)", user_text)
            def run_swap():
                try:
                    if not m:
                        raise ValueError("Could not parse swap command")
                    frm, to, amount = m.groups()
                    payload = {"from": frm, "to": to, "amount": float(amount)}
                    resp = requests.post(f"{EXECUTOR_URL}/swap", json=payload, timeout=10)
                    data = resp.json()
                    if data.get("requiresApproval"):
                        token = data.get("approvalToken") or data.get("token") or ""
                        msg = {
                            "response_type": "ephemeral",
                            "text": f"Swap {frm}->{to} {amount} requires approval.",
                            "blocks": [
                                {
                                    "type": "actions",
                                    "elements": [
                                        {
                                            "type": "button",
                                            "text": {"type": "plain_text", "text": "Approve"},
                                            "style": "primary",
                                            "value": token,
                                            "action_id": "approve_swap",
                                        },
                                        {
                                            "type": "button",
                                            "text": {"type": "plain_text", "text": "Deny"},
                                            "style": "danger",
                                            "value": token,
                                            "action_id": "deny_swap",
                                        },
                                    ],
                                }
                            ],
                        }
                        requests.post(response_url, json=msg, timeout=10)
                    else:
                        reply = "Swap result:\n```" + json.dumps(data, indent=2) + "```"
                        requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
                except Exception as e:
                    try:
                        requests.post(response_url, json={"response_type": "ephemeral", "text": f"Error executing swap: {e}"}, timeout=10)
                    except Exception:
                        pass
            threading.Thread(target=run_swap, daemon=True).start()
            return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking…"}), 200

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
                pass
        threading.Thread(target=run_llm, daemon=True).start()
        return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking… I’ll post the plan here shortly."}), 200

    return "ok", 200

@app.route("/slack/actions", methods=["POST"])
def slack_actions():
    payload = request.form.get("payload")
    if not payload:
        return "", 200
    try:
        data = json.loads(payload)
        actions = data.get("actions") or []
        if not actions:
            return "", 200
        action = actions[0]
        value = action.get("value")
        response_url = data.get("response_url")

        def handle():
            if action.get("action_id") == "approve_swap":
                try:
                    resp = requests.post(f"{EXECUTOR_URL}/swap", json={"approvalToken": value}, timeout=10)
                    reply = "Swap executed:\n```" + json.dumps(resp.json(), indent=2) + "```"
                except Exception as e:
                    reply = f"Error executing swap: {e}"
            else:
                reply = "Swap denied."
            try:
                requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
            except Exception:
                pass

        threading.Thread(target=handle, daemon=True).start()
    except Exception:
        pass
    return "", 200

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
