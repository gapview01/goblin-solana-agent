# planner/llm_planner.py
"""
LLM planner (single-file, rich + autonomous campaign spec + balance-aware + wildcard tokens + robust output)

What this file does
- Turns a fuzzy goal into a safe, structured plan the server can render:
  • summary, token_candidates (optional), options (names are free-form), risks, simulation,
    and a root `actions` list for execution buttons.
- Adds an autonomous `campaign` spec used by your OODA loop:
  • universe filters (category/age/mcap), style/factor weights, target weights (optional),
    rebalance rule (time/drift), budgets (per_trade/per_tick/per_campaign), denomination.
- Balance-aware sizing:
  • The LLM is instructed to size amounts ≤ (sol_balance - fee_buffer)
  • Post-processing clamps all amounts to per-action cap & remaining budget
- Wildcard tokens:
  • If policy.allowed_tokens = ["*"], model may consider any Jupiter-resolvable token,
    but is nudged to prefer min_token_mcap_usd and deep-liquidity assets.
- Robustness:
  • Normalizes/defensively parses `options`
  • Never crashes on unexpected shapes; always returns valid JSON

Env knobs (defaults)
- AUTO_MICRO_SOL=0.05
- HARD_CAP_SOL=0.25
- ALLOWED_TOKENS="SOL,USDC,JITOSOL"   # server may override to ["*"]
- ALLOWED_PROTOCOLS="jito,jupiter"
- MAX_PRICE_IMPACT_BPS=200
- MIN_FEE_BUFFER_SOL=0.003
- MIN_TOKEN_MCAP_USD=15000000
- PLANNER_MODEL="gpt-4.1"
- OPENAI_API_KEY / OPENAI_ORG / OPENAI_PROJECT

Public API
- plan(...) -> JSON string (server uses this)
- generate_plan(...) -> dict (internal)
"""

from __future__ import annotations

import json, os, logging, re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List, Tuple

from dotenv import load_dotenv
load_dotenv()

# --------------------------- config & policy ---------------------------------

ALLOWED_VERBS = {"balance", "quote", "swap", "stake", "unstake"}

DEFAULT_POLICY = {
    "auto_micro_sol": float(os.getenv("AUTO_MICRO_SOL", "0.05")),
    "hard_cap_sol":   float(os.getenv("HARD_CAP_SOL",   "0.25")),
    "allowed_tokens": (os.getenv("ALLOWED_TOKENS", "SOL,USDC,JITOSOL")
                       .replace(" ", "").split(",")),
    "allowed_protocols": (os.getenv("ALLOWED_PROTOCOLS", "jito,jupiter")
                          .replace(" ", "").split(",")),
    "max_price_impact_bps": int(os.getenv("MAX_PRICE_IMPACT_BPS", "200")),
    "min_fee_buffer_sol":   float(os.getenv("MIN_FEE_BUFFER_SOL", "0.003")),
    "min_token_mcap_usd":   float(os.getenv("MIN_TOKEN_MCAP_USD", "15000000")),
}

PLANNER_MODEL = os.getenv("PLANNER_MODEL", "gpt-4.1")
OPENAI_ORG    = os.getenv("OPENAI_ORG") or None
OPENAI_PROJ   = os.getenv("OPENAI_PROJECT") or None

# ----------------------------- utils -----------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _coerce_str_amount(v: Any, default: str = "0.10") -> str:
    try:
        if isinstance(v, (int, float)):
            return f"{float(v):.9f}".rstrip("0").rstrip(".")
        s = str(v).strip()
        if re.fullmatch(r"[0-9]*\.?[0-9]+", s):
            return s
    except Exception:
        pass
    return default

def _to_float_amount(v: Any, fallback: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        try:
            return float(str(v).strip())
        except Exception:
            return fallback

def _extract_text_from_openai_response(resp: Any) -> str:
    # Responses API convenience
    txt = getattr(resp, "output_text", None)
    if isinstance(txt, str) and txt.strip():
        return txt
    # Raw Responses API path
    try:
        return resp.output[0].content[0].text  # type: ignore[attr-defined,index]
    except Exception:
        pass
    # Chat Completions fallback
    try:
        return resp.choices[0].message.content  # type: ignore[attr-defined,index]
    except Exception:
        pass
    return ""

# ---------------------------- balance helpers --------------------------------

def _effective_caps(policy: Dict[str, Any],
                    wallet_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    sol = 0.0
    if wallet_state:
        try:
            sol = float(wallet_state.get("sol_balance") or wallet_state.get("SOL") or 0.0)
        except Exception:
            sol = 0.0
    fee_buf = float(policy.get("min_fee_buffer_sol") or 0.003)
    hard_cap = float(policy.get("hard_cap_sol") or 0.25)
    max_affordable = max(0.0, sol - fee_buf)
    per_action_cap = max(0.0, min(hard_cap, max_affordable))
    return {
        "sol_balance": sol,
        "fee_buffer": fee_buf,
        "max_affordable_sol": max_affordable,
        "per_action_cap_sol": per_action_cap,
    }

# ---------------------------- LLM core ---------------------------------------

_SYSTEM_PROMPT = (
    "You are a DeFi planning assistant for Solana.\n"
    "Output a single JSON object ONLY. Do not include prose outside JSON.\n\n"
    "Brand voice (tone & wording inside JSON fields): minimalistic, edgy, fun, open, open-source, transparent, direct.\n"
    "Keep names short and one-line rationales; avoid hype and long paragraphs.\n"
    "Spell out abbreviations (e.g., say 'decentralized finance' instead of 'DeFi').\n\n"
    "Hard rules (must comply):\n"
    "• Use ONLY verbs: balance, quote, swap, stake, unstake.\n"
    "• ≤ 3 actions per plan; include 'balance' as the first action; ensure 'quote' precedes any 'swap'.\n"
    "• Stay within policy.allowed_tokens & policy.allowed_protocols and policy.hard_cap_sol.\n"
    "• If policy.allowed_tokens contains \"*\", you may consider ANY Solana token resolvable by Jupiter; "
    "  prefer tokens with estimated market cap ≥ policy.min_token_mcap_usd and deep liquidity.\n"
    "• Respect max_price_impact_bps by suggesting a quote gate (DO NOT execute if impact exceeds cap).\n"
    "• For each action include: verb, params, why, risk, requires_approval (bool).\n"
    "• Params for quote/swap: must include 'in', 'out', and 'amount' (string, e.g. '0.10').\n"
    "• Params for stake/unstake: must include 'protocol' and 'amount' (string).\n"
    "• Echo the provided 'policy' EXACTLY under a 'policy' key in your output.\n"
    "• If wallet_state.sol_balance is present, size amounts so that TOTAL SOL used ≤ (sol_balance - policy.min_fee_buffer_sol).\n"
    "• Provide 1–3 options (names are free-form). Each has a short strategy, plan (actions list), rationale, and tradeoffs.\n"
    "• Choose a default option (usually balanced) and mirror its 'plan' to a root-level 'actions' array (for execution UIs).\n"
    "• Include optional 'token_candidates' (array) with items like {symbol, rationale, risks}.\n"
    "• Include a short 'summary', a 'risks' list, and a 'simulation' section describing dry-run quotes & criteria.\n"
    "• Additionally, include a 'campaign' block for autonomous ticks with fields:\n"
    "  - universe.filters: {category?, source?, min_mcap_usd?, max_age_days?}\n"
    "  - style: {momentum?, value?, quality?, growth?} or a named style\n"
    "  - weights (optional explicit targets): {SYMBOL: weight}\n"
    "  - rebalance: {method: 'time'|'drift', cadence?: '1h'|'1d'|..., drift_bps?: int}\n"
    "  - budgets: {per_trade_sol, per_tick_sol, per_campaign_sol}\n"
    "  - denomination: 'SOL'|'USD'|... and optional 'dividend': {pct_yield_to_usdc?: number, cadence?: '1w'|'1m'}\n"
)

def _llm_plan_call(payload: Dict[str, Any]) -> Dict[str, Any]:
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                    organization=OPENAI_ORG, project=OPENAI_PROJ)
    try:
        resp = client.responses.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload)},
            ],
            response_format={"type": "json_object"},
            temperature=0.5,
        )
        text = _extract_text_from_openai_response(resp)
        if not text:
            raise RuntimeError("empty_response_text")
        return json.loads(text)
    except Exception as e_responses:
        try:
            cc = client.chat.completions.create(
                model=PLANNER_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload)},
                ],
                temperature=0.5,
            )
            text = _extract_text_from_openai_response(cc) or ""
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
            if not text:
                raise RuntimeError("empty_chat_completion_text")
            return json.loads(text)
        except Exception as e_chat:
            raise RuntimeError(
                f"llm_error: responses={type(e_responses).__name__} chat={type(e_chat).__name__}"
            ) from e_chat

# ------------------------ balance-aware clamping ------------------------------

def _clamp_amount_to_budget_and_cap(
    amount_str: Any,
    remaining_budget: float,
    per_action_cap: float,
    default_if_zero: float,
) -> Tuple[str, float]:
    requested = max(0.0, _to_float_amount(amount_str, fallback=0.0))
    if requested <= 0.0:
        requested = max(0.0, default_if_zero)
    allowed = min(per_action_cap, remaining_budget)
    final = max(0.0, min(requested, allowed))
    new_remaining = max(0.0, remaining_budget - final)
    return _coerce_str_amount(final, default=_coerce_str_amount(default_if_zero)), new_remaining

def _sanitize_actions(
    actions: Optional[List[Dict[str, Any]]],
    chain: str = "solana",
    max_actions: int = 3,
    remaining_budget_sol: Optional[float] = None,
    per_action_cap_sol: Optional[float] = None,
    auto_micro_sol: Optional[float] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in actions or []:
        verb = (a.get("verb") or "").lower().strip()
        if verb not in ALLOWED_VERBS:
            continue
        params = dict(a.get("params") or {})

        a["chain"] = chain
        a.setdefault("why", "")
        a.setdefault("risk", "")
        a.setdefault("requires_approval", verb in {"swap", "stake", "unstake"})
        a["timestamp"] = a.get("timestamp") or _now_iso()

        if verb in {"quote", "swap", "stake", "unstake"} and remaining_budget_sol is not None and per_action_cap_sol is not None:
            amt_str = params.get("amount")
            amt_str, remaining_budget_sol = _clamp_amount_to_budget_and_cap(
                amt_str,
                remaining_budget_sol,
                per_action_cap_sol,
                default_if_zero=float(auto_micro_sol or 0.05),
            )
            params["amount"] = amt_str

        if verb in {"quote", "swap"}:
            if not all(k in params for k in ("in", "out")):
                continue
            params["amount"] = _coerce_str_amount(
                params.get("amount"),
                default=_coerce_str_amount(auto_micro_sol or 0.05),
            )
        if verb in {"stake", "unstake"}:
            params.setdefault("protocol", params.get("protocol", "jito"))
            params["amount"] = _coerce_str_amount(
                params.get("amount"),
                default=_coerce_str_amount(auto_micro_sol or 0.05),
            )
            a["params"] = params

        out.append({
            "verb": verb,
            "params": params,
            "why": a.get("why", ""),
            "risk": a.get("risk", ""),
            "requires_approval": a["requires_approval"],
            "timestamp": a["timestamp"],
            "chain": chain,
        })
        if len(out) >= max_actions:
            break

    verbs = [x["verb"] for x in out]
    if "balance" not in verbs:
        out.insert(0, {
            "verb": "balance",
            "params": {},
            "why": "Confirm available funds & fee buffer",
            "risk": "none",
            "requires_approval": False,
            "timestamp": _now_iso(),
            "chain": chain
        })
        verbs = ["balance"] + verbs

    try:
        swap_idx = verbs.index("swap")
        if "quote" in verbs:
            quote_idx = verbs.index("quote")
            if quote_idx > swap_idx:
                q = out.pop(quote_idx)
                out.insert(swap_idx, q)
    except ValueError:
        pass

    return out[:max_actions]

# --------------------------- normalization helpers ---------------------------

def _as_option_dict(item: Any, default_actions: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    if isinstance(item, dict):
        if "plan" not in item:
            item["plan"] = default_actions or []
        return item
    if isinstance(item, str):
        s = item.strip()
        try:
            j = json.loads(s)
            if isinstance(j, dict):
                if "plan" not in j:
                    j["plan"] = default_actions or []
                return j
        except Exception:
            pass
        return {
            "name": s or "Option",
            "strategy": "Validate funds first.",
            "rationale": "Normalized from string option.",
            "tradeoffs": {"pros": [], "cons": []},
            "plan": default_actions or [{
                "verb": "balance",
                "params": {},
                "why": "Check available funds before any action.",
                "risk": "none",
                "requires_approval": False,
                "timestamp": _now_iso(),
                "chain": "solana",
            }],
        }
    return {
        "name": str(item),
        "plan": default_actions or [{
            "verb": "balance",
            "params": {},
            "why": "Check available funds before any action.",
            "risk": "none",
            "requires_approval": False,
            "timestamp": _now_iso(),
            "chain": "solana",
        }],
    }

# --------------------------- public API --------------------------------------

def generate_plan(goal: str,
                  wallet_state: Optional[Dict[str, Any]] = None,
                  market_data: Optional[Dict[str, Any]] = None,
                  policy: Optional[Dict[str, Any]] = None,
                  chain: str = "solana",
                  max_actions: int = 3) -> Dict[str, Any]:
    """
    Returns a dict with:
      summary, understanding, policy, token_candidates?, options, default_option,
      actions (root = default option plan), risks, simulation, budget,
      campaign (for autonomous ticks), generated_at
    """
    merged_policy = {**DEFAULT_POLICY, **(policy or {})}
    hints = _effective_caps(merged_policy, wallet_state)

    user_payload = {
        "goal": goal,
        "chain": chain,
        "policy": merged_policy,
        "wallet_state": wallet_state or {},
        "market_data": market_data or {},
        "constraints": {
            "max_actions": max_actions,
            "allowed_verbs": sorted(ALLOWED_VERBS),
            "max_price_impact_bps": merged_policy["max_price_impact_bps"],
        },
        "balance_hints": hints,
        "ui_notes": {
            "buttons_require_actions": True,
            "default_option_should_drive_actions": True
        }
    }

    try:
        model_plan = _llm_plan_call(user_payload)
    except Exception as e:
        logging.exception("LLM planner call failed: %s", e)
        # Minimal safe fallback (still balance-aware)
        fallback_amt = hints["per_action_cap_sol"] or merged_policy["auto_micro_sol"]
        model_plan = {
            "summary": f"Plan for: {goal}",
            "policy": merged_policy,
            "options": [{
                "name": "Standard",
                "strategy": "Validate funds, get a quote, then stake via jito if impact is acceptable.",
                "rationale": "Low complexity, yield-first.",
                "tradeoffs": {"pros": ["Earn yield"], "cons": ["Subject to price moves"]},
                "plan": [{
                    "verb": "balance",
                    "params": {},
                    "why": "Confirm available funds & fee buffer",
                    "risk": "none",
                    "requires_approval": False,
                    "timestamp": _now_iso(), "chain": chain
                },{
                    "verb": "quote",
                    "params": {"in": "SOL", "out": "JITOSOL", "amount": _coerce_str_amount(fallback_amt)},
                    "why": "Check price & route; gate by price impact",
                    "risk": "market slippage",
                    "requires_approval": False,
                    "timestamp": _now_iso(), "chain": chain
                },{
                    "verb": "stake",
                    "params": {"protocol": "jito", "amount": _coerce_str_amount(fallback_amt)},
                    "why": "Stake SOL → JitoSOL for yield",
                    "risk": "smart contract risk",
                    "requires_approval": True,
                    "timestamp": _now_iso(), "chain": chain
                }]
            }],
            "default_option": "Standard",
            "risks": ["Price impact", "Slippage", "Protocol risk"],
            "simulation": {
                "steps": [
                    "Fetch a quote for SOL→JITOSOL with requested amount and slippage.",
                    "If price impact > policy.max_price_impact_bps, reduce amount and re-quote."
                ],
                "success_criteria": {"max_price_impact_bps": merged_policy["max_price_impact_bps"]},
                "notes": "Use dry-run quotes before committing."
            },
            "campaign": {
                "universe": {"filters": {"min_mcap_usd": merged_policy["min_token_mcap_usd"]}},
                "style": {"momentum": 0.5, "value": 0.5},
                "weights": {},
                "rebalance": {"method": "time", "cadence": "1d"},
                "budgets": {"per_trade_sol": fallback_amt, "per_tick_sol": min(0.25, hints["max_affordable_sol"]), "per_campaign_sol": min(1.0, hints["max_affordable_sol"])},
                "denomination": "SOL"
            }
        }

    # Ensure dict (some providers may hand back a JSON string)
    if isinstance(model_plan, str):
        try:
            model_plan = json.loads(model_plan)
        except Exception:
            logging.error("Planner returned non-JSON string; using empty shell.")
            model_plan = {}

    # Normalize options
    default_name = str(model_plan.get("default_option") or "Standard").strip()
    raw_options = model_plan.get("options") or model_plan.get("choices") or []
    if not isinstance(raw_options, list):
        raw_options = [raw_options]
    norm_options: List[Dict[str, Any]] = [_as_option_dict(o, model_plan.get("actions") or []) for o in raw_options]
    model_plan["options"] = norm_options

    # Choose default actions
    chosen = next((opt for opt in norm_options
                   if str(opt.get("name", "")).strip().lower() == default_name.lower()), None)
    chosen_actions = (chosen or {}).get("plan") or model_plan.get("actions") or []

    # Clamp default actions
    remaining = hints["max_affordable_sol"]
    actions = _sanitize_actions(
        chosen_actions,
        chain=chain,
        max_actions=max_actions,
        remaining_budget_sol=remaining,
        per_action_cap_sol=hints["per_action_cap_sol"],
        auto_micro_sol=merged_policy["auto_micro_sol"],
    )

    # Clamp each option plan (display safety)
    sanitized_options: List[Dict[str, Any]] = []
    for opt in norm_options:
        rem = hints["max_affordable_sol"]
        plan_actions = _sanitize_actions(
            opt.get("plan"),
            chain=chain,
            max_actions=max_actions,
            remaining_budget_sol=rem,
            per_action_cap_sol=hints["per_action_cap_sol"],
            auto_micro_sol=merged_policy["auto_micro_sol"],
        )
        sanitized_options.append({**opt, "plan": plan_actions})

    # Campaign defaults/echo
    campaign = model_plan.get("campaign") or {}
    if "budgets" not in campaign:
        campaign["budgets"] = {
            "per_trade_sol": merged_policy["auto_micro_sol"],
            "per_tick_sol": min(0.25, hints["max_affordable_sol"]),
            "per_campaign_sol": min(1.0, hints["max_affordable_sol"]),
        }
    if "denomination" not in campaign:
        campaign["denomination"] = "SOL"
    if "universe" not in campaign:
        campaign["universe"] = {"filters": {"min_mcap_usd": merged_policy["min_token_mcap_usd"]}}
    if "rebalance" not in campaign:
        campaign["rebalance"] = {"method": "time", "cadence": "1d"}

    out = {
        "summary": model_plan.get("summary") or f"Plan for: {goal}",
        "understanding": model_plan.get("understanding", {
            "goal_rewrite": goal,
            "intent": "Grow within safe limits",
            "constraints": {"max_actions": max_actions}
        }),
        "policy": merged_policy,
        "token_candidates": model_plan.get("token_candidates", []),
        "options": sanitized_options or norm_options,
        "default_option": default_name or "Standard",
        "actions": actions,  # execution buttons read from here
        "risks": model_plan.get("risks", []),
        "simulation": model_plan.get("simulation", {}),
        "budget": {
            "sol_balance": hints["sol_balance"],
            "fee_buffer": hints["fee_buffer"],
            "max_affordable_sol": hints["max_affordable_sol"],
            "per_action_cap_sol": hints["per_action_cap_sol"],
        },
        "campaign": campaign,
        "generated_at": _now_iso(),
    }
    return out

def plan(goal: Optional[str] = None,
         wallet_state: Optional[Dict[str, Any]] = None,
         market_data: Optional[Dict[str, Any]] = None,
         policy: Optional[Dict[str, Any]] = None,
         chain: str = "solana",
         max_actions: int = 3,
         text: Optional[str] = None,
         source: Optional[str] = None,
         **kwargs: Any) -> str:
    """
    Returns a JSON string the server renders. Accepts either `goal` or `text`.
    """
    the_goal = (goal or text or "").strip() or "Plan a safe Solana DeFi action."
    result = generate_plan(
        goal=the_goal,
        wallet_state=wallet_state,
        market_data=market_data,
        policy=policy,
        chain=chain,
        max_actions=max_actions,
    )
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

# ---------------------------- local test -------------------------------------
if __name__ == "__main__":
    demo = plan(
        goal="Seek alpha with memecoins but stay safe; small daily rebalance; pay me 20% yield as dividend",
        wallet_state={"sol_balance": 0.42},
        policy={"allowed_tokens": ["*"], "min_token_mcap_usd": 15000000}
    )
    print(demo)