# telegram_service/server.py
import os, logging, inspect
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# --- Planner import (shared with Slack)
try:
    from planner.planner import plan as llm_plan
except Exception:
    llm_plan = None  # keep bot alive even if planner isn't wired

# --- Env
TOKEN          = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
BASE_URL       = (os.getenv("BASE_URL") or "").strip().rstrip("/")   # e.g. https://...run.app
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "hook").strip()
PORT           = int(os.getenv("PORT") or 8080)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ---------- self-heal webhook (runs once at startup)
async def reconcile_webhook(app: Application):
    """
    Ensure Telegram's webhook points to THIS service. If it's blank or wrong,
    set it. Never crash the container if this fails.
    """
    try:
        if not BASE_URL:
            logging.info("Webhook reconcile: BASE_URL unset; skipping")
            return
        expected = f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"
        info = await app.bot.get_webhook_info()
        current = info.url or ""
        if current != expected:
            await app.bot.set_webhook(
                url=expected,
                secret_token=WEBHOOK_SECRET,
                drop_pending_updates=True,
            )
            logging.info("Webhook reconciled: %r -> %r", current, expected)
        else:
            logging.info("Webhook already correct: %r", expected)
    except Exception:
        logging.exception("Webhook reconcile failed; continuing anyway")

# Build the app and attach the reconcile hook
app = (
    Application.builder()
    .token(TOKEN)
    .post_init(reconcile_webhook)   # <- self-heal on startup
    .build()
)

# ---------- helpers
async def _call_planner(goal: str) -> str:
    if not llm_plan:
        return f"(demo) Plan for: {goal}"
    try:
        sig = inspect.signature(llm_plan)
        try:
            res = llm_plan(goal, source="telegram") if "source" in sig.parameters else llm_plan(goal)
        except TypeError:
            try:
                res = llm_plan(text=goal, source="telegram") if "text" in sig.parameters else llm_plan(text=goal)
            except TypeError:
                res = llm_plan(goal)
        if inspect.isawaitable(res):
            res = await res
        return str(res)
    except Exception:
        logging.exception("planner.plan crashed")
        return "⚠️ Planner error. Check Cloud Run logs."

# ---------- handlers
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("START command")
    await update.message.reply_text(
        "GoblinBot ready ✅\n\nUse /plan <goal>\nExample: /plan grow 1 SOL to 10 SOL"
    )

async def ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("PING command")
    await update.message.reply_text("pong")

async def plan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text or ""
    goal = raw.partition(" ")[2].strip() or " ".join(ctx.args).strip()
    if not goal:
        await update.message.reply_text("Usage: /plan <goal>\nExample: /plan grow 1 SOL to 10 SOL")
        return
    try:
        await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    except Exception:
        pass
    logging.info("PLAN request user=%s goal=%r",
                 (update.effective_user.id if update.effective_user else "unknown"), goal)
    plan_text = await _call_planner(goal)
    await update.message.reply_text(plan_text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

async def unknown_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("UNKNOWN command text=%r", getattr(update.message, "text", ""))
    await update.message.reply_text("Unknown command. Try /plan <goal>.")

def add_handlers(a: Application):
    a.add_handler(CommandHandler("start", start))
    a.add_handler(CommandHandler("ping", ping))
    a.add_handler(CommandHandler("plan", plan_cmd))
    a.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

# ---------- run (blocking; PTB manages the event loop)
def main():
    add_handlers(app)
    if BASE_URL:
        url_path = f"webhook/{WEBHOOK_SECRET}"
        webhook_url = f"{BASE_URL}/{url_path}"
        logging.info("Starting webhook server at %s", webhook_url)
        # NOTE: Do NOT await this and do NOT wrap in asyncio.run.
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=url_path,
            webhook_url=webhook_url,          # PTB sets Telegram's webhook internally
            secret_token=WEBHOOK_SECRET,      # Telegram will send this header
            drop_pending_updates=True,
        )
    else:
        logging.info("BASE_URL not set -> polling mode (not suitable for Cloud Run)")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
