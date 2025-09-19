# planner/planner.py
import os
from openai import OpenAI
from openai import APIConnectionError, AuthenticationError, OpenAIError

API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
PROJECT = (os.getenv("OPENAI_PROJECT") or "").strip()
ORG     = (os.getenv("OPENAI_ORG") or "").strip() or None

client = OpenAI(
    api_key=API_KEY,
    project=PROJECT or None,   # required for sk-proj- keys
    organization=ORG           # optional
)

SYSTEM_PROMPT = (
    "You are Goblin Planner, a concise DeFi/crypto planning assistant. "
    "Produce a short, actionable plan with numbered steps. "
    "If making assumptions, state them. Keep it under 10 lines."
)

def plan(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "Please provide a goal, e.g., /plan grow 1 SOL → 10 SOL."

    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": q},
            ],
            temperature=0.3,
            timeout=30,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or "No response generated."
    except AuthenticationError:
        return ("Planner error: Authentication failed. "
                "Check OPENAI_API_KEY and, for sk-proj keys, OPENAI_PROJECT=proj_…")
    except APIConnectionError:
        return "Planner error: Connection error reaching OpenAI. Try again."
    except OpenAIError as e:
        return f"Planner error: {e}"
    except Exception as e:
        return f"Planner error (unexpected): {e}"
      
client = OpenAI()  # reads OPENAI_API_KEY
MODEL = os.getenv("OPENAI_MODEL", "gpt-5")  # or gpt-5-mini

def plan(prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a concise planning assistant."},
            {"role": "user", "content": prompt},
        ],
        reasoning_effort="minimal",   # ok; remove if SDK complains
        # verbosity="low",            # comment out if you get a param error
    )
    return (resp.choices[0].message.content or "").strip()