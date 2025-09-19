import os, logging, requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# --- config ---
ALLOWED_USERS = {6149503319}  # your Telegram user id
REASONER_URL = os.getenv("REASONER_URL", "http://agent_reasoning:8000")
EXECUTOR_URL = os.getenv("EXECUTOR_URL", "http://executor_node:8000")  # placeholder if you add exec endpoints

# --- guards ---
async def _allowed(update: Update) -> bool:
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("Access denied.")
        return False
    return True

# --- handlers ---
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _allowed(update): return
    await update.message.reply_text(
        "GoblinBot ready.\n"
        "• Use /plan <goal>  (or just type) to ask the planner\n"
        "• /help for tips"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _allowed(update): return
    await update.message.reply_text(
        "Commands:\n"
        "/plan <goal> – Plan an action (e.g., swap, stake)\n"
        "Or just send a message and I’ll plan it."
    )

async def cmd_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _allowed(update): return
    q = " ".join(ctx.args) or (update.message.text or "").removeprefix("/plan").strip()
    if not q:
        await update.message.reply_text("Usage: /plan swap 1 SOL to USDC")
        return
    try:
        r = requests.post(f"{REASONER_URL}/plan", json={"query": q}, timeout=25)
        r.raise_for_status()
        reply = r.json().get("response") or str(r.json())
    except Exception as e:
        reply = f"Planner error: {e}"
    await update.message.reply_text(reply)

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # default route = plan whatever the user typed
    ctx.args = []
    await cmd_plan(update, ctx)

# --- bootstrap ---
def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    logging.basicConfig(level=logging.INFO)
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("plan",  cmd_plan))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("✅ Telegram bot started. Send /plan <goal> or just type.")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()