# planner/planner.py
import os
from openai import OpenAI

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