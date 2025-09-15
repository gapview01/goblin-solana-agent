from flask import Flask, request, jsonify
import os, re, time, hmac, hashlib, json, threading, requests

# ---- optional wallet imports (planner shouldn't crash if they're absent)
WALLET_OK = True
try:
    from wallet.agent_wallet import stake_sol, unstake_sol, swap_tokens
except Exception as e:
    WALLET_OK = False
    WALLET_IMPORT_ERR = str(e)

# ---- OpenAI client (prefer new SDK, fallback to old)
try:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    use_new_client = True
except Exception:
    import openai
    openai.api_key = os.getenv("OPENAI_API_KEY")
    use_new_client = False

app = Flask(__name__)

# ---- config
EXECUTOR_URL = (os.getenv("EXECUTOR_URL") or "http://localhost:5000").rstrip("/")
SECRET_APPROVAL_KEY = (os.getenv("SECRET_APPROVAL_KEY") or "dev").encode()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APPROVAL_CHANNEL = os.getenv("SLACK_APPROVAL_CHANNEL", "")
APPROVAL_TOKEN_TTL = int(os.getenv("APPROVAL_TOKEN_TTL", "300"))

# ---------- helpers ----------
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

# ---------- health ----------
@app.route("/ping")
def ping():
    return "pong", 200

# ---------- optional local stake/unstake (planner-local, not used by Slack commands) ----------
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

# ---------- slack entry ----------
@app.route("/slack/events", methods=["POST"])
def slack_events():
    # URL verification
    if request.is_json:
        data = request.get_json(silent=True)
        if data and data.get("type") == "url_verification":
            return jsonify({"challenge": data["challenge"]})

    # Slash command
    if request.form.get("command") == "/goblin":
        user_text = (request.form.get("text") or "").strip()
        response_url = request.form.get("response_url")
        user_name = request.form.get("user_name") or "you"
        lower_text = user_text.lower()

        # ---- balance (executor) ----
        if lower_text.startswith("balance"):
            def run_balance():
                try:
                    r = requests.get(f"{EXECUTOR_URL}/balance", timeout=10)  # GET (no body)
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
            # forgiving parser: spaces around ->, lowercase ok, .5 amounts, symbols with . or -
            m = re.match(
                r"""(?ix)
                ^\s*quote\s+([A-Z0-9.\-]+)\s*->\s*([A-Z0-9.\-]+)\s+([0-9]*\.?[0-9]+)\s*$
                """,
                user_text
            )
            def run_quote():
                try:
                    if not m:
                        raise ValueError("Could not parse quote command. Use: `quote SOL->USDC 0.2`")
                    frm, to, amount = m.groups()
                    frm, to = frm.upper(), to.upper()
                    payload = {"from": frm, "to": to, "amount": float(amount)}
                    resp = requests.post(f"{EXECUTOR_URL}/quote", json=payload, timeout=15)
                    resp.raise_for_status()
                    q = resp.json()

                    # Pretty summary
                    in_amt  = q.get("inAmount")
                    out_amt = q.get("outAmount")
                    price   = q.get("priceImpactPct")
                    route   = q.get("routePlan") or []
                    other   = q.get("otherRoutePlans") or []

                    DEC = {"SOL": 9, "USDC": 6}
                    def to_float(x, dp=9):
                        try: return float(x) / (10 ** dp)
                        except Exception: return None

                    in_readable  = to_float(in_amt,  DEC.get(frm, 9))
                    out_readable = to_float(out_amt, DEC.get(to, 9))

                    lines = [f"*Quote* `{frm}` → `{to}` for **{amount} {frm}**"]
                    if in_readable is not None and out_readable is not None:
                        lines.append(f"• Est. output: **{out_readable:.6f} {to}** (input {in_readable:.6f} {frm})")
                    else:
                        lines.append(f"• Raw: inAmount={in_amt}, outAmount={out_amt}")

                    if price is not None:
                        try: lines.append(f"• Price impact: **{float(price)*100:.2f}%**")
                        except Exception: pass

                    if route:
                        hops = []
                        for hop in route[:3]:
                            prog = (hop.get('swapInfo') or {}).get('programId') or hop.get('programId') or "?"
                            hops.append(f"`{prog}`")
                        lines.append("• Route programs: " + " → ".join(hops))

                    if other:
                        lines.append(f"• Other routes available: {len(other)}")

                    reply = "\n".join(lines)

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
            m = re.match(
                r"""(?ix)
                ^\s*swap\s+([A-Z0-9.\-]+)\s*->\s*([A-Z0-9.\-]+)\s+([0-9]*\.?[0-9]+)\s*$
                """,
                user_text
            )
            def run_swap():
                try:
                    if not m:
                        raise ValueError("Could not parse swap command. Use: `swap SOL->USDC 0.02`")
                    frm, to, amount = m.groups()
                    frm, to = frm.upper(), to.upper()
                    payload = {"from": frm, "to": to, "amount": float(amount)}

                    resp = requests.post(f"{EXECUTOR_URL}/swap", json=payload, timeout=25)

                    # Robust handling if executor returns non-JSON/empty
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"status": resp.status_code, "text": (resp.text or "")[:500]}

                    if resp.status_code == 200 and data.get("txSignature"):
                        reply = f"✅ Swap executed. txSignature: `{data['txSignature']}`"
                    elif data.get("requiresApproval") or data.get("requires_human_approval"):
                        token = data.get("approvalToken") or data.get("token") or ""
                        reply = None
                        msg = {
                            "response_type": "ephemeral",
                            "text": f"Swap {frm}->{to} {amount} requires approval.",
                            "blocks": [{"type":"actions","elements":[
                                {"type":"button","text":{"type":"plain_text","text":"Approve"},"style":"primary","value":token,"action_id":"approve_swap"},
                                {"type":"button","text":{"type":"plain_text","text":"Deny"},"style":"danger","value":token,"action_id":"deny_swap"},
                            ]}],
                        }
                        requests.post(response_url, json=msg, timeout=10)
                    else:
                        reply = "⚠️ Swap failed:\n```" + json.dumps(data, indent=2) + "```"

                    if reply:
                        requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
                except Exception as e:
                    try:
                        requests.post(response_url, json={"response_type": "ephemeral", "text": f"Error executing swap: {e}"}, timeout=10)
                    except Exception:
                        pass
            threading.Thread(target=run_swap, daemon=True).start()
            return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking…"}), 200

        # ---- stake jito (executor) ----
        if lower_text.startswith("stake"):
            # Accept: "stake jito 0.25" or "stake 0.25" (defaults to jito)
            m_full  = re.match(r"""(?ix)^\s*stake\s+jito\s+([0-9]*\.?[0-9]+)\s*(sol)?\s*$""", user_text)
            m_short = re.match(r"""(?ix)^\s*stake\s+([0-9]*\.?[0-9]+)\s*(sol)?\s*$""", user_text)

            def run_stake():
                try:
                    if m_full:
                        amt = float(m_full.group(1))
                    elif m_short:
                        amt = float(m_short.group(1))
                    else:
                        raise ValueError("Usage: `stake jito 0.25` (amount in SOL)")

                    lamports = int(amt * 1_000_000_000)
                    payload = {"protocol": "jito", "amountLamports": lamports}

                    resp = requests.post(f"{EXECUTOR_URL}/stake", json=payload, timeout=25)
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"status": resp.status_code, "text": (resp.text or "")[:500]}

                    if resp.status_code == 200 and data.get("txSignature"):
                        reply = f"✅ Staked **{amt:.6f} SOL** via *jito*. txSignature: `{data['txSignature']}`"
                    elif resp.status_code == 404:
                        reply = "⚠️ Executor does not expose `/stake` yet."
                    else:
                        reply = "⚠️ Stake failed:\n```" + json.dumps(data, indent=2) + "```"
                except Exception as e:
                    reply = f"Error staking: {e}"

                try:
                    requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
                except Exception:
                    pass

            threading.Thread(target=run_stake, daemon=True).start()
            return jsonify({"response_type": "ephemeral", "text": "🧠 Goblin is thinking…"}), 200

        # ---- unstake jito (executor) ----
        if lower_text.startswith("unstake"):
            # Accept: "unstake jito 0.25" or "unstake 0.25" (defaults to jito)
            m_full  = re.match(r"""(?ix)^\s*unstake\s+jito\s+([0-9]*\.?[0-9]+)\s*(sol)?\s*$""", user_text)
            m_short = re.match(r"""(?ix)^\s*unstake\s+([0-9]*\.?[0-9]+)\s*(sol)?\s*$""", user_text)

            def run_unstake():
                try:
                    if m_full:
                        amt = float(m_full.group(1))
                    elif m_short:
                        amt = float(m_short.group(1))
                    else:
                        raise ValueError("Usage: `unstake jito 0.25` (amount in SOL)")

                    lamports = int(amt * 1_000_000_000)
                    payload = {"protocol": "jito", "amountLamports": lamports}

                    resp = requests.post(f"{EXECUTOR_URL}/unstake", json=payload, timeout=25)
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"status": resp.status_code, "text": (resp.text or "")[:500]}

                    if resp.status_code == 200 and data.get("txSignature"):
                        reply = f"✅ Unstaked **{amt:.6f} SOL** via *jito*. txSignature: `{data['txSignature']}`"
                    elif resp.status_code == 404:
                        reply = "⚠️ Executor does not expose `/unstake` yet."
                    else:
                        reply = "⚠️ Unstake failed:\n```" + json.dumps(data, indent=2) + "```"
                except Exception as e:
                    reply = f"Error unstaking: {e}"

                try:
                    requests.post(response_url, json={"response_type": "ephemeral", "text": reply}, timeout=10)
                except Exception:
                    pass

            threading.Thread(target=run_unstake, daemon=True).start()
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
                                  {"role":"user","content":user_text}]
                    )
                    reply = resp.choices[0].message.content
                else:
                    resp = openai.ChatCompletion.create(
                        model="gpt-5",
                        messages=[{"role":"system","content":system_prompt},
                                  {"role":"user","content":user_text}]
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

    # other events -> ignore
    return "ok", 200

# Slack interactive callback (kept for later)
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