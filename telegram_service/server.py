# minimal Telegram bot: only /start, serves /webhook
import os, logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "hook")
BASE_URL = (os.getenv("BASE_URL") or "").rstrip("/")

app = Application.builder().token(TOKEN).build()

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("GoblinBot ready ✅")

app.add_handler(CommandHandler("start", start))

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