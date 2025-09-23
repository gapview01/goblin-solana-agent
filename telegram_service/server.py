# telegram_service/server.py
import os, logging, inspect, math, html, json, time, re, asyncio, uuid, base64, io
import httpx
from httpx import HTTPStatusError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from typing import Any, Dict, Optional

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore
except Exception:  # pragma: no cover - matplotlib optional at runtime
    matplotlib = None  # type: ignore
    plt = None  # type: ignore

# --- import path guard (ensure `planner/` is importable regardless of CWD)
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]  # repo root (one level above telegram_service)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from planner.exec_ready_planner import build_exec_ready_plan

# ---------- basic config (env)
TOKEN             = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
WEBHOOK_BASE_URL  = (os.getenv("WEBHOOK_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
WEBHOOK_SECRET    = (os.getenv("WEBHOOK_SECRET") or "hook").strip()
PORT              = int(os.getenv("PORT") or 8080)
LOG_LEVEL         = (os.getenv("LOG_LEVEL") or "INFO").upper()
SHOW_PLAN_JSON    = (os.getenv("SHOW_PLAN_JSON") or "0").lower() in ("1", "true", "yes")

# Planner selection: "llm" (default) prefers planner/llm_planner.py; set to "legacy" for planner/planner.py
PLANNER_IMPL   = (os.getenv("PLANNER_IMPL") or "llm").lower()
REQUIRE_LLM    = (os.getenv("REQUIRE_LLM_PLANNER") or "0").lower() in ("1", "true", "yes")

# Executor (backend) config
EXECUTOR_BASE_URL = (os.getenv("BASE_URL") or os.getenv("EXECUTOR_URL") or "").rstrip("/")
EXECUTOR_TOKEN    = (os.getenv("EXECUTOR_TOKEN") or "").strip()
WALLET_ADDRESS    = (os.getenv("WALLET_ADDRESS") or "").strip()
NETWORK           = (os.getenv("NETWORK") or "mainnet").strip()
DEFAULT_SLIP_BPS  = int(os.getenv("DEFAULT_SLIPPAGE_BPS") or "100")
ALLOWED_USER_IDS  = {u.strip() for u in (os.getenv("ALLOWED_TELEGRAM_USER_IDS") or "").split(",") if u.strip()}

# Planner policy overrides (allow memecoins + optional mcap hint)
MIN_TOKEN_MCAP_USD = float(os.getenv("MIN_TOKEN_MCAP_USD") or "15000000")  # planner hint only; enforce at execution if desired

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ---------- goblin-brand error formatter ----------
def _goblin_error_message(err: HTTPStatusError | Exception) -> str:
    try:
        if isinstance(err, HTTPStatusError) and err.response is not None:
            data = err.response.json()
            if isinstance(data, dict) and not data.get("ok", True):
                code = str(data.get("code") or "").upper()
                d = data.get("details") or {}
                sug = data.get("suggestion") or ""
                def _fmt(x, dp=3):
                    try:
                        return (f"{float(x):.{dp}f}").rstrip("0").rstrip(".")
                    except Exception:
                        return str(x)
                if code == "INSUFFICIENT_SOL":
                    have = _fmt(d.get("have_sol"))
                    need = _fmt(d.get("need_sol"))
                    try_amt = _fmt(d.get("try_sol"))
                    msg = f"⚠️ Not enough SOL, goblin. Need {need} incl. fees; you’ve got {have}."
                    tip = f"\nTry: {try_amt} or top up." if try_amt else (f"\nTip: {sug}" if sug else "")
                    return (msg + tip).strip()
                if code == "INVALID_AMOUNT":
                    ex = _fmt(d.get("example_sol") or 0.05)
                    return f"⚠️ That amount is cursed.\nUse a positive number like {ex}."
                if code == "ROUTE_NOT_FOUND":
                    fr, to = d.get("from"), d.get("to")
                    extra = f" {fr} → {to}" if fr and to else ""
                    tip = f"\nTip: {sug}" if sug else "\nTry: smaller size or a more liquid token."
                    return f"⚠️ Liquidity void:{extra}.{tip}"
                if code == "PRICE_IMPACT_TOO_HIGH":
                    impact, cap = d.get("impact_pct"), d.get("cap_pct")
                    return f"⚠️ Route too spicy ({impact} > {cap}).\nTry: trim size or re‑quote."
                if code in {"SWAP_FAILED", "UNEXPECTED"}:
                    why = (d.get("short_reason") or "swap failed")
                    tip = f"\nTip: {sug}" if sug else "\nTry: smaller size or re‑quote."
                    return f"⚠️ Sim says no: {why}.{tip}"
                if code == "STAKE_PROTOCOL_UNSUPPORTED":
                    return "⚠️ Only Jito staking for now.\nUse JITOSOL."
                # default fallback if code unrecognized
                base = data.get("user_message") or str(err)
                if sug:
                    base += f"\nTip: {sug}"
                return base
    except Exception:
        pass
    return f"⚠️ {err}"

# --- tiny in-memory plan cache per chat (for option CTAs) ---
_LAST_PLAN: dict[int, dict] = {}   # chat_id -> plan dict

# --- simulation token cache (token -> plan, TTL 600s) ---
_SIM_CACHE_TTL = 600.0
_SIM_CACHE: Dict[str, Dict[str, Any]] = {}

def _sim_cache_put(token: str, plan: Dict[str, Any]) -> None:
    now = time.time()
    _SIM_CACHE[token] = {"plan": plan, "ts": now}
    stale = [key for key, meta in list(_SIM_CACHE.items()) if now - float(meta.get("ts", 0.0)) > _SIM_CACHE_TTL]
    for key in stale:
        _SIM_CACHE.pop(key, None)

def _sim_cache_get(token: str) -> Optional[Dict[str, Any]]:
    entry = _SIM_CACHE.get(token)
    if not entry:
        return None
    if time.time() - float(entry.get("ts", 0.0)) > _SIM_CACHE_TTL:
        _SIM_CACHE.pop(token, None)
        return None
    plan = entry.get("plan")
    return plan if isinstance(plan, dict) else None

# --- Planner import (prefer llm_planner, fallback to legacy, then demo)
llm_plan = None
try:
    if PLANNER_IMPL in ("llm", "llm_planner", "llmplanner"):
        try:
            from planner.llm_planner import plan as llm_plan  # type: ignore
            logging.info("Planner selected: llm_planner (planner.llm_planner)")
        except ImportError:
            from planner.llmplanner import plan as llm_plan  # type: ignore
            logging.info("Planner selected: llmplanner (planner.llmplanner)")
    else:
        from planner.planner import plan as llm_plan  # type: ignore
        logging.info("Planner selected: legacy (planner.planner)")
except Exception as e:
    logging.exception("Planner import failed; attempting legacy fallback: %s", e)
    try:
        from planner.planner import plan as llm_plan  # type: ignore
        logging.info("Planner fallback selected: legacy (planner.planner)")
    except Exception as e2:
        logging.exception("Legacy planner import failed; using demo fallback: %s", e2)
        if REQUIRE_LLM:
            raise
        llm_plan = None  # demo mode

# ---------- self-heal webhook (runs once at startup)
async def reconcile_webhook(app: Application):
    try:
        if not WEBHOOK_BASE_URL:
            logging.info("Webhook reconcile: WEBHOOK_BASE_URL unset; skipping")
            return
        expected = f"{WEBHOOK_BASE_URL}/webhook/{WEBHOOK_SECRET}"
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
    except Exception as err:
        logging.exception("Webhook reconcile failed; continuing anyway: %s", err)

# Build the bot app
app = (
    Application.builder()
    .token(TOKEN)
    .post_init(reconcile_webhook)
    .build()
)

# ---------- helpers

def _fmt_list_or_str(x) -> str:
    """Render a list/tuple/set as 'a, b, c', return string as-is, else ''."""
    if isinstance(x, (list, tuple, set)):
        return ", ".join(str(i) for i in x if i is not None)
    if isinstance(x, str):
        return x
    return ""

async def _call_planner(goal: str) -> tuple[str, Dict[str, Any]]:
    """Call the planner and echo the raw JSON string alongside wallet state."""

    sol = 0.0
    lamports = 0
    if WALLET_ADDRESS:
        try:
            bal = await _exec_post("balance", {"wallet": WALLET_ADDRESS, "token": "SOL", "network": NETWORK})
            if isinstance(bal, dict):
                if "sol" in bal:
                    sol = float(bal.get("sol") or 0.0)
                elif "uiAmount" in bal:
                    sol = float(bal.get("uiAmount") or 0.0)
                elif "lamports" in bal:
                    lamports = int(bal.get("lamports") or 0)
                    sol = lamports / 1_000_000_000
                lamports = int(bal.get("lamports") or lamports or int(sol * 1_000_000_000))
            else:
                sol = float(bal or 0.0)
        except Exception:
            logging.debug("Wallet balance lookup failed", exc_info=True)
    if not lamports:
        lamports = int(sol * 1_000_000_000)
    wallet_state: Dict[str, Any] = {"sol_balance": float(sol), "lamports": int(lamports)}

    def _fallback_plan() -> dict:
        return {
            "summary": f"Demo plan for {goal}",
            "understanding": {"goal_rewrite": goal},
            "options": [
                {"name": "Conservative", "strategy": "Hold SOL and monitor market", "plan": []},
                {"name": "Standard", "strategy": "Blend SOL with liquid staking for yield", "plan": []},
                {"name": "Aggressive", "strategy": "Deploy into higher beta swaps when liquidity allows", "plan": []},
            ],
            "actions": [],
            "risks": ["Market volatility", "Execution risk"],
            "simulation": {"horizon_days": 30},
            "baseline": {"name": "Hold SOL"},
            "sizing": {"desired_sol": 0.1},
            "frame": "CSA",
        }

    if not llm_plan:
        return json.dumps(_fallback_plan()), wallet_state

    try:
        sig = inspect.signature(llm_plan)
        kwargs: Dict[str, Any] = {}

        if "wallet_state" in sig.parameters:
            kwargs["wallet_state"] = {"sol_balance": sol}

        policy_override = {"allowed_tokens": ["*"], "min_token_mcap_usd": MIN_TOKEN_MCAP_USD}
        if "policy" in sig.parameters:
            kwargs["policy"] = policy_override

        if "source" in sig.parameters:
            kwargs["source"] = "telegram"

        if "goal" in sig.parameters and ("text" not in sig.parameters):
            res = llm_plan(goal, **kwargs)
        else:
            kwargs["text"] = goal
            res = llm_plan(**kwargs)

        if inspect.isawaitable(res):
            res = await res
        if isinstance(res, (dict, list)):
            return json.dumps(res), wallet_state
        return str(res), wallet_state
    except Exception:
        logging.exception("planner.plan crashed")
        return json.dumps(_fallback_plan()), wallet_state

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
    except Exception as err:
        raise ValueError("Amount must be a positive number, e.g. 0.5") from err

# ---------- tolerant parsing + token aliases ----------
def _clean(tokens: list[str]) -> list[str]:
    drop = {"to", "TO", "->", "→", "=>"}
    return [t for t in tokens if t not in drop]

_ALIASES = {
    "SOL": "SOL", "USDC": "USDC",
    "JITO": "JITOSOL", "JITOSOL": "JITOSOL",
    "MARINADE": "MSOL", "MSOL": "MSOL",
    "BLAZE": "BSOL", "BSOL": "BSOL",
    "SOCEAN": "SCNSOL", "SCNSOL": "SCNSOL",
}
def _norm(sym: str) -> str:
    key = sym.replace(" ", "").replace("-", "").replace("_", "").upper()
    return _ALIASES.get(key, sym.upper())

# --- extra symbol normalizer to catch common typos from LLM (e.g., JIT0SOL)
def _normalize_symbol_guess(sym: str) -> str:
    s = (sym or "").upper().replace("-", "").replace("_", "")
    s = s.replace("0", "O").replace("1", "I")
    if s.startswith("JITO") and s.endswith("SOL"):
        s = "JITOSOL"
    return _ALIASES.get(s, s)

# ---------- payload builders (includes wallet + both slippage keys)
def _quote_payload(ins: str, outs: str, amount: str | float, slip_bps: int) -> dict:
    return {
        "from": ins,
        "to": outs,
        "amount": str(amount),
        "slippage_bps": int(slip_bps),
        "slippageBps": int(slip_bps),
        "network": NETWORK,
        "wallet": WALLET_ADDRESS,
    }

def _swap_payload(ins: str, outs: str, amount: str | float, slip_bps: int) -> dict:
    return {
        "from": ins,
        "to": outs,
        "amount": str(amount),
        "slippage_bps": int(slip_bps),
        "slippageBps": int(slip_bps),
        "wallet": WALLET_ADDRESS,
        "network": NETWORK,
    }

# ---------- JUP tokenlist resolver ----------
_TOKENLIST_CACHE = None
_TOKENLIST_TS = 0
_JUP_URL = "https://token.jup.ag/all"

def _looks_like_mint(s: str) -> bool:
    return isinstance(s, str) and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", s or "") is not None

async def _jup_tokenlist() -> list:
    global _TOKENLIST_CACHE, _TOKENLIST_TS
    now = time.time()
    if not _TOKENLIST_CACHE or (now - _TOKENLIST_TS) > 600:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=20.0)) as client:
            r = await client.get(_JUP_URL)
            r.raise_for_status()
            data = r.json()
            _TOKENLIST_CACHE = data if isinstance(data, list) else []
            _TOKENLIST_TS = now
    return _TOKENLIST_CACHE

async def _resolve_mint_by_symbol(symbol: str) -> str | None:
    if _looks_like_mint(symbol):
        return symbol
    sym = _normalize_symbol_guess(symbol)
    if not sym:
        return None
    if sym == "SOL":
        return "So11111111111111111111111111111111111111112"
    if sym == "USDC":
        return "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    tokens = await _jup_tokenlist()
    for t in tokens:
        if (t.get("symbol") or "").upper() == sym:
            return t.get("address")
    for t in tokens:
        if sym in (t.get("symbol") or "").upper():
            return t.get("address")
    return None

_EXECUTOR_MINTED = {"SOL", "USDC"}

async def _quote_payload_indexjs(from_sym: str, to_sym: str, amount_ui: str | float, slip_bps: int) -> dict:
    f_raw = _normalize_symbol_guess(from_sym)
    t_raw = _normalize_symbol_guess(to_sym)
    from_value = f_raw if f_raw in _EXECUTOR_MINTED else (await _resolve_mint_by_symbol(f_raw)) or f_raw
    to_value   = t_raw if t_raw in _EXECUTOR_MINTED else (await _resolve_mint_by_symbol(t_raw)) or t_raw
    return {
        "from": from_value,
        "to": to_value,
        "amount": str(amount_ui),
        "slippageBps": int(slip_bps),
    }

# ---------- _exec_post: debug + fail-fast + single quick retry
async def _exec_post(path: str, payload: dict) -> dict:
    if not EXECUTOR_BASE_URL:
        raise RuntimeError("EXECUTOR_BASE_URL not set")
    url = f"{EXECUTOR_BASE_URL}/{path.lstrip('/')}"
    headers = {"Content-Type": "application/json"}
    if EXECUTOR_TOKEN:
        headers["Authorization"] = f"Bearer {EXECUTOR_TOKEN}"

    timeout = httpx.Timeout(20.0, read=12.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        logging.debug("POST %s payload=%s", url, payload)
        try:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            logging.warning("Exec POST timed out, retrying once quickly…")
            r = await client.post(url, headers=headers, json=payload, timeout=httpx.Timeout(10.0, read=8.0))
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.json()
            except Exception:
                body = e.response.text
            raise httpx.HTTPStatusError(f"{e} | body={body}", request=e.request, response=e.response) from e

        try:
            return r.json()
        except Exception:
            return {"ok": False, "raw": r.text}

# ----- pretty-print helpers for tokens/amounts -----
MINTS = {
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
    if not sig:
        return ""
    if NETWORK.lower() == "mainnet":
        return f"https://solscan.io/tx/{sig}"
    return f"https://solscan.io/tx/{sig}?cluster={NETWORK}"

def pull_sig(res: dict):
    return res.get("signature") or res.get("txid") or res.get("transactionId")

def summarize_swap_like(res: dict, fallback_in_sym: str, fallback_out_sym: str, slip_bps: int):
    in_mint  = res.get("inputMint")
    out_mint = res.get("outputMint")
    in_info  = mint_info(in_mint) if in_mint else {"symbol": fallback_in_sym,  "decimals": 9 if fallback_in_sym == "SOL" else 6}
    out_info = mint_info(out_mint) if out_mint else {"symbol": fallback_out_sym, "decimals": 9 if fallback_out_sym == "SOL" else 6}

    in_ui  = to_ui(res.get("inAmount"),  in_info["decimals"])
    out_ui = to_ui(res.get("outAmount"), out_info["decimals"])
    price  = (out_ui / in_ui) if in_ui else None

    route_labels = []
    for leg in (res.get("routePlan") or []):
        label = (leg.get("label") or leg.get("swapInfo", {}).get("label"))
        if label:
            route_labels.append(label)
    route = " → ".join(route_labels) if route_labels else None

    impact = res.get("priceImpactPct")
    impact_pct = f"{float(impact) * 100:.2f}%" if impact is not None else None
    slip_pct = f"{(res.get('slippageBps') or slip_bps) / 100:.2f}%"

    lines = []
    if in_ui and out_ui:
        lines.append(f"{fmt(in_ui)} {in_info['symbol']} → {fmt(out_ui)} {out_info['symbol']}")
    elif in_ui:
        lines.append(f"{fmt(in_ui)} {in_info['symbol']}")
    if price is not None:
        lines.append(f"Price: 1 {in_info['symbol']} ≈ {fmt(price,6)} {out_info['symbol']}")
    meta = f"Slippage: {slip_pct}"
    if impact_pct:
        meta += f" · Impact: {impact_pct}"
    lines.append(meta)
    if route:
        lines.append(f"Route: {route}")
    return "\n".join(lines)

# ---------- protocol & unit helpers ----------
def to_lamports(sol_amount: float) -> int:
    return int(round(sol_amount * 1_000_000_000))

_PROTOCOLS = {
    "JITOSOL": "jito", "JITO": "jito",
    "MSOL": "marinade", "MARINADE": "marinade",
    "BSOL": "blaze", "BLAZE": "blaze",
    "SCNSOL": "socean", "SOCEAN": "socean",
}
def _proto(token: str) -> str:
    return _PROTOCOLS.get(token.upper(), token.lower())

# ---------- option helpers ----------
def _actions_to_buttons(actions: list[dict]) -> list[list[InlineKeyboardButton]]:
    """Build inline buttons for a list of actions (balance/quote/swap/stake/unstake)."""
    rows: list[list[InlineKeyboardButton]] = []
    proto_to_token = {"jito": "JITOSOL", "marinade": "MSOL", "blaze": "BSOL", "socean": "SCNSOL"}
    for a in actions or []:
        verb = (a.get("verb") or "").lower()
        p = a.get("params") or {}
        if verb == "balance":
            rows.append([InlineKeyboardButton("💰 Balance", callback_data="run:balance")])
        elif verb == "quote":
            ins, outs, amt = p.get("in","?"), p.get("out","?"), p.get("amount","0.10")
            rows.append([InlineKeyboardButton(f"🧮 Quote {ins}→{outs} {amt}",
                          callback_data=f"run:quote:{ins}:{outs}:{amt}:{DEFAULT_SLIP_BPS}")])
        elif verb == "swap":
            ins, outs, amt = p.get("in","?"), p.get("out","?"), p.get("amount","0.10")
            rows.append([InlineKeyboardButton(f"🔁 Swap {ins}→{outs} {amt}",
                          callback_data=f"run:swap:{ins}:{outs}:{amt}:{DEFAULT_SLIP_BPS}")])
        elif verb == "stake":
            proto, amt = (p.get("protocol") or "").lower(), p.get("amount","0.10")
            token = proto_to_token.get(proto, "JITOSOL")
            rows.append([InlineKeyboardButton(f"🪙 Stake {token} {amt}",
                          callback_data=f"run:stake:{token}:{amt}")])
        elif verb == "unstake":
            proto, amt = (p.get("protocol") or "").lower(), p.get("amount","0.10")
            token = proto_to_token.get(proto, "JITOSOL")
            rows.append([InlineKeyboardButton(f"🪙 Unstake {token} {amt}",
                          callback_data=f"run:unstake:{token}:{amt}")])
    return rows

def _options_cta(options: list[dict]) -> list[list[InlineKeyboardButton]]:
    """Build CTA buttons for options; label with their names."""
    rows: list[list[InlineKeyboardButton]] = []
    for idx, opt in enumerate(options or []):
        name = str(opt.get("name") or f"Option {idx+1}").strip()
        rows.append([InlineKeyboardButton(f"▶️ {name}", callback_data=f"opt:{idx}")])
    return rows

# ---------- handlers (basic)
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("START command")
    await update.message.reply_text(
        "GoblinBot ready ✅\n\n"
        "Use /plan <goal>\nExample: /plan grow 1 SOL to 10 SOL\n\n"
        "Exec:\n"
        "/balance [TOKEN]\n"
        "/quote <FROM> <TO> <AMOUNT> [slippage_bps]\n"
        "/swap <FROM> <TO> <AMOUNT> [slippage_bps]\n"
        "/stake <TOKEN> <AMOUNT>\n"
        "/unstake <TOKEN> <AMOUNT>"
    )

async def ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong")

# ---- /plan: build exec-ready plan and surface simulate CTA
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

    plan_text, wallet_state = await _call_planner(goal)

    try:
        parsed = json.loads(plan_text)
        plan_dict = parsed if isinstance(parsed, dict) else {}
    except Exception:
        logging.warning("Planner response not JSON; proceeding with empty plan snapshot")
        plan_dict = {}

    exec_plan = build_exec_ready_plan(goal=goal, raw_plan=plan_dict, wallet_state=wallet_state)
    token = uuid.uuid4().hex
    exec_plan["token"] = token

    chat_id = update.effective_chat.id if update.effective_chat else None
    if isinstance(chat_id, int):
        _LAST_PLAN[chat_id] = exec_plan

    # Warm Jupiter list in background (faster first quote)
    asyncio.create_task(_jup_tokenlist())

    await send_playback_with_simulate(update, ctx, exec_plan)

    if SHOW_PLAN_JSON:
        code = f"<pre>{html.escape(plan_text)}</pre>"
        await update.message.reply_text(code, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def send_playback_with_simulate(update: Update, ctx: ContextTypes.DEFAULT_TYPE, plan: Dict[str, Any]) -> None:
    """Send the playback summary with a single simulate CTA and cache the plan."""

    token = plan.get("token") or uuid.uuid4().hex
    plan["token"] = token
    _sim_cache_put(token, plan)

    playback_text = plan.get("playback_text") or plan.get("summary") or "Plan ready. Simulate before executing"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🧪 Simulate Scenarios", callback_data=f"SIM:{token}")]]
    )

    if update.message:
        await update.message.reply_text(
            playback_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    else:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is not None:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=playback_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

async def _remove_inline_keyboard(callback_query) -> None:
    try:
        await callback_query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

def _render_series_chart(series: list[Dict[str, Any]], title: Optional[str]) -> Optional[io.BytesIO]:
    if plt is None:
        return None
    fig, ax = plt.subplots(figsize=(6, 4))  # type: ignore[call-arg]
    plotted = False
    for item in series:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "Scenario")
        t = item.get("t") or []
        v = item.get("v") or []
        if not isinstance(t, (list, tuple)) or not isinstance(v, (list, tuple)):
            continue
        if len(t) != len(v):
            continue
        try:
            xs = [float(x) for x in t]
            ys = [float(y) for y in v]
        except Exception:
            continue
        ax.plot(xs, ys, label=name)  # type: ignore[arg-type]
        plotted = True
    if not plotted:
        plt.close(fig)
        return None
    ax.set_title(title or "Simulation")  # type: ignore[attr-defined]
    ax.set_xlabel("Days")  # type: ignore[attr-defined]
    ax.set_ylabel("Value (normalized)")  # type: ignore[attr-defined]
    ax.legend(loc="best")  # type: ignore[attr-defined]
    fig.tight_layout()  # type: ignore[attr-defined]
    buf = io.BytesIO()
    fig.savefig(buf, format="png")  # type: ignore[attr-defined]
    plt.close(fig)
    buf.seek(0)
    buf.name = "simulation.png"
    return buf

def _series_summary_lines(series: list[Dict[str, Any]], title: Optional[str], caption: Optional[str]) -> list[str]:
    lines: list[str] = []
    if title:
        lines.append(str(title))
    for item in series:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "Scenario")
        values = item.get("v") or []
        if isinstance(values, (list, tuple)) and values:
            try:
                start = float(values[0])
                end = float(values[-1])
                lines.append(f"{name}: {start:.4f} → {end:.4f}")
            except Exception:
                lines.append(f"{name}: data unavailable")
    if caption:
        lines.append(str(caption))
    if not lines:
        lines.append("Simulation completed.")
    return lines

async def on_simulate_scenarios(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer("Simulating…")
    except Exception:
        pass

    token = ""
    data = q.data or ""
    if isinstance(data, str) and data.startswith("SIM:"):
        token = data.split(":", 1)[1]
    if not token:
        await _remove_inline_keyboard(q)
        await q.message.reply_text("Session expired. Ask for a new plan.")
        return

    plan = _sim_cache_get(token)
    if not plan:
        await _remove_inline_keyboard(q)
        await q.message.reply_text("Session expired. Ask for a new plan.")
        return

    options = plan.get("options") or []
    payload: Dict[str, Any] = {
        "frame": plan.get("frame") or "CSA",
        "options": list(options)[:3],
        "sizing": plan.get("sizing") or {},
        "baseline": plan.get("baseline") or {"name": "Hold SOL"},
        "horizon_days": plan.get("horizon_days") or 30,
    }
    account = plan.get("account") or plan.get("payer")
    if account:
        payload["account"] = account

    try:
        resp = await _exec_post("simulate", payload)
    except HTTPStatusError as err:
        body_text = ""
        try:
            if err.response is not None:
                body_text = err.response.text
        except Exception:
            body_text = ""
        snippet = (body_text or str(err))[:200]
        await _remove_inline_keyboard(q)
        await q.message.reply_text(_goblin_error_message(err))
        return
    except Exception as err:
        await _remove_inline_keyboard(q)
        await q.message.reply_text(f"⚠️ Simulation failed: {err}")
        return

    await _remove_inline_keyboard(q)

    if not isinstance(resp, dict):
        await q.message.reply_text("⚠️ Unexpected simulation response.")
        return

    chart_b64 = resp.get("chart_png_base64")
    caption = resp.get("caption") if isinstance(resp.get("caption"), str) else None
    title = resp.get("title") if isinstance(resp.get("title"), str) else None

    if isinstance(chart_b64, str) and chart_b64.strip():
        try:
            image_bytes = base64.b64decode(chart_b64)
            bio = io.BytesIO(image_bytes)
            bio.name = "simulation.png"
            await q.message.reply_photo(photo=bio, caption=caption or title)
            return
        except Exception as err:
            await q.message.reply_text(f"⚠️ Simulation returned invalid chart: {err}")
            return

    raw_series = resp.get("series")
    series = [s for s in raw_series if isinstance(s, dict)] if isinstance(raw_series, list) else []
    if series:
        chart_buf = _render_series_chart(series, title)
        if chart_buf:
            await q.message.reply_photo(photo=chart_buf, caption=caption or title)
            return
        lines = _series_summary_lines(series, title, caption)
        await q.message.reply_text("\n".join(lines))
        return

    await q.message.reply_text("⚠️ Unexpected simulation response.")

# ---- Option CTA handler: show that option’s mini-brief and its actions
async def on_option_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "").split(":")
    if not data or data[0] != "opt":
        return
    if ALLOWED_USER_IDS and str(getattr(update.effective_user, "id", "")) not in ALLOWED_USER_IDS:
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    plan = _LAST_PLAN.get(chat_id or -1) if isinstance(chat_id, int) else None
    if not plan:
        return await q.message.reply_text("Plan expired. Run /plan again.")

    options = plan.get("options") or []
    if len(data) >= 2 and data[1].isdigit():
        idx = int(data[1])
        if idx < 0 or idx >= len(options):
            return await q.message.reply_text("That option is unavailable. Run /plan again.")
        opt = options[idx]

        # mini-brief
        name = html.escape(str(opt.get("name") or f"Option {idx+1}"))
        strat = html.escape(str(opt.get("strategy") or ""))
        rationale = html.escape(str(opt.get("rationale") or ""))
        tradeoffs = opt.get("tradeoffs")
        if isinstance(tradeoffs, dict):
            pros = _fmt_list_or_str(tradeoffs.get("pros"))
            cons = _fmt_list_or_str(tradeoffs.get("cons"))
        else:
            pros = _fmt_list_or_str(tradeoffs)
            cons = ""

        lines = [f"<b>{name}</b>", strat]
        if rationale: lines.append(f"· Why: {rationale}")
        if pros: lines.append(f"· Pros: {html.escape(pros)}")
        if cons: lines.append(f"· Cons: {html.escape(cons)}")

        await q.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

        # actions for this option
        opt_actions = opt.get("plan") or []
        rows = _actions_to_buttons(opt_actions)
        if rows:
            # add a back-to-options button
            rows.append([InlineKeyboardButton("◀️ Back to options", callback_data="opt:menu")])
            await q.message.reply_text("Actions for this option:", reply_markup=InlineKeyboardMarkup(rows))
        return

    # opt:menu → show the option CTA menu again
    if len(data) >= 2 and data[1] == "menu":
        opt_rows = _options_cta(options)
        return await q.message.reply_text("Choose a path:", reply_markup=InlineKeyboardMarkup(opt_rows))

# ---- Inline primitive executor
async def on_action_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "").split(":")
    if not data or data[0] != "run":
        return
    if ALLOWED_USER_IDS and str(getattr(update.effective_user, "id", "")) not in ALLOWED_USER_IDS:
        return

    try:
        verb = data[1]
        if verb == "balance":
            res = await _exec_post("balance", {"wallet": WALLET_ADDRESS, "token": "SOL", "network": NETWORK})
            if "sol" in res:
                ui = float(res["sol"]); extra = f" ({int(res.get('lamports', ui*1e9)):,} lamports)"
            elif "uiAmount" in res:
                ui = float(res["uiAmount"]); extra = ""
            elif "amount" in res and "decimals" in res:
                ui = int(res["amount"]) / (10**int(res["decimals"])); extra = ""
            else:
                ui = res.get("balance") or res.get("result") or res; extra = ""
            await q.message.reply_text(f"💰 Balance SOL: {fmt(float(ui))}{extra}")
            return

        if verb in ("quote", "swap"):
            _, _, ins, outs, amt, slip = data
            slip_bps = int(slip)

            if verb == "quote":
                payload = await _quote_payload_indexjs(ins, outs, amt, slip_bps)
                res = await _exec_post("quote", payload)
                in_mint, out_mint = res.get("inputMint"), res.get("outputMint")
                in_info  = mint_info(in_mint) if in_mint else {"symbol": _normalize_symbol_guess(ins),  "decimals": 9 if ins == "SOL" else 6}
                out_info = mint_info(out_mint) if out_mint else {"symbol": _normalize_symbol_guess(outs), "decimals": 9 if outs == "SOL" else 6}
                in_ui  = to_ui(res.get("inAmount"),  in_info["decimals"]) or float(amt)
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
                await q.message.reply_text("\n".join(lines))
                return

            res = await _exec_post("swap", _swap_payload(ins, outs, amt, slip_bps))
            sig = pull_sig(res)
            summary = summarize_swap_like(res, ins, outs, slip_bps)
            msg = f"🔁 Swap sent ✅\n{summary}"
            if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
            await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
            return

        if verb in ("stake", "unstake"):
            _, _, token, amt = data
            payload = {"protocol": _proto(token), "amountLamports": to_lamports(float(amt)),
                       "wallet": WALLET_ADDRESS, "network": NETWORK}
            try:
                res = await _exec_post(verb, payload)
                sig = pull_sig(res)
                msg = f"🪙 {verb.capitalize()}d {fmt(float(amt))} {token} ✅"
                if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
                await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
            except HTTPStatusError as e_http:
                await q.message.reply_text(_goblin_error_message(e_http))
            return
    except Exception as err:
        logging.exception("Action button failed: %s", err)
        await q.message.reply_text(f"⚠️ Action failed: {err}")

async def unknown_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Unknown command. Try /plan <goal>.")

# ---------- direct commands (power users)
async def balance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    toks = _args(ctx)
    token = _norm(toks[0]) if toks else "SOL"
    try:
        res = await _exec_post("balance", {"wallet": WALLET_ADDRESS, "token": token, "network": NETWORK})
        if "sol" in res:
            ui = float(res["sol"]); extra = f" ({int(res.get('lamports', ui*1e9)):,} lamports)"
        elif "uiAmount" in res:
            ui = float(res["uiAmount"]); extra = ""
        elif "amount" in res and "decimals" in res:
            ui = int(res["amount"]) / (10**int(res["decimals"])); extra = ""
        else:
            ui = res.get("balance") or res.get("result") or res; extra = ""
        await update.message.reply_text(f"💰 Balance {token}: {fmt(float(ui))}{extra}")
    except Exception as err:
        await update.message.reply_text(f"⚠️ Balance failed: {err}")

async def quote_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _clean(_args(ctx))
    if len(argv) < 3:
        return await update.message.reply_text("Usage: /quote <FROM> <TO> <AMOUNT> [slippage_bps]\nExample: /quote SOL USDC 0.5 100")
    from_sym, to_sym = _norm(argv[0]), _norm(argv[1])
    try:
        amount = _parse_amount(argv[2])
    except Exception as err:
        return await update.message.reply_text(str(err))
    slip_bps = int(argv[3]) if len(argv) >= 4 else DEFAULT_SLIP_BPS
    try:
        payload = await _quote_payload_indexjs(from_sym, to_sym, amount, slip_bps)
        res = await _exec_post("quote", payload)
        in_mint, out_mint = res.get("inputMint"), res.get("outputMint")
        in_info  = mint_info(in_mint) if in_mint else {"symbol": _normalize_symbol_guess(from_sym), "decimals": 9 if from_sym == "SOL" else 6}
        out_info = mint_info(out_mint) if out_mint else {"symbol": _normalize_symbol_guess(to_sym),  "decimals": 9 if to_sym == "SOL"  else 6}
        in_ui  = to_ui(res.get("inAmount"),  in_info["decimals"]) or float(amount)
        out_ui = to_ui(res.get("outAmount"), out_info["decimals"])
        price  = (out_ui / in_ui) if in_ui else None
        impact = res.get("priceImpactPct"); impact_pct = f"{float(impact) * 100:.2f}%" if impact is not None else None
        slip_pct = f"{(res.get('slippageBps') or slip_bps) / 100:.2f}%"
        lines = [f"🧮 Quote {fmt(in_ui)} {in_info['symbol']} → {out_info['symbol']}"]
        if out_ui:
            lines.append(f"Est. out: {fmt(out_ui)} {out_info['symbol']}")
        if price is not None:
            lines.append(f"Price: 1 {in_info['symbol']} ≈ {fmt(price,6)} {out_info['symbol']}")
        meta = f"Slippage: {slip_pct}"
        if impact_pct:
            meta += f" · Impact: {impact_pct}"
        lines.append(meta)
        await update.message.reply_text("\n".join(lines))
    except Exception as err:
        if isinstance(err, HTTPStatusError):
            await update.message.reply_text(_goblin_error_message(err))
        else:
            await update.message.reply_text(f"⚠️ Quote failed: {err}")

async def swap_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _clean(_args(ctx))
    if len(argv) < 3:
        return await update.message.reply_text("Usage:\n/swap <FROM> <TO> <AMOUNT> [slippage_bps]\nExample: /swap SOL USDC 0.5 100")
    from_sym, to_sym = _norm(argv[0]), _norm(argv[1])
    try:
        amount = _parse_amount(argv[2])
    except Exception as err:
        return await update.message.reply_text(str(err))
    slip_bps = int(argv[3]) if len(argv) >= 4 else DEFAULT_SLIP_BPS
    try:
        res = await _exec_post("swap", _swap_payload(from_sym, to_sym, amount, slip_bps))
        sig = pull_sig(res)
        summary = summarize_swap_like(res, from_sym, to_sym, slip_bps)
        msg = f"🔁 Swap sent ✅\n{summary}"
        if sig:
            msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
    except Exception as err:
        if isinstance(err, HTTPStatusError):
            await update.message.reply_text(_goblin_error_message(err))
        else:
            await update.message.reply_text(f"⚠️ Swap failed: {err}")

async def stake_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _args(ctx)
    if len(argv) < 2:
        return await update.message.reply_text("Usage:\n/stake <TOKEN> <AMOUNT>\nExample: /stake JITOSOL 1.0")
    token = _norm(argv[0])
    try:
        amount = _parse_amount(argv[1])
    except Exception as err:
        return await update.message.reply_text(str(err))
    payload = {"protocol": _proto(token), "amountLamports": to_lamports(amount),
               "wallet": WALLET_ADDRESS, "network": NETWORK}
    try:
        res = await _exec_post("stake", payload)
        sig = pull_sig(res)
        msg = f"🪙 Staked {fmt(amount)} {token} ✅"
        if sig:
            msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
    except HTTPStatusError as e_http:
        await update.message.reply_text(_goblin_error_message(e_http))

async def unstake_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _args(ctx)
    if len(argv) < 2:
        return await update.message.reply_text("Usage:\n/unstake <TOKEN> <AMOUNT>\nExample: /unstake JITOSOL 0.5")
    token = _norm(argv[0])
    try:
        amount = _parse_amount(argv[1])
    except Exception as err:
        return await update.message.reply_text(str(err))
    payload = {"protocol": _proto(token), "amountLamports": to_lamports(amount),
               "wallet": WALLET_ADDRESS, "network": NETWORK}
    try:
        res = await _exec_post("unstake", payload)
        sig = pull_sig(res)
        msg = f"🪙 Unstaked {fmt(amount)} {token} ✅"
        if sig:
            msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
    except HTTPStatusError as e_http:
        await update.message.reply_text(_goblin_error_message(e_http))

def add_handlers(a: Application):
    a.add_handler(CommandHandler("start", start))
    a.add_handler(CommandHandler("ping", ping))
    a.add_handler(CommandHandler("plan", plan_cmd))
    a.add_handler(CallbackQueryHandler(on_simulate_scenarios, pattern=r"^SIM:"))
    a.add_handler(CallbackQueryHandler(on_option_button, pattern=r"^opt:"))  # option CTAs
    a.add_handler(CallbackQueryHandler(on_action_button, pattern=r"^run:"))  # primitive CTAs
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
    if WEBHOOK_BASE_URL:
        url_path = f"webhook/{WEBHOOK_SECRET}"
        webhook_url = f"{WEBHOOK_BASE_URL}/{url_path}"
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
        logging.info("WEBHOOK_BASE_URL not set -> polling mode (not suitable for Cloud Run)")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
