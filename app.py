from flask import Flask, request, jsonify
import os
import threading
import requests

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

@app.route("/ping")
def ping():
    return "pong", 200

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