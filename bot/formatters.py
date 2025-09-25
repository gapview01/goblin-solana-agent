from __future__ import annotations

import html
from typing import Optional


def shortName(raw: str, maxLen: int = 18) -> str:
    s = " ".join((raw or "").split())
    if len(s) <= maxLen:
        return s
    stop = {"of", "the", "and", "to"}
    suffix = {"strategy", "track", "plan", "path"}
    words = [w for w in s.split(" ") if w.lower() not in stop]
    words = [w for w in words if w.lower() not in suffix]
    out = " ".join(words) or s
    if len(out) > maxLen:
        out = out[: maxLen - 1] + "…"
    return out


def padName(name: str, width: int = 18) -> str:
    s = (name or "")
    if len(s) >= width:
        return s
    return s + (" " * (width - len(s)))


def formatDelta(delta_sol: Optional[float], delta_usd: Optional[float]) -> str:
    if delta_sol is not None:
        sign = "+" if delta_sol > 0 else ""
        return f"{sign}{delta_sol:.4f} SOL"
    if delta_usd is not None:
        return f"-${abs(delta_usd):.2f} cost"
    return "0.0000 SOL"


def barFor(delta_sol: Optional[float], maxDelta: float) -> str:
    if delta_sol is None or delta_sol <= 0 or maxDelta <= 0:
        return "░░░░░"
    length = max(0, min(18, round(18 * (delta_sol / maxDelta))))
    return "█" * length


def escape_markdown(text: str) -> str:
    # Not used if we render as HTML; provided for completeness
    replacements = {"_": "\\_", "*": "\\*", "[": "\\[", "]": "\\]", "(": "\\(", ")": "\\)",
                    "~": "\\~", "`": "\\`", ">": "\\>", "#": "\\#", "+": "\\+", "-": "\\-",
                    "=": "\\=", "|": "\\|", "{": "\\{", "}": "\\}", ".": "\\.", "!": "\\!"}
    return "".join(replacements.get(ch, ch) for ch in text or "")


def escape_html(text: str) -> str:
    return html.escape(text or "")


