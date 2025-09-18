from openai import OpenAI

client = OpenAI()  # uses OPENAI_API_KEY from env

resp = client.chat.completions.create(
    model="gpt-5",                     # or gpt-5-mini / gpt-5-nano
    messages=[
        {"role":"system","content":"You are a concise planning assistant."},
        {"role":"user","content":"Give me a 5-step plan to grow 1 SOL to 10 SOL."}
    ],
    reasoning_effort="minimal",        # optional: minimal | medium | high
    verbosity="low"                    # optional: low | medium | high
)
print(resp.choices[0].message.content)
