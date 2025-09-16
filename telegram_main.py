from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import requests

ALLOWED_USERS = {6149503319}  # Your Telegram user ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("Access denied.")
        return
    await update.message.reply_text("Welcome to GoblinBot 👾")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("Access denied.")
        return

    user_input = update.message.text

    # Call your planner here
    planning_response = requests.post(
        "http://agent_reasoning:8000/plan",
        json={"query": user_input}
    ).json()

    await update.message.reply_text(planning_response.get("response", "No response"))

if __name__ == '__main__':
    app = ApplicationBuilder().token("YOUR_BOT_TOKEN").build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()