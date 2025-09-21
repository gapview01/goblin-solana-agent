# telegram_service/server.py
import os, logging, inspect, math, html, json, time, re, asyncio
import httpx
from httpx import HTTPStatusError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

# --- import path guard (ensure `planner/` is importable regardless of CWD)
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]  # repo root (one level above telegram_service)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------- basic config (env)
TOKEN          = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
BASE_URL       = (os.getenv("BASE_URL") or "").strip().rstrip("/")
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "hook").strip()
PORT           = int(os.getenv("PORT") or 8080)
LOG_LEVEL      = (os.getenv("LOG_LEVEL") or "INFO").upper()
SHOW_PLAN_JSON = (os.getenv("SHOW_PLAN_JSON") or "0").lower() in ("1", "true", "yes")

# Planner selection: "llm" (default) prefers planner/llm_planner.py; set to "legacy" for planner/planner.py
PLANNER_IMPL   = (os.getenv("PLANNER_IMPL") or "llm").lower()
REQUIRE_LLM    = (os.getenv("REQUIRE_LLM_PLANNER") or "0").lower() in ("1", "true", "yes")

# Executor (backend) config
EXECUTOR_URL      = (os.getenv("EXECUTOR_URL") or "").rstrip("/")
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

# --- tiny in-memory plan cache per chat (for option CTAs) ---
_LAST_PLAN: dict[int, dict] = {}   # chat_id -> plan dict

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

async def _call_planner(goal: str) -> str:
    """
    Calls the planner with:
      - wallet_state (sol_balance) so it can size amounts
      - policy override to ALLOW ALL TOKENS ('*') for planning (memecoins included)
      - optional min_token_mcap_usd hint
    """
    if not llm_plan:
        return f"(demo) Plan for: {goal}"
    try:
        # get SOL balance for balance-aware sizing
        sol = 0.0
        try:
            bal = await _exec_post("balance", {"wallet": WALLET_ADDRESS, "token": "SOL", "network": NETWORK})
            sol = float(bal.get("sol") or bal.get("uiAmount") or 0.0)
        except Exception:
            pass

        sig = inspect.signature(llm_plan)
        kwargs = {}

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
    if not EXECUTOR_URL:
        raise RuntimeError("EXECUTOR_URL not set")
    url = f"{EXECUTOR_URL}/{path.lstrip('/')}"
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

# ---- /plan: RICH BRIEFING + option CTAs + inline actions
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

    # Parse planner JSON (rich)
    try:
        plan = json.loads(plan_text)
    except Exception:
        code = f"<pre>{html.escape(plan_text)}</pre>"
        await update.message.reply_text(code, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    # Cache plan per chat for option callbacks
    chat_id = update.effective_chat.id if update.effective_chat else None
    if isinstance(chat_id, int):
        _LAST_PLAN[chat_id] = plan

    # Warm Jupiter list in background (faster first quote)
    asyncio.create_task(_jup_tokenlist())

    # ---- Rich briefing fields
    summary = plan.get("summary") or ""
    understanding = plan.get("understanding") or {}
    policy = plan.get("policy") or {}
    options = plan.get("options") or []
    token_candidates = plan.get("token_candidates") or []
    risks = plan.get("risks") or []
    simulation = plan.get("simulation") or {}
    default_option = (plan.get("default_option") or "").strip()
    actions = plan.get("actions") or []

    # Build briefing text
    brief = []
    goal_rewrite = understanding.get("goal_rewrite") or goal
    brief.append(f"<b>Goal:</b> {html.escape(goal_rewrite)}")
    if summary:
        brief.append(f"<b>Summary:</b> {html.escape(summary)}")

    if token_candidates:
        brief.append("<b>Candidates:</b>")
        for t in token_candidates[:3]:
            sym = html.escape(str(t.get("symbol") or ""))
            why = html.escape(str(t.get("rationale") or ""))
            rsk = _fmt_list_or_str(t.get("risks"))
            line = f"• <code>{sym}</code> — {why}"
            if rsk:
                line += f" (risks: {html.escape(rsk)})"
            brief.append(line)

    if options:
        brief.append("<b>Options:</b>")
        for idx, opt in enumerate(options):
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
            mark = " (default)" if default_option and name.lower() == default_option.lower() else ""
            brief.append(f"• <b>{name}</b>{mark}: {strat}")
            if rationale:
                brief.append(f"  · Why: {rationale}")
            if pros:
                brief.append(f"  · Pros: {html.escape(pros)}")
            if cons:
                brief.append(f"  · Cons: {html.escape(cons)}")

    if risks:
        brief.append("<b>Key risks:</b> " + html.escape(_fmt_list_or_str(risks)))

    if simulation:
        crit = simulation.get("success_criteria") or {}
        gates = []
        if isinstance(crit, dict) and "max_price_impact_bps" in crit:
            gates.append(f"impact ≤ {crit['max_price_impact_bps']} bps")
        if "min_token_mcap_usd" in policy:
            gates.append(f"mcap ≥ ${int(policy['min_token_mcap_usd']):,}")
        if gates:
            brief.append("<b>Simulate before executing:</b> " + " · ".join(gates))

    await update.message.reply_text("\n".join(brief), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    # ---- Option CTAs (dynamic names)
    if options:
        opt_rows = _options_cta(options)
        await update.message.reply_text("Choose a path:", reply_markup=InlineKeyboardMarkup(opt_rows))

    # ---- Primitive action buttons (root 'actions' for quick simulate/exec)
    action_rows = _actions_to_buttons(actions)
    if action_rows:
        await update.message.reply_text("Tap to simulate or execute:", reply_markup=InlineKeyboardMarkup(action_rows))

    if SHOW_PLAN_JSON:
        code = f"<pre>{html.escape(plan_text)}</pre>"
        await update.message.reply_text(code, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

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
            except HTTPStatusError:
                if verb == "stake":
                    res = await _exec_post("swap", _swap_payload("SOL", token, amt, DEFAULT_SLIP_BPS))
                    sig = pull_sig(res)
                    summary = summarize_swap_like(res, "SOL", token, DEFAULT_SLIP_BPS)
                    msg = f"🪙 Staked (via swap) ✅\n{summary}"
                    if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
                    await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
                else:
                    res = await _exec_post("swap", _swap_payload(token, "SOL", amt, DEFAULT_SLIP_BPS))
                    sig = pull_sig(res)
                    summary = summarize_swap_like(res, token, "SOL", DEFAULT_SLIP_BPS)
                    msg = f"🪙 Unstaked (via swap) ✅\n{summary}"
                    if sig: msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
                    await q.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
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
    except HTTPStatusError:
        try:
            res = await _exec_post("swap", _swap_payload("SOL", token, amount, DEFAULT_SLIP_BPS))
            sig = pull_sig(res)
            summary = summarize_swap_like(res, "SOL", token, DEFAULT_SLIP_BPS)
            msg = f"🪙 Staked (via swap) ✅\n{summary}"
            if sig:
                msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
        except Exception as err2:
            await update.message.reply_text(f"⚠️ Stake failed: {err2}")

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
    except HTTPStatusError:
        try:
            res = await _exec_post("swap", _swap_payload(token, "SOL", amount, DEFAULT_SLIP_BPS))
            sig = pull_sig(res)
            summary = summarize_swap_like(res, token, "SOL", DEFAULT_SLIP_BPS)
            msg = f"🪙 Unstaked (via swap) ✅\n{summary}"
            if sig:
                msg += f"\nTx: `{sig}`\n{solscan_url(sig)}"
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
        except Exception as err2:
            await update.message.reply_text(f"⚠️ Unstake failed: {err2}")

def add_handlers(a: Application):
    a.add_handler(CommandHandler("start", start))
    a.add_handler(CommandHandler("ping", ping))
    a.add_handler(CommandHandler("plan", plan_cmd))
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
