"""Simple OpenAI planner logic placeholder."""
import os
from openai import OpenAI


def plan(prompt: str) -> str:
    """Call OpenAI to generate a plan from a prompt."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.responses.create(model="gpt-4.1-mini", input=prompt)
    return response.output[0].content[0].text
