EMOJI = {
    # headers
    "header_goal": "🧠",
    "header_summary": "📋",
    "header_strats": "🚀",
    "header_risks": "⚠️",
    # summary bullets
    "bullet_count": "📦",
    "bullet_size": "🪙",
    "bullet_frame": "🧭",
    # strategy types
    "strat_staking": "🌱",
    "strat_bluechip": "💎",
    "strat_momentum": "📈",
    "strat_arbitrage": "🔀",
    "strat_experimental": "🧪",
    "strat_defensive": "🛡️",
    # risks
    "risk_market": "📉",
    "risk_protocol": "🛠️",
    "risk_stablecoin": "💸",
    "risk_liquidity": "🔄",
    "risk_execution": "⏳",
    # actions
    "simulate": "▶️",
}


STRATEGY_TYPE_MAP = {
    # normalize playback types/keywords to canonical buckets
    "staking": "strat_staking",
    "yield": "strat_staking",
    "jito": "strat_staking",
    "stsol": "strat_staking",
    "msol": "strat_staking",
    "bsol": "strat_staking",
    "bluechip": "strat_bluechip",
    "stable": "strat_bluechip",
    "stablecoin": "strat_bluechip",
    "usdc": "strat_bluechip",
    "momentum": "strat_momentum",
    "trend": "strat_momentum",
    "narrative": "strat_momentum",
    "arbitrage": "strat_arbitrage",
    "rotation": "strat_arbitrage",
    "hedge": "strat_arbitrage",
    "defensive": "strat_defensive",
    "riskoff": "strat_defensive",
    "experimental": "strat_experimental",
    "new": "strat_experimental",
}


def pick_strategy_emoji(playback_type_or_name: str) -> str:
    key = (playback_type_or_name or "").lower()
    for k, v in STRATEGY_TYPE_MAP.items():
        if k in key:
            return EMOJI[v]
    # fallback heuristics on name words
    if any(x in key for x in ("stake", "jito", "stsol", "msol", "bsol")):
        return EMOJI["strat_staking"]
    if any(x in key for x in ("usdc", "bluechip", "stable")):
        return EMOJI["strat_bluechip"]
    if any(x in key for x in ("momentum", "pump", "pyth", "trend")):
        return EMOJI["strat_momentum"]
    return EMOJI["strat_experimental"]


