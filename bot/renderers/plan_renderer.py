from __future__ import annotations

from typing import Any, Dict, List

from bot.design_system import EMOJI


def _shorten_words(text: str, max_words: int = 12) -> str:
    words = (text or "").split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def _fmt_size(v: float) -> str:
    try:
        return (f"{float(v):.3f}").rstrip("0").rstrip(".")
    except Exception:
        return str(v)

def _clamp_line(text: str, max_chars: int = 80) -> str:
    s = text.strip()
    if len(s) <= max_chars:
        return s
    # keep whole words; trim and add ellipsis
    cut = s[: max_chars - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


_ABBREV_MAP = {
    "CSA": "Conservative · Standard · Aggressive",
    "LSD": "liquid staking",
    "APR": "annual percentage rate",
    "TVL": "total value locked",
    "DeFi": "decentralized finance",
    "defi": "decentralized finance",
}


def _expand_abbrev(text: str) -> str:
    out = text or ""
    for k, v in _ABBREV_MAP.items():
        out = out.replace(k, v)
    return out


def render_plan_simple(goal: str, playback: Dict[str, Any]) -> str:
    options = playback.get("options") or []
    num_strats = min(3, len(options))
    sizing = playback.get("sizing") or {}
    size_sol = sizing.get("final_sol") or sizing.get("desired_sol") or 0.0
    frame = _expand_abbrev(str(playback.get("frame") or "Conservative · Standard · Aggressive"))

    # Header: Goal
    lines: List[str] = []
    # Disclaimer above goal (brand rule)
    lines.append("⚠️ DISCLAIMER - NOT FINANCIAL ADVICE")
    lines.append("")
    lines.append(f"{EMOJI['header_goal']} Goal: {goal}")
    lines.append("")

    # Plan Summary
    lines.append(f"{EMOJI['header_summary']} Plan Summary")
    lines.append(_clamp_line(f"- {EMOJI['bullet_count']} {num_strats or len(options)} strategies generated"))
    lines.append(_clamp_line(f"- {EMOJI['bullet_size']} Using ~{_fmt_size(size_sol)} SOL (after buffer)"))
    lines.append(_clamp_line(f"- {EMOJI['bullet_frame']} Frame: {_expand_abbrev(frame)}"))
    lines.append("")

    # Strategies (up to 3)
    lines.append(f"{EMOJI['header_strats']} Strategies")
    for opt in options[:3]:
        name = str(opt.get("name") or "Strategy").strip()[:40].rstrip()
        why = _expand_abbrev(str(opt.get("strategy") or opt.get("rationale") or opt.get("bucket") or "").strip())
        lines.append(_clamp_line(f"- {name} — {_summarize(_shorten_words(why, 12), 160)}"))
    lines.append("")

    # Risks (map or default)
    lines.append(f"{EMOJI['header_risks']} Risks")
    risks = playback.get("risks") or []
    if not risks:
        lines.append(_clamp_line(f"- Market volatility"))
        lines.append(_clamp_line(f"- Protocol risk (staking/contracts)"))
        lines.append(_clamp_line(f"- Stablecoin depeg (USDC/USDT)"))
        lines.append(_clamp_line(f"- Slippage or price impact if liquidity is low"))
    else:
        # Best-effort mapping; cap at 4
        mapped: List[str] = []
        for r in risks:
            s = str(r).lower()
            if any(k in s for k in ("market", "price", "volat")) and "- Market" not in "\n".join(mapped):
                mapped.append(_clamp_line(f"- {str(r)}"))
            elif any(k in s for k in ("protocol", "contract", "smart")):
                mapped.append(_clamp_line(f"- {str(r)}"))
            elif any(k in s for k in ("usdc", "stable", "counterparty")):
                mapped.append(_clamp_line(f"- {str(r)}"))
            elif any(k in s for k in ("liquidity", "impact", "slippage")):
                mapped.append(_clamp_line(f"- {str(r)}"))
            if len(mapped) >= 4:
                break
        if not mapped:
            mapped = [
                _clamp_line(f"- Market volatility"),
                _clamp_line(f"- Protocol risk (staking/contracts)"),
                _clamp_line(f"- Stablecoin depeg (USDC/USDT)"),
                _clamp_line(f"- Slippage or price impact if liquidity is low"),
            ]
        lines.extend(mapped[:4])

    lines.append("")
    lines.append(f"{EMOJI['simulate']} /simulate  — simulate scenarios")

    return "\n".join(lines)


