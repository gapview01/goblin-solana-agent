START_TEXT = """👾 GoblinBot Ready ✅

💰 /check (balance, quote)
🛠️ /do (swap, stake, unstake)
🌱 /grow (plan, scale, earn)
"""

CHECK_TEXT = """💰 Check Options:
- 👛 /balance — see tokens in your wallet
- 📊 /quote — estimate what you’d receive on a swap
"""

DO_TEXT = """🛠️ Do Options:
- 🔄 /swap     — trade one token for another
- 🌱 /stake    — deposit to earn
- 📤 /unstake  — withdraw your deposit
"""

GROW_MENU_TEXT = """🌱 Grow Options:
- 🧠 /plan — set a goal & get a plan (Takes ~7 mins ⏳😅)
- 📈 /scale — grow 1 SOL to 10 SOL
- 💹 /earn — earn yield on your SOL this month
"""

QUOTE_HELP = """📊 Quote time!
Pick one or type your own:

- 🔄 /quote SOL USDC 0.5
- 💰 /quote SOL BONK 1
- ✍️ Custom — type: /quote FROM TO AMOUNT

(Advanced: add [slippage_bps], e.g. /quote SOL USDC 0.5 100)
"""

SWAP_HELP = """🔄 Let’s swap.
Pick one or type your own:

- 🔄 /swap SOL USDC 0.5
- 🔁 /swap USDC SOL 25
- ✍️ Custom — type: /swap FROM TO AMOUNT

Defaults: slippage 1.0%
(Advanced: add [slippage_bps], e.g. /swap SOL USDC 0.5 100)
"""

STAKE_HELP = """🌱 Stake to earn.
Examples:
- 🌱 /stake SOL 1
- 🌱 /stake mSOL 2
- ✍️ Custom — type: /stake TOKEN AMOUNT
"""

UNSTAKE_HELP = """📤 Unstake your deposit.
Examples:
- 📤 /unstake SOL 1
- 📤 /unstake mSOL 2
- ✍️ Custom — type: /unstake TOKEN AMOUNT
"""

PLAN_HELP = """🧠 Set a goal & get a plan.
Use /plan <describe your goals in plain English>
"""

ERR_TOO_MUCH = "⚠️ That’s more than your balance ({balance}). Try a smaller amount or MAX."
ERR_UNKNOWN_TOKEN = "🤔 I don’t recognize “{token}”. Try SOL, USDC, BONK, mSOL, or type the symbol again."
ERR_MISSING = "👀 I need a bit more info. See examples above or type “help”."


# -------- Scenario Compare inline card renderer --------
from typing import Dict, Any, List, Optional
from bot.formatters import shortName, padName, formatDelta, barFor, escape_html


def render_compare_card(plan: Dict[str, Any]) -> str:
    goal = str(plan.get("goal") or plan.get("summary") or "Your goal")
    horizon = str(plan.get("horizon") or "30d")
    region = str(plan.get("region") or (plan.get("cap", {}) or {}).get("region") or "global")
    max_impact = str(plan.get("max_impact") or (plan.get("cap", {}) or {}).get("impact_cap_bps") or "200")
    try:
        if isinstance(max_impact, (int, float)):
            max_impact = f"{float(max_impact)/100:.2f}%"
        elif str(max_impact).isdigit():
            max_impact = f"{int(max_impact)/100:.2f}%"
    except Exception:
        max_impact = str(max_impact)
    micro = str(plan.get("micro_budget") or (plan.get("cap", {}) or {}).get("micro_sol") or "0.05 SOL")

    baseline = plan.get("baseline") or {}
    base_name = baseline.get("display_name") or baseline.get("name") or "Do-Nothing (Baseline)"

    scenarios: List[Dict[str, Any]] = list(plan.get("scenarios") or plan.get("options") or [])
    # compute selection
    def _delta_sol(x: Dict[str, Any]) -> float:
        try:
            v = x.get("delta_sol")
            return float(v) if v is not None else -1e9
        except Exception:
            return -1e9

    nonneg = [s for s in scenarios if (s.get("delta_sol") or 0) >= 0]
    nonneg.sort(key=lambda s: float(s.get("delta_sol") or 0.0), reverse=True)
    top = nonneg[:3]
    if len(top) < 3:
        rest = [s for s in scenarios if s not in top]
        top += rest[: 3 - len(top)]

    max_delta = max([float(s.get("delta_sol") or 0.0) for s in top] + [0.0])

    def _name_of(s: Dict[str, Any]) -> str:
        return str(s.get("display_name") or s.get("name") or s.get("key") or "Scenario")

    # rows
    rows: List[str] = []
    rows.append(f"{padName(shortName(base_name), 18)}:  0.0000 SOL  ─────────────")
    for s in top:
        n = padName(shortName(_name_of(s), 18), 18)
        dsol = s.get("delta_sol")
        dusd = s.get("delta_usd")
        label = formatDelta(dsol if dsol is not None else None, dusd if dusd is not None else None)
        bar = barFor(float(dsol) if dsol is not None else None, float(max_delta))
        rows.append(f"{n}:  {label}  {bar}")

    header = (
        f"📈 Scenario Compare · {escape_html(horizon)}\n\n"
        f"🎯 Goal: {escape_html(goal)}\n"
        f"🛡️ Micro ≤ {escape_html(str(micro))} · Impact ≤ {escape_html(str(max_impact))} · Region: {escape_html(region)}\n\n"
    )
    footer = "\n\n• Quotes expire; refresh if TTL shows 0s."
    body = "\n".join(rows)
    return f"<pre>{header}{escape_html(body)}{footer}</pre>"


# -------- Simple plan renderer (pre-compare summary) --------
def _fmt_sol(v: float, dp: int = 3) -> str:
    try:
        return (f"{float(v):.{dp}f}").rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def _strategy_emoji(name: str, strategy: str) -> str:
    s = f"{name} {strategy}".lower()
    if any(k in s for k in ["stake", "yield", "jito", "msol", "bsol", "lsd"]):
        return "🌱"
    if any(k in s for k in ["stable", "usdc", "preservation", "bluechip", "defensive"]):
        return "💎"
    if any(k in s for k in ["momentum", "trend", "alpha", "growth"]):
        return "📈"
    if any(k in s for k in ["arb", "arbitrage", "basis", "spread"]):
        return "🔀"
    if any(k in s for k in ["experimental", "pilot", "test"]):
        return "🧪"
    return "🌱"


def render_simple_plan(plan: Dict[str, Any]) -> str:
    goal = str(plan.get("goal") or "your goal").strip()
    frame = str(plan.get("frame") or "CSA").strip()
    options = plan.get("options") or []
    num_strats = max(0, min(3, len(options)))
    sizing = plan.get("sizing") or {}
    final_sol = sizing.get("final_sol") or sizing.get("desired_sol") or 0.0

    lines: list[str] = []
    lines.append(f"🧠 Goal: {goal}")
    lines.append("")
    lines.append("📋 Plan Summary")
    lines.append(f"• {num_strats or len(options)} strategies generated")
    if final_sol:
        lines.append(f"• Using ~{_fmt_sol(final_sol)} SOL (after buffer)")
    lines.append(f"• Frame: {frame} (Conservative · Standard · Aggressive)")
    lines.append("")
    lines.append("🚀 Strategies")
    for i, opt in enumerate(options[:3], start=1):
        name = str(opt.get("name") or f"Strategy {i}")
        why = str(opt.get("strategy") or opt.get("rationale") or "").strip() or str(opt.get("bucket") or "")
        emoji = _strategy_emoji(name, why)
        one_liner = " ".join(why.split())[:140]
        lines.append(f"{i}. {emoji} {name} — {one_liner}")
    lines.append("")
    risks = plan.get("risks") or []
    if isinstance(risks, list) and risks:
        lines.append("⚠️ Risks")
        for r in risks[:4]:
            lines.append(f"• {str(r)}")
    lines.append("")
    lines.append("▶️ /simulate  — simulate scenarios")

    return "\n".join(lines)


# Hints/Errors for simulate command
SIMULATE_HINT = "▶️ /simulate  — simulate scenarios"
NO_PLAN_FOUND = "🤔 No recent plan found. Try /plan first."

# -------- Layer-3 prompts for new Grow commands --------
GOAL_HELP = """🧠 Set a goal.
Try:
- 🧠 /goal grow 1 SOL to 10 SOL
- 🧠 /goal earn yield on 2 SOL this month

(You can also describe your goal in plain English.)
"""

EARN_HELP = """💹 Earn with staking.
Examples:
- 💹 /earn SOL 1
- 💹 /earn mSOL 2
- ✍️ Custom — type: /earn TOKEN AMOUNT
"""

# Level-2 Grow (screenshot style)
GROW_TEXT = """🌱 Grow Options:
- 🧠 /plan     — set a goal & get a plan
Takes ~7 minutes ⏳😅
"""

