# telegram_service/server.py
import os, logging, inspect, math, html, json, time, re, asyncio, uuid, base64, io
from aiohttp import web
import httpx
from httpx import HTTPStatusError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.request import HTTPXRequest
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
from bot.texts import (
    START_TEXT, CHECK_TEXT, DO_TEXT, GROW_MENU_TEXT, GROW_TEXT,
    QUOTE_HELP, SWAP_HELP, STAKE_HELP, UNSTAKE_HELP, PLAN_HELP, GOAL_HELP, EARN_HELP,
    ERR_TOO_MUCH, ERR_UNKNOWN_TOKEN, ERR_MISSING,
    render_simple_plan, NO_PLAN_FOUND,
)
from bot.renderers.plan_renderer import render_plan_simple
from bot.handlers.compare import show_compare as _show_compare_card
from bot.nlp import (
    parse_quote, parse_swap, parse_stake, parse_unstake, parse_plan, parse_goal, parse_earn,
)

# ---------- basic config (env)
TOKEN             = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
WEBHOOK_BASE_URL  = (os.getenv("WEBHOOK_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
WEBHOOK_SECRET    = (os.getenv("WEBHOOK_SECRET") or "hook").strip()
PORT              = int(os.getenv("PORT") or 8080)
LOG_LEVEL         = (os.getenv("LOG_LEVEL") or "INFO").upper()
SHOW_PLAN_JSON    = (os.getenv("SHOW_PLAN_JSON") or "0").lower() in ("1", "true", "yes")
USE_POLLING       = (os.getenv("USE_POLLING") or "0").lower() in ("1", "true", "yes")

# Planner selection: "llm" (default) prefers planner/llm_planner.py; set to "legacy" for planner/planner.py
PLANNER_IMPL   = (os.getenv("PLANNER_IMPL") or "llm").lower()
REQUIRE_LLM    = (os.getenv("REQUIRE_LLM_PLANNER") or "0").lower() in ("1", "true", "yes")

# Executor (backend) config
EXECUTOR_BASE_URL = (os.getenv("BASE_URL") or os.getenv("EXECUTOR_URL") or "").rstrip("/")
EXECUTOR_TOKEN    = (os.getenv("EXECUTOR_TOKEN") or "").strip()
SIM_WEBAPP_URL    = (os.getenv("SIM_WEBAPP_URL") or "").strip()
SIM_API_URL       = (os.getenv("SIM_API_URL") or "").strip()  # optional external API for web app
WALLET_ADDRESS    = (os.getenv("WALLET_ADDRESS") or "").strip()
NETWORK           = (os.getenv("NETWORK") or "mainnet").strip()
DEFAULT_SLIP_BPS  = int(os.getenv("DEFAULT_SLIPPAGE_BPS") or "100")
HIGH_IMPACT_THRESHOLD = float(os.getenv("HIGH_PRICE_IMPACT_THRESHOLD") or "3.0")
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
                    msg = f"⚠️ Not enough SOL, goblin. Need {need} incl. fees; you've got {have}."
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

# --- robust Telegram send helper ---
async def _send_message_with_retry(ctx: ContextTypes.DEFAULT_TYPE, *, chat_id: int, text: str,
                                   parse_mode: Optional[str] = None,
                                   reply_markup: Optional[InlineKeyboardMarkup] = None,
                                   reply_to_message_id: Optional[int] = None) -> None:
    attempts = 2
    last_err: Optional[Exception] = None
    for i in range(attempts):
        try:
            await ctx.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
                disable_web_page_preview=True,
            )
            return
        except Exception as err:  # retry once on transient network issues
            last_err = err
            await asyncio.sleep(1.5)
    if last_err:
        raise last_err

# ---------- scenario compare helpers (ASCII) ----------
def _capability_snapshot() -> Dict[str, Any]:
    return {
        "region": os.getenv("REGION") or "global",
        "micro_sol": float(os.getenv("AUTO_MICRO_SOL") or "0.05"),
        "impact_cap_bps": int(os.getenv("MAX_PRICE_IMPACT_BPS") or os.getenv("MAX_PRICE_IMPACT", "200")),
        "unit": (os.getenv("DENOM") or "SOL").upper(),
        "mode": (os.getenv("SIM_MODE") or "Asset"),
    }

def _build_scenario_tracks(exec_plan: Dict[str, Any]) -> list[Dict[str, Any]]:
    # Choose up to 3 options from the exec-ready plan as tracks
    tracks: list[Dict[str, Any]] = []
    options = exec_plan.get("options") or []
    for opt in options[:3]:
        name = str(opt.get("name") or "Track").strip()
        plan = opt.get("plan") or []
        tracks.append({"name": name, "plan": plan})
    # Fallback default labels if nothing present
    if not tracks:
        tracks = [
            {"name": "Yield Track",   "plan": []},
            {"name": "Factor Track",  "plan": []},
        ]
    return tracks[:3]

def _ascii_bar(delta: float, max_abs: float) -> str:
    if max_abs <= 0:
        return "──────────"
    length = int(round(10 * (abs(delta) / max_abs)))
    if length <= 0:
        return "──────────"
    if length == 1:
        return "▊"
    return ("█" * (length - 1)) + "▊"

def _fmt_delta(v: float, unit: str) -> str:
    if unit == "USD":
        sign = "+" if v >= 0 else "-"
        return f"{sign}${abs(v):.2f}"
    # default SOL-style
    return f"{v:+.4f}"

def _fmt_pct(v: float) -> str:
    return f"{v*100:+.1f}%"

def _is_approve_micro_enabled(tracks: list[Dict[str, Any]], micro_sol: float) -> bool:
    # Enabled only if the best track (first in list) has all amounts ≤ micro
    if not tracks:
        return False
    best = tracks[0]
    actions = best.get("plan") or []
    for a in actions:
        p = a.get("params") or {}
        amt = p.get("amount") or p.get("amountLamports")
        if amt is None:
            continue
        try:
            val = float(amt) if not isinstance(amt, str) else float(str(amt))
        except Exception:
            return False
        if val > micro_sol:
            return False
    return True

def _render_compare_card(goal: str, unit: str, mode: str, cap: Dict[str, Any], series: list[Dict[str, Any]]) -> Dict[str, Any]:
    # Expect first series to be baseline, followed by scenarios
    baseline = series[0] if series else {"name": "Baseline", "t": [], "v": [1.0, 1.0]}
    scenarios = series[1:4] if len(series) > 1 else []
    def _delta(s: Dict[str, Any]) -> float:
        vals = s.get("v") or []
        if isinstance(vals, list) and len(vals) >= 2:
            try:
                start = float(vals[0]); end = float(vals[-1])
                return end - start
            except Exception:
                return 0.0
        return 0.0
    deltas = [(_delta(s), s.get("name") or f"Scenario {i+1}") for i, s in enumerate(scenarios)]
    max_abs = max([abs(d[0]) for d in deltas] + [0.0])

    header = (
        "📈 Scenario Compare (vs Do-Nothing Baseline)\n"
        f"🎯 Goal: {goal} · Unit: {unit} · Mode: {mode}\n"
        f"🛡️ Micro ≤ {cap.get('micro_sol')} {unit} · Impact ≤ {cap.get('impact_cap_bps')/100:.2f}% · Region: {cap.get('region')}\n\n"
    )
    lines = [f"Baseline (HODL): 0.0000  ───────────"]
    for dv, name in deltas:
        bar = _ascii_bar(dv, max_abs)
        lines.append(f"{name}:  {_fmt_delta(dv, unit):>7}  {bar}")
    footer = ("\n\n• Bars scaled to today's best outcome.\n"
              "• Quotes expire; refresh if TTL shows 0s.")
    text = (header + "\n".join(lines) + footer)
    if len(text) > 900:
        text = text[:880] + "…"
    return {"text": text}

def _resample(values: list[float], width: int) -> list[float]:
    if not values:
        return [0.0] * width
    n = len(values)
    if n == 1:
        return [values[0]] * width
    out: list[float] = []
    for i in range(width):
        idx = round(i * (n - 1) / max(1, width - 1))
        idx = max(0, min(n - 1, idx))
        out.append(float(values[idx]))
    return out

_BLOCKS = "▁▂▃▄▅▆▇█"

def _sparkline_series(values: list[float], width: int, vmin: float, vmax: float) -> str:
    if width <= 0:
        return ""
    if vmax <= vmin + 1e-12:
        return "─" * width
    vals = _resample(values, width)
    chars = []
    for v in vals:
        r = (float(v) - vmin) / (vmax - vmin)
        r = 0.0 if r < 0 else (1.0 if r > 1 else r)
        lvl = int(round(r * (len(_BLOCKS) - 1)))
        chars.append(_BLOCKS[lvl])
    return "".join(chars)

def _render_projection_card(goal: str, unit: str, mode: str, cap: Dict[str, Any], series: list[Dict[str, Any]]) -> Dict[str, Any]:
    # Build deltas vs baseline (normalized)
    if not series:
        return {"text": "📈 Projection unavailable."}
    baseline = series[0]
    base_v = [float(x) for x in (baseline.get("v") or [1.0, 1.0])]
    b0 = base_v[0] if base_v else 1.0
    if b0 == 0:
        b0 = 1.0

    tracks = series[1:4]
    # Prepare global min/max across all deltas for consistent scaling
    all_vals: list[float] = []
    lines: list[tuple[str, list[float]]] = []

    # Baseline deltas (flat zero)
    base_delta = [ (float(v) / b0) - 1.0 for v in base_v ]
    lines.append(("Baseline (HODL)", base_delta))
    all_vals += base_delta

    for s in tracks:
        name = str(s.get("name") or "Scenario").strip()
        vals = [float(x) for x in (s.get("v") or [])]
        if not vals:
            continue
        d = [ (float(v) / b0) - 1.0 for v in vals ]
        lines.append((name, d))
        all_vals += d

    if not lines:
        return {"text": "📈 Projection unavailable."}

    vmin = min(all_vals)
    vmax = max(all_vals)
    width = 24

    header = (
        "📈 Projection (vs Do-Nothing Baseline)\n"
        f"🎯 Goal: {goal} · Unit: {unit} · Mode: {mode}\n"
        f"🛡️ Micro ≤ {cap.get('micro_sol')} {unit} · Impact ≤ {cap.get('impact_cap_bps')/100:.2f}% · Region: {cap.get('region')}\n\n"
    )

    body_lines: list[str] = []
    for (name, d) in lines:
        spark = _sparkline_series(d, width, vmin, vmax)
        final = d[-1] if d else 0.0
        final_str = _fmt_delta(final, unit)
        body_lines.append(f"{name}: {final_str:>7}  {spark}")

    footer = "\n\n• Higher bars ≈ better relative outcome.\n• Quotes expire; refresh if TTL shows 0s."
    text = header + "\n".join(body_lines) + footer
    if len(text) > 900:
        text = text[:880] + "…"
    return {"text": text}

def _render_simple_compare_text(series: list[Dict[str, Any]]) -> str:
    # Always show 4 lines: Baseline + up to 3 scenarios. Percent vs baseline
    if not series:
        return "No scenarios."
    baseline = series[0]
    base_v = [float(x) for x in (baseline.get("v") or [1.0, 1.0])]
    b0 = base_v[0] if base_v else 1.0
    if b0 == 0:
        b0 = 1.0
    lines: list[str] = []
    # Baseline first
    lines.append("Baseline: 0.0%  ───────────")
    # Scenarios
    scenarios = series[1:4]
    deltas: list[tuple[str, float]] = []
    for s in scenarios:
        name = str(s.get("name") or "Scenario").strip()
        vals = [float(x) for x in (s.get("v") or [])]
        pct = ((vals[-1] / b0) - 1.0) if vals else 0.0
        deltas.append((name, pct))
    max_abs = max([abs(p) for _n, p in deltas] + [0.0])
    for name, pct in deltas:
        bar = _ascii_bar(pct, max_abs)
        lines.append(f"{name}:  {_fmt_pct(pct):>7}  {bar}")
    return "\n".join(lines)

# Compute per-series deltas vs baseline
def _series_deltas(series: list[Dict[str, Any]]) -> list[tuple[str, float]]:
    if not series:
        return []
    out: list[tuple[str, float]] = []
    for s in series[1:4]:
        name = str(s.get("name") or "Scenario").strip()
        vals = s.get("v") or []
        try:
            if isinstance(vals, list) and len(vals) >= 2:
                delta = float(vals[-1]) - float(vals[0])
            else:
                delta = 0.0
        except Exception:
            delta = 0.0
        out.append((name, float(delta)))
    return out

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

# Build the bot app with higher Telegram HTTP timeouts
_TG_REQUEST = HTTPXRequest(
    connect_timeout=10.0,
    read_timeout=30.0,
    write_timeout=30.0,
    pool_timeout=10.0,
)
app = (
    Application.builder()
    .token(TOKEN)
    .request(_TG_REQUEST)
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

        start_ts = time.time()
        logging.info("planner:start")
        if "goal" in sig.parameters and ("text" not in sig.parameters):
            # Run potentially blocking LLM call off the event loop
            res = await asyncio.to_thread(llm_plan, goal, **kwargs)
        else:
            kwargs["text"] = goal
            res = await asyncio.to_thread(llm_plan, **kwargs)

        if inspect.isawaitable(res):
            res = await res
        logging.info("planner:end %.1fs", time.time() - start_ts)
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

# ---------- wallet helpers (for timeouts/fallbacks) ----------
async def _get_wallet_state() -> Dict[str, Any]:
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
    return {"sol_balance": float(sol), "lamports": int(lamports)}

def _make_fallback_plan(goal: str) -> Dict[str, Any]:
    return {
        "summary": f"Plan for: {goal}",
        "options": [
            {"name": "Yield Track", "plan": [{"verb": "balance", "params": {}}, {"verb": "stake", "params": {"protocol": "jito", "amount": "0.05"}}]},
            {"name": "Stable Track", "plan": [{"verb": "balance", "params": {}}, {"verb": "quote", "params": {"in": "SOL", "out": "USDC", "amount": "0.05"}}]},
        ],
        "actions": [{"verb": "balance", "params": {}}],
        "simulation": {"horizon_days": 30},
        "baseline": {"name": "Hold SOL"},
        "frame": "CSA",
    }

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
    logging.info("menu:start")
    await update.message.reply_text(START_TEXT)

async def ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("pong")

async def check_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("menu:check")
    await update.message.reply_text(CHECK_TEXT)

async def do_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("menu:do")
    await update.message.reply_text(DO_TEXT)

async def grow_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("menu:grow")
    return await update.message.reply_text(GROW_MENU_TEXT)

async def goal_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text or ""
    goal = raw.partition(" ")[2].strip() or " ".join(ctx.args or []).strip()
    if not goal or raw.strip().lower().endswith("help"):
        logging.info("help:goal")
        return await update.message.reply_text(GOAL_HELP)
    return await plan_cmd(update, ctx)

async def earn_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    argv = _args(ctx)
    raw = (update.message.text or "").strip()
    if len(argv) < 2 or raw.strip().lower().endswith("help"):
        logging.info("help:earn")
        return await update.message.reply_text(EARN_HELP)
    token = _norm(argv[0]); amount = argv[1]
    ctx.args = [token, amount]
    return await stake_cmd(update, ctx)

# ---------- natural-language router (plain text -> canonical commands)
async def nl_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return
    lower = text.lower()

    def _dispatch(cmd: str) -> list[str]:
        parts = (cmd or "").split()
        return parts[1:] if parts and parts[0].startswith("/") else parts

    # Try each parser; if parse fails but prefix matches, show that command's help
    if lower.startswith("quote"):
        cmd = parse_quote(text)
        if cmd:
            logging.info("nl:quote parsed")
            ctx.args = _dispatch(cmd)
            return await quote_cmd(update, ctx)
        logging.info("help:quote")
        return await update.message.reply_text(QUOTE_HELP)
    if lower.startswith("swap"):
        cmd = parse_swap(text)
        if cmd:
            logging.info("nl:swap parsed")
            ctx.args = _dispatch(cmd)
            return await swap_cmd(update, ctx)
        logging.info("help:swap")
        return await update.message.reply_text(SWAP_HELP)
    if lower.startswith("stake"):
        cmd = parse_stake(text)
        if cmd:
            logging.info("nl:stake parsed")
            ctx.args = _dispatch(cmd)
            return await stake_cmd(update, ctx)
        logging.info("help:stake")
        return await update.message.reply_text(STAKE_HELP)
    if lower.startswith("unstake"):
        cmd = parse_unstake(text)
        if cmd:
            logging.info("nl:unstake parsed")
            ctx.args = _dispatch(cmd)
            return await unstake_cmd(update, ctx)
        logging.info("help:unstake")
        return await update.message.reply_text(UNSTAKE_HELP)
    if lower.startswith("plan"):
        cmd = parse_plan(text)
        if cmd:
            logging.info("nl:plan parsed")
            ctx.args = _dispatch(cmd)
            return await plan_cmd(update, ctx)
        logging.info("help:plan")
        return await update.message.reply_text(PLAN_HELP)
    if lower.startswith("goal"):
        cmd = parse_goal(text)
        if cmd:
            logging.info("nl:goal parsed")
            ctx.args = _dispatch(cmd)
            return await goal_cmd(update, ctx)
        logging.info("help:goal")
        return await update.message.reply_text(GOAL_HELP)
    if lower.startswith("earn"):
        cmd = parse_earn(text)
        if cmd:
            logging.info("nl:earn parsed")
            ctx.args = _dispatch(cmd)
            return await earn_cmd(update, ctx)
        logging.info("help:earn")
        return await update.message.reply_text(EARN_HELP)

# ---- /plan: build exec-ready plan and surface simulate CTA
async def plan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text or ""
    goal = raw.partition(" ")[2].strip() or " ".join(ctx.args).strip()
    if not goal or raw.strip().lower().endswith("help"):
        logging.info("help:plan")
        return await update.message.reply_text(PLAN_HELP)
    try:
        await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    except Exception:
        pass

    # Send immediate stub to improve perceived latency
    stub_msg = None
    try:
        stub_msg = await ctx.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🧠 Planning…",
            reply_to_message_id=(update.message.message_id if update.message else None),
            disable_web_page_preview=True,
        )
    except Exception:
        stub_msg = None
    logging.info("PLAN request user=%s goal=%r",
                 (update.effective_user.id if update.effective_user else "unknown"), goal)

    # Planner call with watchdog to avoid silent stalls during rollouts
    try:
        plan_text, wallet_state = await asyncio.wait_for(_call_planner(goal), timeout=150.0)
    except asyncio.TimeoutError:
        logging.warning("planner:timeout after 150s; using fallback plan")
        wallet_state = await _get_wallet_state()
        plan_text = json.dumps(_make_fallback_plan(goal))
    # If planner stalls or returns empty, build a minimal fallback so we always respond
    if not plan_text or len(str(plan_text).strip()) < 2:
        logging.warning("Planner returned empty; using fallback plan")
        wallet_state = await _get_wallet_state()
        plan_text = json.dumps(_make_fallback_plan(goal))

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

    # Ensure plan is available for simulate callbacks/webapp
    try:
        _sim_cache_put(token, exec_plan)
    except Exception:
        pass

    # Warm Jupiter list in background (faster first quote)
    asyncio.create_task(_jup_tokenlist())

    # Render simplified plan summary first
    try:
        logging.info("plan:render_simple")
        simple = render_plan_simple(goal, exec_plan)
        # Prefer editing the stub so the summary replaces "Planning…" in place
        if stub_msg and getattr(stub_msg, "message_id", None):
            try:
                await ctx.bot.edit_message_text(
                    chat_id=stub_msg.chat_id,
                    message_id=stub_msg.message_id,
                    text=simple,
                    parse_mode=None,
                    disable_web_page_preview=True,
                )
            except Exception:
                # Fallback: send as a new message without reply threading
                await _send_message_with_retry(
                    ctx,
                    chat_id=update.effective_chat.id,
                    text=simple,
                    parse_mode=None,
                    reply_markup=None,
                    reply_to_message_id=None,
                )
        else:
            await _send_message_with_retry(
                ctx,
                chat_id=update.effective_chat.id,
                text=simple,
                parse_mode=None,
                reply_markup=None,
                reply_to_message_id=None,
            )
    except Exception:
        pass

    # Skip legacy verbose playback; concise block only

    # Keep the stub; do not delete to avoid losing user context if anything fails

    if SHOW_PLAN_JSON:
        code = f"<pre>{html.escape(plan_text)}</pre>"
        await update.message.reply_text(code, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def send_playback_with_simulate(update: Update, ctx: ContextTypes.DEFAULT_TYPE, plan: Dict[str, Any]) -> None:
    """Send the playback summary with a single simulate CTA and cache the plan."""

    token = plan.get("token") or uuid.uuid4().hex
    plan["token"] = token
    _sim_cache_put(token, plan)

    playback_text = plan.get("playback_text") or plan.get("summary") or "Plan ready. Simulate before executing"
    # Prefer Telegram Web App if URL configured; else fallback to callback handler
    if SIM_WEBAPP_URL:
            # Build a compact context (goal + top track names), URL-safe base64
        try:
            top_opts = []
            for opt in list(plan.get("options") or [])[:3]:
                top_opts.append({"name": str(opt.get("name") or "Track")})
            ctx_obj = {
                "goal": plan.get("summary") or plan.get("playback_text") or "Your goal",
                "unit": "SOL",
                "mode": "Asset",
                "tracks": top_opts,
            }
            ctx_b64 = base64.urlsafe_b64encode(json.dumps(ctx_obj).encode()).decode()
        except Exception:
            ctx_b64 = ""
        web_url = f"{SIM_WEBAPP_URL}?token={token}&ctx={ctx_b64}"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🧪 Simulate Scenarios", web_app=WebAppInfo(url=web_url))]]
        )
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🧪 Simulate Scenarios", callback_data=f"SIM:{token}")]]
        )

    if update.message:
        await _send_message_with_retry(
            ctx,
            chat_id=update.effective_chat.id,
            text=playback_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            reply_to_message_id=update.message.message_id,
        )
    else:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is not None:
            await _send_message_with_retry(
                ctx,
                chat_id=chat_id,
                text=playback_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
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

# ---------- Mini Web App (served by PTB webhook aiohttp app) ----------
_WEB_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Goblin – Simulate Scenarios</title>
    <script src=\"https://telegram.org/js/telegram-web-app.js\"></script>
    <style>
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 16px; color: #e6e6e6; background: #0f0f13; }
      .card { background: #16161d; border-radius: 12px; padding: 16px; box-shadow: 0 8px 24px rgba(0,0,0,.35); }
      h1 { font-size: 18px; margin: 0 0 8px; }
      pre { white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.35; }
      .muted { color: #9aa0a6; font-size: 12px; }
      .bar { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
      .btns { margin-top: 12px; display: grid; gap: 8px; grid-template-columns: repeat(2, 1fr); }
      button { background: #1f2937; color: #fff; border: 0; border-radius: 8px; padding: 10px 12px; cursor: pointer; }
      button.primary { background: #22c55e; color: #08130b; font-weight: 600; }
      .dbg { display:none; position: fixed; top: 0; left: 0; right: 0; padding: 6px 10px; font-size: 11px; background: #332; color: #ff8; opacity: .9; }
+      .hzn { margin: 6px 0 10px; display:flex; gap:6px; align-items:center; }
+      .hzn button { padding:6px 10px; background:#1f2937; color:#cbd5e1; border-radius:999px; border:0; cursor:pointer; font-size:12px; }
+      .hzn button.active { background:#22c55e; color:#08130b; font-weight:600; }
    </style>
    <script>
      async function load() {
        const urlParams = new URLSearchParams(window.location.search);
        const token = urlParams.get('token') || '';
        const ctx = urlParams.get('ctx') || '';
+        let horizon = parseInt(urlParams.get('h') || '30', 10);
+        if (!Number.isFinite(horizon)) horizon = 30;
+        const setHeader = () => { document.getElementById('goal').textContent = 'Scenario Compare · ' + horizon + 'd'; };
+        setHeader();
        let tgOK = false, readyOK = false;
        try {
          const tg = window.Telegram && window.Telegram.WebApp;
          if (tg) { tgOK = true; try { tg.ready(); readyOK = true; } catch(e) {} }
        } catch (e) {}
        const dbg = document.getElementById('dbg');
        const showDbg = urlParams.get('debug') === '1';
        if (showDbg) dbg.style.display = 'block';
+        const render = async () => {
+          const resp = await fetch('/telegram/simulate?token=' + encodeURIComponent(token) + '&ctx=' + encodeURIComponent(ctx) + '&h=' + encodeURIComponent(String(horizon)));
+          const data = await resp.json();
+          document.getElementById('text').textContent = data.text || 'No data';
+          if (dbg) dbg.textContent = 'load:1 | tg:' + (tgOK?'1':'0') + ' | ready:' + (readyOK?'1':'0') + ' | api:' + (data && data.ok ? '1':'0');
+        };
+        try { await render(); }
+        catch (e) {
+          document.getElementById('text').textContent = 'Failed to load simulation.';
+          if (dbg) dbg.textContent = 'load:1 | tg:' + (tgOK?'1':'0') + ' | ready:' + (readyOK?'1':'0') + ' | api:0';
+        }
+        window.setH = async (h) => {
+          horizon = h;
+          setHeader();
+          for (const el of document.querySelectorAll('.hzn button')) el.classList.remove('active');
+          const id = 'h' + String(h);
+          const btn = document.getElementById(id); if (btn) btn.classList.add('active');
+          try { await render(); } catch (_) {}
+        };
+        const active = document.getElementById('h' + String(horizon)); if (active) active.classList.add('active');
      }
      window.addEventListener('load', load);
    </script>
  </head>
  <body>
    <div id="dbg" class="dbg">init…</div>
    <div class="card"> 
+      <h1 id="goal">Scenario Compare · 30d</h1>
+      <div class="hzn">
+        <button id="h7" onclick="setH(7)">7d</button>
+        <button id="h30" onclick="setH(30)">30d</button>
+        <button id="h90" onclick="setH(90)">90d</button>
+      </div>
      <pre id="text" class="bar">Loading…</pre>
      <div class="muted">Bars scaled to today's best outcome. Quotes expire; refresh if TTL shows 0s.</div>
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

def _build_web_app(tg_app: Application) -> web.Application:
    app = web.Application()

    async def sim_page(_request: web.Request) -> web.Response:
        headers = {"Content-Security-Policy": "frame-ancestors https://*.telegram.org https://web.telegram.org https://*.web.telegram.org https://t.me https://*.t.me"}
        return web.Response(text=_WEB_HTML, content_type="text/html", headers=headers)

    async def sim_api(request: web.Request) -> web.Response:
        token = request.query.get("token") or ""
        ctx_b64 = request.query.get("ctx") or ""
        h_str = request.query.get("h") or ""
        horizon = int(h_str) if h_str.isdigit() else None
        logging.debug("[sim_api] GET token=%s ctx_len=%d h=%s", token, len(ctx_b64), h_str)
        plan = _sim_cache_get(token) if token else None
        if not isinstance(plan, dict):
            logging.debug("[sim_api] cache_miss token=%s", token)
            # Fallback: if we have ctx (base64 JSON with goal/tracks), render baseline + 3 named tracks flat
            try:
                if ctx_b64:
                    ctx_json = json.loads(base64.urlsafe_b64decode(ctx_b64 + "==").decode())
                else:
                    ctx_json = {}
                goal_text = str(ctx_json.get("goal") or "Your goal")
                tracks_in = ctx_json.get("tracks") or []
                track_names = [str(t.get("name") or f"Scenario {i+1}") for i, t in enumerate(tracks_in[:3])]
                # Build simple series: baseline flat, three scenarios flat (or tiny uplift) so 4 bars appear
                series = [
                    {"name": "Baseline (HODL)", "t": [0,1], "v": [1.0, 1.0]},
                ]
                for i, name in enumerate(track_names):
                    uplift = 0.01 if i == 0 else (0.02 if i == 1 else 0.03)
                series = series + [{"name": name, "t": [0,1], "v": [1.0, 1.0 + uplift]} for i, name in enumerate(track_names)]
                # Use original rich projection + compare view
                cap = _capability_snapshot()
                projection = _render_projection_card(goal_text, cap.get("unit") or "SOL", cap.get("mode") or "Asset", cap, series)
                deltas = _series_deltas(series)
                max_abs = max([abs(d[1]) for d in deltas] + [0.0])
                header = (
                    "📈 Scenario Compare (vs Do-Nothing Baseline)\n"
                    f"🎯 Goal: {goal_text} · Unit: {cap.get('unit') or 'SOL'} · Mode: {cap.get('mode') or 'Asset'}\n"
                    f"🛡️ Micro ≤ {cap.get('micro_sol')} {cap.get('unit') or 'SOL'} · Impact ≤ {cap.get('impact_cap_bps')/100:.2f}% · Region: {cap.get('region')}\n\n"
                )
                body = ["Baseline (HODL): 0.0000  ───────────"]
                for name, dv in deltas:
                    bar = _ascii_bar(dv, max_abs)
                    body.append(f"{name}:  {_fmt_delta(dv, cap.get('unit') or 'SOL'):>7}  {bar}")
                footer = "\n\n• Bars scaled to today's best outcome.\n• Quotes expire; refresh if TTL shows 0s."
                compare_text = header + "\n".join(body) + footer
                visual_text = projection["text"] + "\n\n" + compare_text
                return web.json_response({"ok": True, "text": visual_text}, status=200)
            except Exception:
                logging.exception("[sim_api] fallback-from-ctx failed")
                return web.json_response({"ok": False, "text": "Session expired. Press Simulate again."}, status=200)

        # If we already computed the visual text, return it
        visual_text = str(plan.get("visual_text") or "")
        if visual_text:
            logging.debug("[sim_api] cache_hit token=%s text_len=%d", token, len(visual_text))
            return web.json_response({"ok": True, "text": visual_text}, status=200)

        # Otherwise, compute on-demand (same logic as callback handler)
        try:
            options = plan.get("options") or []
            payload: Dict[str, Any] = {
                "frame": plan.get("frame") or "CSA",
                "options": list(options)[:3],
                "sizing": plan.get("sizing") or {},
                "baseline": plan.get("baseline") or {"name": "Hold SOL"},
                "horizon_days": horizon or plan.get("horizon_days") or 30,
            }
            account = plan.get("account") or plan.get("payer")
            if account:
                payload["account"] = account
            logging.debug("[sim_api] POST simulate payload_keys=%s", list(payload.keys()))
            resp = await _exec_post("simulate", payload)
        except HTTPStatusError as e_http:
            logging.exception("[sim_api] simulate HTTP error")
            return web.json_response({"ok": False, "text": _goblin_error_message(e_http)}, status=200)
        except Exception as err:
            logging.exception("[sim_api] simulate failed: %s", err)
            return web.json_response({"ok": False, "text": f"⚠️ Simulation failed: {err}"}, status=200)

        raw_series = resp.get("series")
        series = [s for s in raw_series if isinstance(s, dict)] if isinstance(raw_series, list) else []
        try:
            goal_text = plan.get("summary") or plan.get("playback_text") or "Your goal"
            cap = _capability_snapshot()
            projection = _render_projection_card(goal_text, cap.get("unit") or "SOL", cap.get("mode") or "Asset", cap, series)
            deltas = _series_deltas(series)
            max_abs = max([abs(d[1]) for d in deltas] + [0.0])
            header = (
                "📈 Scenario Compare (vs Do-Nothing Baseline)\n"
                f"🎯 Goal: {goal_text} · Unit: {cap.get('unit') or 'SOL'} · Mode: {cap.get('mode') or 'Asset'}\n"
                f"🛡️ Micro ≤ {cap.get('micro_sol')} {cap.get('unit') or 'SOL'} · Impact ≤ {cap.get('impact_cap_bps')/100:.2f}% · Region: {cap.get('region')}\n\n"
            )
            body = ["Baseline (HODL): 0.0000  ───────────"]
            for name, dv in deltas:
                bar = _ascii_bar(dv, max_abs)
                body.append(f"{name}:  {_fmt_delta(dv, cap.get('unit') or 'SOL'):>7}  {bar}")
            footer = "\n\n• Bars scaled to today's best outcome.\n• Quotes expire; refresh if TTL shows 0s."
            compare_text = header + "\n".join(body) + footer
            visual_text = projection["text"] + "\n\n" + compare_text
            plan_copy = dict(plan)
            plan_copy["visual_text"] = visual_text
            _sim_cache_put(token, plan_copy)
            logging.debug("[sim_api] computed text_len=%d token=%s", len(visual_text), token)
            return web.json_response({"ok": True, "text": visual_text}, status=200)
        except Exception:
            logging.exception("[sim_api] render failed")
            return web.json_response({"ok": False, "text": "⚠️ Render failed."}, status=200)

    async def webhook_handler(request: web.Request) -> web.Response:
        if request.method != "POST":
            return web.Response(status=405)
        # Verify Telegram secret token if provided
        secret_hdr = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if WEBHOOK_SECRET and secret_hdr != WEBHOOK_SECRET:
            return web.Response(status=403, text="forbidden")
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="bad json")
        try:
            update = Update.de_json(data, tg_app.bot)
            await tg_app.update_queue.put(update)
        except Exception:
            logging.exception("webhook_handler failed to enqueue update")
            return web.Response(status=500, text="enqueue failed")
        return web.json_response({"ok": True})

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    # Routes
    app.router.add_get("/webapp/sim", sim_page)
    app.router.add_get("/telegram/simulate", sim_api)
    app.router.add_post(f"/webhook/{WEBHOOK_SECRET}", webhook_handler)
    app.router.add_get("/health", health)

    async def on_startup(_app: web.Application) -> None:
        try:
            await tg_app.initialize()
            await tg_app.start()
            await reconcile_webhook(tg_app)
            logging.info("Startup complete")
        except Exception:
            logging.exception("Startup failed")

    async def on_cleanup(_app: web.Application) -> None:
        try:
            await tg_app.stop()
            await tg_app.shutdown()
        except Exception:
            logging.exception("Shutdown failed")

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app

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
    # Expose an in-memory last-visual text so WebApp can fetch it
    try:
        goal_text = plan.get("summary") or plan.get("playback_text") or "Your goal"
        cap = _capability_snapshot()
        projection = _render_projection_card(goal_text, cap.get("unit") or "SOL", cap.get("mode") or "Asset", cap, series)
        deltas = _series_deltas(series)
        max_abs = max([abs(d[1]) for d in deltas] + [0.0])
        header = (
            "📈 Scenario Compare (vs Do-Nothing Baseline)\n"
            f"🎯 Goal: {goal_text} · Unit: {cap.get('unit') or 'SOL'} · Mode: {cap.get('mode') or 'Asset'}\n"
            f"🛡️ Micro ≤ {cap.get('micro_sol')} {cap.get('unit') or 'SOL'} · Impact ≤ {cap.get('impact_cap_bps')/100:.2f}% · Region: {cap.get('region')}\n\n"
        )
        body = ["Baseline (HODL): 0.0000  ───────────"]
        for name, dv in deltas:
            bar = _ascii_bar(dv, max_abs)
            body.append(f"{name}:  {_fmt_delta(dv, cap.get('unit') or 'SOL'):>7}  {bar}")
        footer = "\n\n• Bars scaled to today's best outcome.\n• Quotes expire; refresh if TTL shows 0s."
        compare_text = header + "\n".join(body) + footer
        visual_text = projection["text"] + "\n\n" + compare_text
        # Store for WebApp retrieval (short TTL via SIM cache timestamp)
        plan_copy = dict(plan)
        plan_copy["visual_text"] = visual_text
        _sim_cache_put(token, plan_copy)
    except Exception:
        pass
    # Render inline compare card below the playback message
    try:
        compare_payload = {
            "goal": goal_text,
            "horizon": f"{int(payload.get('horizon_days') or 30)}d",
            "region": cap.get("region") or "global",
            "micro_budget": f"{cap.get('micro_sol')} {cap.get('unit') or 'SOL'}",
            "max_impact": f"{cap.get('impact_cap_bps')/100:.2f}%",
            "baseline": {"display_name": (plan.get("baseline") or {}).get("name") or "Do-Nothing (Baseline)"},
            "scenarios": [
                {"id": f"s{i}", "display_name": str(s.get("name") or f"Scenario {i}"), "delta_sol": float(s.get("v", [0,0])[-1]) - float(s.get("v", [0,0])[0]) if (s.get("v") and len(s.get("v"))>=2) else 0.0}
                for i, s in enumerate(series[1:5], start=1)
            ],
            "token": plan.get("token") or token,
        }
        # store for callbacks
        try:
            chat_id = update.effective_chat.id if update.effective_chat else None
            if isinstance(chat_id, int):
                app.chat_data.get(chat_id, {}).setdefault("last_plan", compare_payload)
        except Exception:
            pass
        await _show_compare_card(update, ctx, compare_payload)
        return
    except Exception:
        pass
    # Fallbacks: still produce the visual compare instead of raw summary
    if series:
        try:
            compare_text = _render_simple_compare_text(series)
            text = compare_text
            await _send_message_with_retry(
                ctx,
                chat_id=update.effective_chat.id,
                text=text,
                reply_to_message_id=q.message.message_id,
                parse_mode=None,
                reply_markup=None,
            )
            return
        except Exception:
            pass
        # absolute last resort
        lines = _series_summary_lines(series, title, caption)
        await q.message.reply_text("\n".join(lines))
        return
    await q.message.reply_text("⚠️ Unexpected simulation response.")

# ---- Option CTA handler: show that option's mini-brief and its actions
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

async def on_approve_micro(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    token = ""
    data = q.data or ""
    if isinstance(data, str) and data.startswith("approve_micro:"):
        token = data.split(":", 1)[1]
    plan = _sim_cache_get(token) if token else None
    if not isinstance(plan, dict):
        return await q.message.reply_text("Session expired. Run /plan again.")
    cap = _capability_snapshot()
    tracks = _build_scenario_tracks(plan)
    if not _is_approve_micro_enabled(tracks, float(cap.get("micro_sol") or 0.05)):
        return await q.message.reply_text("⚠️ Micro approval not available for this plan.")
    # For now, this is a no-op approval; wire to real tx later
    await q.message.reply_text("✅ Micro approved. Ready to execute when you are.")

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
    raw = (update.message.text or "").lower()
    if (not argv) or ("help" in raw) or (len(argv) < 3):
        logging.info("help:quote")
        return await update.message.reply_text(QUOTE_HELP)
    from_sym, to_sym = _norm(argv[0]), _norm(argv[1])
    try:
        amount = _parse_amount(argv[2])
    except Exception as err:
        return await update.message.reply_text(ERR_MISSING)
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
        impact = res.get("priceImpactPct"); impact_value = (float(impact) * 100.0) if impact is not None else None
        impact_pct = f"{impact_value:.2f}%" if impact_value is not None else None
        slip_pct = f"{(res.get('slippageBps') or slip_bps) / 100:.2f}%"
        lines = []
        if impact_value is not None and impact_value > HIGH_IMPACT_THRESHOLD:
            lines.append(f"⚠️ High price impact ({impact_value:.2f}%). Consider a smaller amount.")
        lines.append(f"🧮 Quote {fmt(in_ui)} {in_info['symbol']} → {out_info['symbol']}")
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
    raw = (update.message.text or "").lower()
    if (not argv) or ("help" in raw) or (len(argv) < 3):
        logging.info("help:swap")
        return await update.message.reply_text(SWAP_HELP)
    from_sym, to_sym = _norm(argv[0]), _norm(argv[1])
    try:
        amount = _parse_amount(argv[2])
    except Exception as err:
        return await update.message.reply_text(ERR_MISSING)
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
    raw = (update.message.text or "").lower()
    if (not argv) or ("help" in raw) or (len(argv) < 2):
        logging.info("help:stake")
        return await update.message.reply_text(STAKE_HELP)
    token = _norm(argv[0])
    try:
        amount = _parse_amount(argv[1])
    except Exception as err:
        return await update.message.reply_text(ERR_MISSING)
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

async def simulate_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logging.info("simulate:render")
    data = ctx.user_data.get("last_plan") if hasattr(ctx, "user_data") else None
    if not isinstance(data, dict):
        return await update.message.reply_text(NO_PLAN_FOUND)
    payload = data.get("payload") or data.get("plan") or {}
    if not isinstance(payload, dict):
        return await update.message.reply_text(NO_PLAN_FOUND)
    token = payload.get("token") or uuid.uuid4().hex
    payload["token"] = token
    _sim_cache_put(token, payload)
    # Reuse existing simulate flow by faking a callback
    # Send an inline compare card directly
    try:
        # Build a minimal fake series rendering path by calling the existing simulate handler logic
        # Trigger via internal call path: we cannot fabricate a CallbackQuery easily, so invoke _exec_post and then render
        q_update = update  # placeholder for types
        class _Q:  # tiny shim to reuse below logic
            def __init__(self, message):
                self.data = f"SIM:{token}"
                self.message = message
            async def answer(self, text=None):
                return None
        q = _Q(update.message)
        # Manually call the simulate routine core by composing a minimal payload
        options = payload.get("options") or []
        req = {
            "frame": payload.get("frame") or "CSA",
            "options": list(options)[:3],
            "sizing": payload.get("sizing") or {},
            "baseline": payload.get("baseline") or {"name": "Hold SOL"},
            "horizon_days": payload.get("horizon_days") or 30,
        }
        resp = await _exec_post("simulate", req)
        # Emulate rendering path from on_simulate_scenarios (inline card)
        raw_series = resp.get("series")
        series = [s for s in raw_series if isinstance(s, dict)] if isinstance(raw_series, list) else []
        goal_text = payload.get("summary") or payload.get("playback_text") or payload.get("goal") or "Your goal"
        cap = _capability_snapshot()
        deltas = _series_deltas(series)
        max_abs = max([abs(d[1]) for d in deltas] + [0.0])
        header = (
            "📈 Scenario Compare (vs Do-Nothing Baseline)\n"
            f"🎯 Goal: {goal_text} · Unit: {cap.get('unit') or 'SOL'} · Mode: {cap.get('mode') or 'Asset'}\n"
            f"🛡️ Micro ≤ {cap.get('micro_sol')} {cap.get('unit') or 'SOL'} · Impact ≤ {cap.get('impact_cap_bps')/100:.2f}% · Region: {cap.get('region')}\n\n"
        )
        body = ["Baseline (HODL): 0.0000  ───────────"]
        for name, dv in deltas:
            bar = _ascii_bar(dv, max_abs)
            body.append(f"{name}:  {_fmt_delta(dv, cap.get('unit') or 'SOL'):>7}  {bar}")
        footer = "\n\n• Bars scaled to today's best outcome.\n• Quotes expire; refresh if TTL shows 0s."
        compare_text = header + "\n".join(body) + footer
        buttons = []
        tracks = _build_scenario_tracks(payload)
        if _is_approve_micro_enabled(tracks, float(cap.get("micro_sol") or 0.05)):
            buttons.append([InlineKeyboardButton("✅ Approve ≤ micro", callback_data=f"approve_micro:{token}")])
        buttons.append([InlineKeyboardButton("📝 Edit Goal", callback_data="edit_goal")])
        buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_compare:{token}")])
        buttons.append([InlineKeyboardButton("🛑 Cancel", callback_data="cancel")])
        keyboard = InlineKeyboardMarkup(buttons)
        await update.message.reply_text(compare_text, reply_markup=keyboard)
        return
    except Exception:
        logging.exception("simulate:render failed")
        return await update.message.reply_text("⚠️ Couldn’t load quotes. Tap Refresh.")
async def unstake_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return await update.message.reply_text("🚫 Not allowed.")
    argv = _args(ctx)
    raw = (update.message.text or "").lower()
    if (not argv) or ("help" in raw) or (len(argv) < 2):
        logging.info("help:unstake")
        return await update.message.reply_text(UNSTAKE_HELP)
    token = _norm(argv[0])
    try:
        amount = _parse_amount(argv[1])
    except Exception as err:
        return await update.message.reply_text(ERR_MISSING)
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
    a.add_handler(CommandHandler("check", check_menu))
    a.add_handler(CommandHandler("do", do_menu))
    a.add_handler(CommandHandler("grow", grow_menu))
    a.add_handler(CommandHandler("goal", goal_cmd))
    a.add_handler(CommandHandler("earn", earn_cmd))
    a.add_handler(CommandHandler("ping", ping))
    a.add_handler(CommandHandler("plan", plan_cmd))
    a.add_handler(CommandHandler(["simulate", "sim"], simulate_cmd))
    # keep callback handler for legacy clients; web app button is preferred
    a.add_handler(CallbackQueryHandler(on_simulate_scenarios, pattern=r"^SIM:"))
    a.add_handler(CallbackQueryHandler(on_option_button, pattern=r"^opt:"))  # option CTAs
    a.add_handler(CallbackQueryHandler(on_action_button, pattern=r"^run:"))  # primitive CTAs
    a.add_handler(CommandHandler("balance", balance_cmd))
    a.add_handler(CommandHandler("quote",   quote_cmd))
    a.add_handler(CommandHandler("swap",    swap_cmd))
    a.add_handler(CommandHandler("stake",   stake_cmd))
    a.add_handler(CommandHandler("unstake", unstake_cmd))
    # Natural-language router for plain text (no leading slash)
    a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, nl_router))
    a.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

# ---------- run (blocking; PTB manages the event loop)
def main():
    add_handlers(app)
    logging.info("Planner wired? %s from %s", llm_plan is not None, getattr(llm_plan, "__module__", None))
    if USE_POLLING:
        logging.info("Starting in polling mode (USE_POLLING=1)")
        # Polling mode bypasses webhook delivery; good for quick recovery and tests
        app.run_polling()
    else:
        # Webhook + mini webapp
        web_app = _build_web_app(app)
        if WEBHOOK_BASE_URL:
            logging.info("Serving at 0.0.0.0:%s and expecting webhook at %s/webhook/%s", PORT, WEBHOOK_BASE_URL, WEBHOOK_SECRET)
        else:
            logging.warning("WEBHOOK_BASE_URL not set; Telegram webhook cannot be reconciled")
        web.run_app(web_app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
