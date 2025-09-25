import re
from typing import Optional, Tuple

_RE_QUOTE = re.compile(r"^\s*quote\s+(?P<amount>[\d.]+)\s+(?P<from>\w+)\s+to\s+(?P<to>\w+)\s*$", re.I)
_RE_SWAP  = re.compile(r"^\s*swap\s+(?P<amount>[\d.]+)\s+(?P<from>\w+)\s+to\s+(?P<to>\w+)\s*$", re.I)
_RE_STAKE = re.compile(r"^\s*stake\s+(?P<amount>[\d.]+)\s+(?P<token>\w+)\s*$", re.I)
_RE_UNSTK = re.compile(r"^\s*unstake\s+(?P<amount>[\d.]+)\s+(?P<token>\w+)\s*$", re.I)
_RE_PLAN  = re.compile(r"^\s*plan\s+(?P<goal>.+)$", re.I)

def _norm(sym: str) -> str:
    return (sym or "").upper()

def parse_quote(text: str) -> Optional[str]:
    m = _RE_QUOTE.match(text or "")
    if not m: return None
    amt = m.group("amount"); f = _norm(m.group("from")); t = _norm(m.group("to"))
    return f"/quote {f} {t} {amt}"

def parse_swap(text: str) -> Optional[str]:
    m = _RE_SWAP.match(text or "")
    if not m: return None
    amt = m.group("amount"); f = _norm(m.group("from")); t = _norm(m.group("to"))
    return f"/swap {f} {t} {amt}"

def parse_stake(text: str) -> Optional[str]:
    m = _RE_STAKE.match(text or "")
    if not m: return None
    amt = m.group("amount"); tok = _norm(m.group("token"))
    return f"/stake {tok} {amt}"

def parse_unstake(text: str) -> Optional[str]:
    m = _RE_UNSTK.match(text or "")
    if not m: return None
    amt = m.group("amount"); tok = _norm(m.group("token"))
    return f"/unstake {tok} {amt}"

def parse_plan(text: str) -> Optional[str]:
    m = _RE_PLAN.match(text or "")
    if not m: return None
    goal = m.group("goal").strip()
    return f"/plan {goal}"


