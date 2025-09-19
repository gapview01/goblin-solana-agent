# telegram_service/server.py
import os, logging, inspect, math
import httpx
from httpx import HTTPStatusError  # for surfacing executor error bodies
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

# ---------- _exec_post: include executor error body in exceptions
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
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Surface the executor's response body so you see the reason in Telegram
            try:
                body = r.json()
            except Exception:
                body = r.text
            raise httpx.HTTPStatusError(f"{e} | body={body}", request=e.request, response=e.response)
        try:
            return r.json()
        except Exception:
            return {"ok": False, "raw": r.text}

# ----- pretty-print helpers for tokens/amounts -----
MINTS = {
    # SOL & USDC (mainnet)
    "So11111111111111111111111111111111111111112": {"symbol": "SOL",  "decimals": 9},
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"symbol": "USDC", "decimals": 6},
}
def mint_info(mint: str):
    return MINTS.get(mint, {"symbol": mint[:4] + "…", "decimals": 6})

def to_ui(amount_str: str | int | float, decimals: int) -> float:
    if amount_str is None:
        return 0.0
    if isinstance(amount_str, (int, float)):
        return float(amount_str) / (10 ** decimals)
    return int(str(amount_str)) / (10 ** decimals)

def fmt(n: float, dp: int = 6) -> str:
    s = f"{n:.{dp}f}"
    return s.rstrip("0").rstrip(".")

def solscan_url(sig: str) -> str:
    if not sig: return ""
    if NETWORK.lower() == "mainnet":
        return f"https://solscan.io/tx/{sig}"
    return f"https://solscan.io/tx/{sig}?cluster={NETWORK}"

def pull_sig(res: dict):
    return res.get("signature") or res.get("txid") or res.get("transactionId")

def summarize_swap_like(res: dict, fallback_in_sym: str, fallback_out_sym: str, slip_bps: int):
    in_mint  = res.get("inputMint")
    out_mint = res.get("outputMint")
    in_info  = mint_info(in_mint) if in_mint else {"symbol": fallback_in_sym,  "decimals": 9 if fallback_in_sym=="SOL" else 6}
    out_info = mint_info(out_mint) if out_mint else {"symbol": fallback_out_sym, "decimals": 9 if fallback_out_sym=="SOL" else 6}

    in_ui  = to_ui(res.get("inAmount"),  in_info["decimals"])
    out_ui = to_ui(res.get("outAmount"), out_info["decimals"])
    price  = (out_ui / in_ui) if in_ui else None

    route_labels = []
    for leg in (res.get("routePlan") or []):
        label = (leg.get("label") or leg.get("swapInfo", {}).get("label"))
        if label: route_labels.append(label)
    route = " → ".join(route_labels) if route_labels else None

    impact = res.get("priceImpactPct")
    impact_pct = f"{float(impact)*100:.2f}%" if impact is not None else None
    slip_pct = f"{(res.get('slippageBps') or slip_bps)/100:.2f}%"

    lines = []
    if in_ui and out_ui:
        lines.append(f"{fmt(in_ui)} {in_info['symbol']} → {fmt(out_ui)} {out_info['symbol']}")
    elif in_ui:
        lines.append(f"{fmt(in_ui)} {in_info['symbol']}")
    if price is not None:
        lines.append(f"Price: 1 {in_info['symbol']} ≈ {fmt(price,6)} {out_info['symbol']}")
    meta = f"Slippage: {slip_pct}"
    if impact_pct: meta += f" · Impact: {impact_pct}"
    lines.append(meta)
    if route: lines.append(f"Route: {route}")
    return "\n".join(lines)

# ---------- tolerant parsing + token aliases ----------
def _clean(tokens: list[str]) -> list[str]:
    """Drop filler words/arrows so 'SOL to USDC 0.2' also works."""
    drop = {"to", "TO", "->", "→", "=>"}
    return [t for t in tokens if t not in drop]

# Slack-style aliases -> canonical tickers used by routes/LSTs
_ALIASES = {
    # Base assets
    "SOL": "SOL", "USDC": "USDC",
    # Jito LST
    "JITO": "JITOSOL", "JITOSOL": "JITOSOL", "JITO-SOL": "JITOSOL", "JITO_SOL": "JITOSOL", "JITOSOLTOKEN": "JITOSOL",
    # Marinade LST (note: MNDE is governance; LST is MSOL)
    "MARINADE": "MSOL", "MSOL": "MSOL", "MARINADE-SOL": "MSOL", "MARINADE_SOL": "MSOL",
    # BlazeStake
    "BLAZE": "BSOL", "BSOL": "BSOL", "BLAZE-SOL": "BSOL", "BLAZE_SOL": "BSOL",
    # Socean
    "SOCEAN": "SCNSOL", "SCNSOL": "SCNSOL", "SOCEAN-SOL": "SCNSOL", "SOCEAN_SOL": "SCNSOL",
}
def _norm(sym: str) -> str:
    key = sym.replace(" ", "").replace("-", "").replace("_", "").upper()
    return _ALIASES.get(key, sym.upper())

# ---------- protocol & unit helpers for native stake endpoints ----------
def to_lamports(sol_amount: float) -> int:
    """Convert SOL (UI) to lamports."""
    return int(round(sol_amount * 1_000_000_000))

_PROTOCOLS = {
    "JITOSOL": "jito", "JITO": "jito",
    "MSOL": "marinade", "MARINADE": "marinade",
    "BSOL": "blaze", "BLAZE": "blaze",
    "SCNSOL": "socean", "SOCEAN": "socean",
}
def _proto(token: str) -> str:
    return _PROTOCOLS.get(token.upper(), token.lower())

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
        return await update.message.reply_text("🚫 Not allowed.")
    toks = _args(ctx)
    token = _norm(toks[0]) if toks else "SOL"
    payload = {"wallet": WALLET_ADDRESS, "token": token, "network": NETWORK}
    try:
        res = await _exec_post("balance", payload)
        # friendly formats
        if "sol" in res:
            ui = float(res["sol"]); extra = f" ({int(res.get('lamports', ui*1e9)):,} lamports)"
        elif "uiAmount" in res:
            ui = float(res["uiAmount"]); extra = ""
        elif "amount" in res and "decimals" in res:
            ui = int(res["amount"]) / (10**int(res["decimals"])); extra = ""
        else:
            ui = res.get("balance") or res.get("result") or res; extra = ""
        await update.message.reply_text(f"💰 Balance {token}: {fmt(float(ui))}{extra}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Balance failed: {e}")

async def quote_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _clean(_args(ctx))
    if len(argv) < 3:
        return await update.message.reply_text("Usage: /quote <FROM> <TO> <AMOUNT> [slippage_bps]\nExample: /quote SOL USDC 0.5 100")
    from_sym, to_sym = _norm(argv[0]), _norm(argv[1])
    try:
        amount = _parse_amount(argv[2])
    except Exception as e:
        return await update.message.reply_text(str(e))
    slip_bps = int(argv[3]) if len(argv) >= 4 else DEFAULT_SLIP_BPS
    payload = {"from": from_sym, "to": to_sym, "amount": str(amount), "slippage_bps": slip_bps, "network": NETWORK}
    try:
        res = await _exec_post("quote", payload)
        # Pretty summary
        in_mint, out_mint = res.get("inputMint"), res.get("outputMint")
        in_info  = mint_info(in_mint) if in_mint else {"symbol": from_sym, "decimals": 9 if from_sym=="SOL" else 6}
        out_info = mint_info(out_mint) if out_mint else {"symbol": to_sym,  "decimals": 9 if to_sym=="SOL"  else 6}
        in_ui  = to_ui(res.get("inAmount"),  in_info["decimals"]) or amount
        out_ui = to_ui(res.get("outAmount"), out_info["decimals"])
        price  = (out_ui / in_ui) if in_ui else None
        impact = res.get("priceImpactPct"); impact_pct = f"{float(impact)*100:.2f}%" if impact is not None else None
        slip_pct = f"{(res.get('slippageBps') or slip_bps)/100:.2f}%"

        lines = [f"🧮 Quote {fmt(in_ui)} {in_info['symbol']} → {out_info['symbol']}"]
        if out_ui: lines.append(f"Est. out: {fmt(out_ui)} {out_info['symbol']}")
        if price is not None: lines.append(f"Price: 1 {in_info['symbol']} ≈ {fmt(price,6)} {out_info['symbol']}")
        meta = f"Slippage: {slip_pct}"
        if impact_pct: meta += f" · Impact: {impact_pct}"
        lines.append(meta)

        # Route (optional)
        route_labels = []
        for leg in (res.get("routePlan") or []):
            label = (leg.get("label") or leg.get("swapInfo", {}).get("label"))
            if label: route_labels.append(label)
        if route_labels: lines.append("Route: " + " → ".join(route_labels))

        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Quote failed: {e}")

async def swap_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _clean(_args(ctx))
    if len(argv) < 3:
        return await update.message.reply_text("Usage:\n/swap <FROM> <TO> <AMOUNT> [slippage_bps]\nExample: /swap SOL USDC 0.5 100")
    from_sym, to_sym = _norm(argv[0]), _norm(argv[1])
    try:
        amount = _parse_amount(argv[2])
    except Exception as e:
        return await update.message.reply_text(str(e))
    slip_bps = int(argv[3]) if len(argv) >= 4 else DEFAULT_SLIP_BPS
    payload = {
        "from": from_sym, "to": to_sym, "amount": str(amount),
        "slippage_bps": slip_bps, "wallet": WALLET_ADDRESS, "network": NETWORK
    }
    try:
        res = await _exec_post("swap", payload)
        sig = pull_sig(res)
        summary = summarize_swap_like(res, from_sym, to_sym, slip_bps)
        msg = f"🔁 Swap sent ✅\n{summary}"
        if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Swap failed: {e}")

async def stake_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _args(ctx)
    if len(argv) < 2:
        return await update.message.reply_text("Usage:\n/stake <TOKEN> <AMOUNT>\nExample: /stake JITOSOL 1.0")
    token = _norm(argv[0])
    try:
        amount = _parse_amount(argv[1])
    except Exception as e:
        return await update.message.reply_text(str(e))

    # Native executor stake: needs protocol + amountLamports
    payload = {
        "protocol": _proto(token),                 # "jito" / "marinade" / "blaze" / "socean"
        "amountLamports": to_lamports(amount),     # integer lamports
        "wallet": WALLET_ADDRESS,
        "network": NETWORK,
    }
    try:
        res = await _exec_post("stake", payload)
        sig = pull_sig(res)
        msg = f"🪙 Staked {fmt(amount)} {token} ✅"
        if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
        return await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
    except HTTPStatusError as e:
        logging.exception("Stake HTTP error (native). Falling back to swap: %s", e)
        # Fallback: SOL -> LST swap
        try:
            res = await _exec_post("swap", {
                "from": "SOL", "to": token, "amount": str(amount),
                "slippage_bps": DEFAULT_SLIP_BPS, "wallet": WALLET_ADDRESS, "network": NETWORK
            })
            sig = pull_sig(res)
            summary = summarize_swap_like(res, "SOL", token, DEFAULT_SLIP_BPS)
            msg = f"🪙 Staked (via swap) ✅\n{summary}"
            if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
            return await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
        except Exception as ee:
            return await update.message.reply_text(f"⚠️ Stake failed: {ee}")
    except Exception as e:
        logging.exception("Stake unexpected error: %s", e)
        return await update.message.reply_text(f"⚠️ Stake failed: {e}")

async def unstake_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _args(ctx)
    if len(argv) < 2:
        return await update.message.reply_text("Usage:\n/unstake <TOKEN> <AMOUNT>\nExample: /unstake JITOSOL 0.5")
    token = _norm(argv[0])
    try:
        amount = _parse_amount(argv[1])
    except Exception as e:
        return await update.message.reply_text(str(e))

    payload = {
        "protocol": _proto(token),
        "amountLamports": to_lamports(amount),
        "wallet": WALLET_ADDRESS,
        "network": NETWORK,
    }
    try:
        res = await _exec_post("unstake", payload)
        sig = pull_sig(res)
        msg = f"🪙 Unstaked {fmt(amount)} {token} ✅"
        if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
        return await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
    except HTTPStatusError as e:
        logging.exception("Unstake HTTP error (native). Falling back to swap: %s", e)
        # Fallback: LST -> SOL swap
        try:
            res = await _exec_post("swap", {
                "from": token, "to": "SOL", "amount": str(amount),
                "slippage_bps": DEFAULT_SLIP_BPS, "wallet": WALLET_ADDRESS, "network": NETWORK
            })
            sig = pull_sig(res)
            summary = summarize_swap_like(res, token, "SOL", DEFAULT_SLIP_BPS)
            msg = f"🪙 Unstaked (via swap) ✅\n{summary}"
            if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
            return await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
        except Exception as ee:
            return await update.message.reply_text(f"⚠️ Unstake failed: {ee}")
    except Exception as e:
        logging.exception("Unstake unexpected error: %s", e)
        return await update.message.reply_text(f"⚠️ Unstake failed: {e}")

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
    logging.info("Planner wired? %s from %s", llm_plan is not None, getattr(llm_plan, "__module__", None))
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
