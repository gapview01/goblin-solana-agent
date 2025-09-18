# minimal Telegram bot: only /start, serves /webhook
import os, logging
import asyncio  # NEW
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from planner.planner import plan as llm_plan  # NEW

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "hook")
BASE_URL = (os.getenv("BASE_URL") or "").rstrip("/")

app = Application.builder().token(TOKEN).build()

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("GoblinBot ready ✅")

# NEW: /plan handler (calls your planner.planner.plan)
async def plan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # accept "/plan grow 1 SOL to 10 SOL"
    prompt = " ".join(ctx.args).strip()
    if not prompt:
        # handle someone typing "/plan" then text on same line
        text = (update.message.text or "")
        prompt = text.replace("/plan", "", 1).strip()
    if not prompt:
        await update.message.reply_text("Usage: /plan <your goal>\nExample: /plan grow 1 SOL to 10 SOL")
        return

    await update.message.reply_text("_thinking…_")
    try:
        # run the LLM call off the event loop so the bot stays responsive
        reply = await asyncio.to_thread(llm_plan, prompt)
    except Exception as e:
        await update.message.reply_text(f"Planner error: {e}")
        return

    await update.message.reply_text((reply or "").strip()[:4000])

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("plan", plan_cmd))  # NEW

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", "8080"))
    webhook_url = f"{BASE_URL}/webhook" if BASE_URL.startswith("https://") else None
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="webhook",          # EXACTLY /webhook
        webhook_url=webhook_url,     # ok if None; we also set via Bot API
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
    )