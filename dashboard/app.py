"""Streamlit dashboard for monitoring the Goblin agent."""

import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

# Minimal FastAPI app for Telegram Web App endpoints
fastapi_app = FastAPI()

HTML_TEMPLATE = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Goblin – Simulate Scenarios</title>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 16px; color: #e6e6e6; background: #0f0f13; }
      .card { background: #16161d; border-radius: 12px; padding: 16px; box-shadow: 0 8px 24px rgba(0,0,0,.35); }
      h1 { font-size: 18px; margin: 0 0 8px; }
      pre { white-space: pre-wrap; word-break: break-word; }
      .muted { color: #9aa0a6; font-size: 12px; }
      .bar { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
      .row { margin-top: 6px; }
      .btns { margin-top: 12px; display: grid; gap: 8px; grid-template-columns: repeat(2, 1fr); }
      button { background: #1f2937; color: #fff; border: 0; border-radius: 8px; padding: 10px 12px; cursor: pointer; }
      button.primary { background: #22c55e; color: #08130b; font-weight: 600; }
    </style>
    <script>
      async function load() {
        const urlParams = new URLSearchParams(window.location.search);
        const token = urlParams.get('token') || '';
        document.getElementById('goal').textContent = "Scenario Compare";
        try {
          const resp = await fetch('/telegram/simulate?token=' + encodeURIComponent(token));
          const data = await resp.json();
          const text = data.text || 'No data';
          document.getElementById('text').textContent = text;
        } catch (e) {
          document.getElementById('text').textContent = 'Failed to load simulation.';
        }
      }
      window.addEventListener('load', load);
    </script>
  </head>
  <body>
    <div class="card">
      <h1 id="goal">Scenario Compare</h1>
      <pre id="text" class="bar">Loading…</pre>
      <div class="muted">Bars scaled to today’s best outcome. Quotes expire; refresh if TTL shows 0s.</div>
      <div class="btns">
        <button class="primary" onclick="window.Telegram && Telegram.WebApp && Telegram.WebApp.close()">Approve ≤ micro</button>
        <button onclick="location.reload()">Refresh</button>
        <button onclick="history.back()">Edit Goal</button>
        <button onclick="window.Telegram && Telegram.WebApp && Telegram.WebApp.close()">Cancel</button>
      </div>
    </div>
  </body>
</html>
"""

@fastapi_app.get("/webapp/sim", response_class=HTMLResponse)
async def webapp_sim(_: Request):
    return HTML_TEMPLATE

@fastapi_app.get("/telegram/simulate")
async def proxy_simulate(token: str = ""):
    # Read the latest visual text from the Telegram service via its SIM cache
    # For now, call the same backend endpoint to force a recompute in case cache expired
    import httpx
    base = os.environ.get("TELEGRAM_INTERNAL_URL") or os.environ.get("WEBHOOK_BASE_URL")
    if not base:
        return {"ok": False, "text": "Service URL not configured."}
    try:
        # Ask the telegram-service to rebuild if needed by sending a no-op simulate on empty options
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=10.0)) as client:
            # First, try to fetch from a lightweight endpoint if it exists
            r = await client.get(f"{base}/webhook/{'health' if 'health' in base else ''}")
            _ = r.status_code  # ignore
    except Exception:
        pass
    # As we don't have a direct cache endpoint exposed publicly, return a friendly placeholder
    # The Telegram callback path already pushes the visual into chat; this endpoint supplies WebApp text only.
    return {"ok": True, "text": "If this is empty, press the Simulate button again to refresh the session token."}

# --- Streamlit dashboard (kept for local runs) ---
if __name__ == "__main__":
    import streamlit as st
    from planner.planner import plan
    from wallet.agent_wallet import get_balance

    def main() -> None:
        st.title("Goblin Agent Dashboard")
        try:
            balance = get_balance()
        except Exception as exc:  # pragma: no cover - runtime connectivity
            balance = 0.0
            st.error(f"Failed to fetch balance: {exc}")
        st.metric("Current SOL Balance", f"{balance:.4f} SOL")

        start, target = 1.0, 10.0
        progress = (balance - start) / (target - start)
        progress = max(0.0, min(progress, 1.0))
        st.progress(progress)
        st.caption(f"Progress to {target} SOL goal")

        st.subheader("Next Action Plan")
        try:
            next_plan = plan("Grow safely")
            st.write(next_plan)
        except Exception as exc:  # pragma: no cover
            st.error(f"Failed to generate plan: {exc}")

    main()
