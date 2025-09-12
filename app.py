from flask import Flask, request, jsonify
import os, re, time, hmac, hashlib, json, threading, requests

# ---- optional wallet imports (planner should not depend on these at import time)
WALLET_OK = True
try:
    # If you truly need these in the planner, guard them; ideally keep them only on the executor.
    from wallet.agent_wallet import stake_sol, unstake_sol, swap_tokens
except Exception as e:
    WALLET_OK = False
    WALLET_IMPORT_ERR = str(e)

# ---- OpenAI client (new SDK first, fallback to old)
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    use_new_client = True
except Exception:
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    use_new_client = False

app = Flask(__name__)

# Single source of truth for EXECUTOR_URL; strip trailing slash; default only for local dev
EXECUTOR_URL = (os.getenv("EXECUTOR_URL") or "http://localhost:5000").rstrip("/")
SECRET_APPROVAL_KEY = (os.getenv("SECRET_APPROVAL_KEY") or "dev").encode()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APPROVAL_CHANNEL = os.getenv("SLACK_APPROVAL_CHANNEL", "")
APPROVAL_TOKEN_TTL = int(os.getenv("APPROVAL_TOKEN_TTL", "300"))

def _sign_payload(from_mint: str, to_mint: str, amount: float, expires: int) -> str:
    msg = json.dumps(
        {"from_mint": from_mint, "to_mint": to_mint, "amount": amount, "expires": expires},
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
    expected = _sign_payload(data.get("from_mint"), data.get("to_mint"), data.get("amount"), expires)
    return hmac.compare_digest(expected, token)

def _post_slack_approval(from_mint: str, to_mint: str, amount: float) -> None:
    if not SLACK_BOT_TOKEN or not SLACK_APPROVAL_CHANNEL:
        return
    expires = int(time.time()) + APPROVAL_TOKEN_TTL
    token = _sign_payload(from_mint, to_mint, amount, expires)
    payload = {"from_mint": from_mint, "to_mint": to_mint, "amount": amount, "expires": expires, "token": token}
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"Swap request: {amount} {from_mint} → {to_mint}"}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "style": "primary",
             "value": json.dumps(payload), "action_id": "approve"},
            {"type": "button", "text": {"type": "plain_text", "text": "Deny"}, "style": "danger",
             "value": "deny", "action_id": "deny"},
        ]},
    ]
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    try:
        requests.post(
            "https://slack.com/api/chat.postMessage",
            headers=headers,
            json={"channel": SLACK_APPROVAL_CHANNEL, "text": "Swap approval required", "blocks": blocks},
            timeout=10,
        )
    except Exception:
        pass

@app.route("/ping")
def ping():
    return "pong", 200

# ---- Optional local stake/unstake endpoints (no-op if wallet not present)
@app.route("/stake", methods=["POST"])
def stake_handler():
    if not WALLET_OK:
        return jsonify({"error": f"wallet module not available: {WALLET_IMPORT_ERR}"}), 501
    data = request.get_json(force=True)
    protocol = data.get("protocol")
    amount_lamports = int(data.get("amountLamports", 0))
    return jsonify(stake_sol(protocol, amount_lamports)), 200

@app.route("/unstake", methods=["POST"])
def unstake_handler():
    if not WALLET_OK:
        return jsonify({"error": f"wallet module not available: {WALLET_IMPORT_ERR}"}), 501
    data = request.get_json(force=True)
    protocol = data.get("protocol")
    amount_lamports = int(data.get("amountLamports", 0))
    return jsonify(unstake_sol(protocol, amount_lamports)), 200

@app.route("/slack/events", methods=["POST"])
def slack_events():
    # Slack URL verification (JSON)
    if request.is_json:
        data = request.get_json(silent=True)
        if data and data.get("type") == "url_verification":
            return jsonify({"challenge": data["challenge"]})

    # Slash command (form-encoded)
    if request.form.get("command") == "/goblin":
        user_text = (request.form.get("text") or "").strip()
        response_url = request.form.get("response_url")
        user_name = request.form.get("user_name") or "you"
        lower_text = user_text.lower()

        # ---- balance (executor) ----
        if lower_text.startswith("balance"):
            def run_balance():
                try:
                    r = requests.post(f"{EXECUTOR_URL}/balance", timeout=10)
                    r.raise_for_status()
                    data = r.json()
                    sol = data.get("sol") or data.get("SOL") or (data.get("lamports", 0)/1_000_000_000)
                    reply = f"💰 Agent balance ({data.get('pubkey','?')}): **{float(sol):.6f} SOL**"
                except Exception as e:
                    reply = f"Error fetching balance: {e}"
                try:
                    requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
                except Exception:
                    pass
            threading.Thread(target=run_balance, daemon=True).start()
            return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking…"}), 200

        # ---- quote (executor) ----
        if lower_text.startswith("quote"):
            m = re.match(r"quote\s+([A-Za-z0-9]+)->([A-Za-z0-9]+)\s+([0-9.]+)", user_text)
            def run_quote():
                try:
                    if not m:
                        raise ValueError("Could not parse quote command. Use: `quote SOL->USDC 0.2`")
                    frm, to, amount = m.groups()
                    payload = {"from": frm, "to": to, "amount": float(amount)}
                    resp = requests.post(f"{EXECUTOR_URL}/quote", json=payload, timeout=15)
                    resp.raise_for_status()
                    reply = "Best route:\n```" + json.dumps(resp.json(), indent=2) + "```"
                except Exception as e:
                    reply = f"Error fetching quote: {e}"
                try:
                    requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
                except Exception:
                    pass
            threading.Thread(target=run_quote, daemon=True).start()
            return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking…"}), 200

        # ---- swap (executor with approval) ----
        if lower_text.startswith("swap"):
            m = re.match(r"swap\s+([A-Za-z0-9]+)->([A-Za-z0-9]+)\s+([0-9.]+)", user_text)
            def run_swap():
                try:
                    if not m:
                        raise ValueError("Could not parse swap command. Use: `swap SOL->USDC 0.02`")
                    frm, to, amount = m.groups()
                    payload = {"from": frm, "to": to, "amount": float(amount)}
                    resp = requests.post(f"{EXECUTOR_URL}/swap", json=payload, timeout=20)
                    data = resp.json()
                    if data.get("requiresApproval") or data.get("requires_human_approval"):
                        token = data.get("approvalToken") or data.get("token") or ""
                        msg = {
                            "response_type": "ephemeral",
                            "text": f"Swap {frm}->{to} {amount} requires approval.",
                            "blocks": [{"type": "actions","elements":[
                                {"type":"button","text":{"type":"plain_text","text":"Approve"},"style":"primary","value":token,"action_id":"approve_swap"},
                                {"type":"button","text":{"type":"plain_text","text":"Deny"},"style":"danger","value":token,"action_id":"deny_swap"},
                            ]}],
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

        # ---- LLM plan (non-blocking) ----
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
                        messages=[{"role":"system","content":system_prompt},
                                  {"role":"user","content":user_text}],
                        temperature=0.4, max_tokens=400
                    )
                    reply = resp.choices[0].message.content
                else:
                    resp = openai.ChatCompletion.create(
                        model="gpt-5",
                        messages=[{"role":"system","content":system_prompt},
                                  {"role":"user","content":user_text}],
                        temperature=0.4, max_tokens=400
                    )
                    reply = resp["choices"][0]["message"]["content"]
            except Exception as e:
                reply = f"🤕 Error generating plan: {e}"
            try:
                requests.post(response_url, json={
                    "response_type": "in_channel",
                    "text": f"🧙 Goblin to @{user_name}:\n{reply}"
                }, timeout=10)
            except Exception:
                pass

        threading.Thread(target=run_llm, daemon=True).start()
        return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking… I’ll post the plan here shortly."}), 200

    # non-command events
    return "ok", 200

# Slack interactive button callback (optional, kept for later)
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
                requests.post(response_url, json={"text":"❌ Swap denied","replace_original":True}, timeout=10)
            except Exception:
                pass
        return "", 200

    try:
        data = json.loads(value)
    except Exception:
        if response_url:
            try:
                requests.post(response_url, json={"text":"⚠️ Invalid payload","replace_original":True}, timeout=10)
            except Exception:
                pass
        return "", 200

    try:
        resp = requests.post(f"{EXECUTOR_URL}/swap", json=data, timeout=20)
        result = resp.json()
        text = f"✅ Swap executed. txSignature: {result['txSignature']}" if (resp.status_code==200 and result.get("txSignature")) \
               else f"⚠️ Swap failed: {result.get('error','unknown')}"
    except Exception as e:
        text = f"⚠️ Swap failed: {e}"

    if response_url:
        try:
            requests.post(response_url, json={"text":text,"replace_original":True}, timeout=10)
        except Exception:
            pass
    return "", 200

# Cloud Run port binding
PORT = int(os.getenv("PORT", "8080"))
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)