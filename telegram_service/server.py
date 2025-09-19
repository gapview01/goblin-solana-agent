# telegram_service/server.py
import os, logging, inspect, math
import httpx
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# ---------- basic config (env)
TOKEN          = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
BASE_URL       = (os.getenv("BASE_URL") or "").strip().rstrip("/")
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "hook").strip()
PORT           = int(os.getenv("PORT") or 8080)
LOG_LEVEL      = (os.getenv("LOG_LEVEL") or "INFO").upper()

# Executor (backend) config
EXECUTOR_URL      = (os.getenv("EXECUTOR_URL") or "").rstrip("/")
EXECUTOR_TOKEN    = (os.getenv("EXECUTOR_TOKEN") or "").strip()        # optional: static Bearer token
WALLET_ADDRESS    = (os.getenv("WALLET_ADDRESS") or "").strip()        # your agent wallet public key
NETWORK           = (os.getenv("NETWORK") or "mainnet").strip()        # e.g. mainnet
DEFAULT_SLIP_BPS  = int(os.getenv("DEFAULT_SLIPPAGE_BPS") or "100")    # 100 bps = 1%
ALLOWED_USER_IDS  = {u.strip() for u in (os.getenv("ALLOWED_TELEGRAM_USER_IDS") or "").split(",") if u.strip()}  # optional allow list

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# --- Planner import (same simple planner as Slack)
try:
    from planner.planner import plan as llm_plan
    logging.info("Planner selected: simple (planner.planner)")
except Exception as e:
    logging.exception("Simple planner import failed; using demo fallback: %s", e)
    llm_plan = None  # demo mode fallback

# ---------- self-heal webhook (runs once at startup)
async def reconcile_webhook(app: Application):
    """Ensure Telegram's webhook points to THIS service."""
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

# Build the bot app
app = (
    Application.builder()
    .token(TOKEN)
    .post_init(reconcile_webhook)
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

def _is_allowed(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    uid = str(getattr(update.effective_user, "id", ""))
    return uid in ALLOWED_USER_IDS

def _args(ctx: ContextTypes.DEFAULT_TYPE) -> list[str]:
    if getattr(ctx, "args", None):
        return list(ctx.args)
    text = getattr(getattr(ctx, "update", None), "message", None)
    text = (text.text if text else "") or ""
    return text.split()[1:]

def _parse_amount(s: str) -> float:
    try:
        v = float(s)
        if not math.isfinite(v) or v <= 0:
            raise ValueError
        return v
    except Exception:
        raise ValueError("Amount must be a positive number, e.g. 0.5")

async def _exec_post(path: str, payload: dict) -> dict:
    if not EXECUTOR_URL:
        raise RuntimeError("EXECUTOR_URL not set")
    url = f"{EXECUTOR_URL}/{path.lstrip('/')}"
    headers = {"Content-Type": "application/json"}
    if EXECUTOR_TOKEN:
        headers["Authorization"] = f"Bearer {EXECUTOR_TOKEN}"
    timeout = httpx.Timeout(20.0, read=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"ok": False, "raw": r.text}

# ---------- handlers (basic)
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("START command")
    await update.message.reply_text(
        "GoblinBot ready ✅\n\n"
        "Use /plan <goal>\nExample: /plan grow 1 SOL to 10 SOL\n\n"
        "Exec:\n"
        "/balance [TOKEN]\n"
        "/quote <FROM> <TO> <AMOUNT> [slip_bps]\n"
        "/swap <FROM> <TO> <AMOUNT> [slip_bps]\n"
        "/stake <TOKEN> <AMOUNT>\n"
        "/unstake <TOKEN> <AMOUNT>"
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

# ---------- executor command handlers
async def balance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text("🚫 Not allowed."); return
    token = (_args(ctx)[0] if _args(ctx) else "SOL").upper()
    payload = {"wallet": WALLET_ADDRESS, "token": token, "network": NETWORK}
    try:
        res = await _exec_post("balance", payload)
        amt = res.get("balance") or res.get("result") or res
        await update.message.reply_text(f"💰 Balance {token}: {amt}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Balance failed: {e}")

async def quote_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text("🚫 Not allowed."); return
    argv = _args(ctx)
    if len(argv) < 3:
        await update.message.reply_text("Usage: /quote <FROM> <TO> <AMOUNT> [slippage_bps]\nExample: /quote SOL USDC 0.5 100")
        return
    from_sym, to_sym = argv[0].upper(), argv[1].upper()
    try:
        amount = _parse_amount(argv[2])
    except Exception as e:
        await update.message.reply_text(str(e)); return
    slip_bps = int(argv[3]) if len(argv) >= 4 else DEFAULT_SLIP_BPS
    payload = {"from": from_sym, "to": to_sym, "amount": str(amount), "slippage_bps": slip_bps, "network": NETWORK}
    try:
        res = await _exec_post("quote", payload)
        out = res.get("out") or res.get("amount_out") or res
        px  = res.get("price")
        txt = f"🧮 Quote {amount} {from_sym} → {to_sym}\n"
        if px:  txt += f"Price: {px}\n"
        if out: txt += f"Estimated out: {out} {to_sym}"
        await update.message.reply_text(txt.strip())
    except Exception as e:
        await update.message.reply_text(f"⚠️ Quote failed: {e}")

async def swap_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text("🚫 Not allowed."); return
    argv = _args(ctx)
    if len(argv) < 3:
        await update.message.reply_text("Usage: /swap <FROM> <TO> <AMOUNT> [slippage_bps]\nExample: /swap SOL USDC 0.5 100")
        return
    from_sym, to_sym = argv[0].upper(), argv[1].upper()
    try:
        amount = _parse_amount(argv[2])
    except Exception as e:
        await update.message.reply_text(str(e)); return
    slip_bps = int(argv[3]) if len(argv) >= 4 else DEFAULT_SLIP_BPS
    payload = {
        "from": from_sym, "to": to_sym, "amount": str(amount),
        "slippage_bps": slip_bps, "wallet": WALLET_ADDRESS, "network": NETWORK
    }
    try:
        res = await _exec_post("swap", payload)
        sig = res.get("signature") or res.get("txid") or res
        await update.message.reply_text(f"🔁 Swap sent ✅\nTx: `{sig}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Swap failed: {e}")

async def stake_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text("🚫 Not allowed."); return
    argv = _args(ctx)
    if len(argv) < 2:
        await update.message.reply_text("Usage: /stake <TOKEN> <AMOUNT>\nExample: /stake JITOSOL 1.0")
        return
    token = argv[0].upper()
    try:
        amount = _parse_amount(argv[1])
    except Exception as e:
        await update.message.reply_text(str(e)); return
    payload = {"token": token, "amount": str(amount), "wallet": WALLET_ADDRESS, "network": NETWORK}
    try:
        res = await _exec_post("stake", payload)
        sig = res.get("signature") or res.get("txid") or res
        await update.message.reply_text(f"🪙 Stake sent ✅\nTx: `{sig}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Stake failed: {e}")

async def unstake_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        await update.message.reply_text("🚫 Not allowed."); return
    argv = _args(ctx)
    if len(argv) < 2:
        await update.message.reply_text("Usage: /unstake <TOKEN> <AMOUNT>\nExample: /unstake JITOSOL 0.5")
        return
    token = argv[0].upper()
    try:
        amount = _parse_amount(argv[1])
    except Exception as e:
        await update.message.reply_text(str(e)); return
    payload = {"token": token, "amount": str(amount), "wallet": WALLET_ADDRESS, "network": NETWORK}
    try:
        res = await _exec_post("unstake", payload)
        sig = res.get("signature") or res.get("txid") or res
        await update.message.reply_text(f"🪙 Unstake sent ✅\nTx: `{sig}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Unstake failed: {e}")

def add_handlers(a: Application):
    a.add_handler(CommandHandler("start", start))
    a.add_handler(CommandHandler("ping", ping))
    a.add_handler(CommandHandler("plan", plan_cmd))

    # executor commands
    a.add_handler(CommandHandler("balance", balance_cmd))
    a.add_handler(CommandHandler("quote",   quote_cmd))
    a.add_handler(CommandHandler("swap",    swap_cmd))
    a.add_handler(CommandHandler("stake",   stake_cmd))
    a.add_handler(CommandHandler("unstake", unstake_cmd))

    a.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

# ---------- run (blocking; PTB manages the event loop)
def main():
    add_handlers(app)
    logging.info(
        "Planner wired? %s from %s",
        llm_plan is not None, getattr(llm_plan, "__module__", None)
    )
    if BASE_URL:
        url_path = f"webhook/{WEBHOOK_SECRET}"
        webhook_url = f"{BASE_URL}/{url_path}"
        logging.info("Starting webhook server at %s", webhook_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=url_path,
            webhook_url=webhook_url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
    else:
        logging.info("BASE_URL not set -> polling mode (not suitable for Cloud Run)")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
