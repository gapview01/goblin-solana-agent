"""Planner utilities to transform raw planner output into an execution-ready plan."""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Sequence

LAMPORTS_PER_SOL = 1_000_000_000
FEE_BUFFER_LAMPORTS = 500_000
TIP_LAMPORTS = 100_000
RENT_ATA_LAMPORTS = 2_100_000
HARD_CAP_SOL = 0.25
BUFFERS_LAMPORTS = FEE_BUFFER_LAMPORTS + TIP_LAMPORTS + RENT_ATA_LAMPORTS
CSA_BUCKETS = ("Conservative", "Standard", "Aggressive")

__all__ = ["decide_size", "build_exec_ready_plan"]


def decide_size(balance_lamports: int, desired_lamports: int) -> int:
    """Clamp a desired allocation against buffers, wallet balance, and the hard cap."""

    buffers = BUFFERS_LAMPORTS
    affordable = max(0, balance_lamports - buffers)
    hard_cap = int(HARD_CAP_SOL * LAMPORTS_PER_SOL)
    return max(0, min(int(desired_lamports), affordable, hard_cap))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except Exception:
        try:
            return int(float(str(value).strip()))
        except Exception:
            return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        return float(value)
    except Exception:
        try:
            return float(str(value).strip())
        except Exception:
            return default


def _parse_amount_lamports(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, int) and abs(value) > LAMPORTS_PER_SOL:
            return int(value)
        return int(float(value) * LAMPORTS_PER_SOL)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0
        if s.isdigit() and len(s) > 4:
            return int(s)
        try:
            return int(float(s) * LAMPORTS_PER_SOL)
        except Exception:
            return 0
    return 0


def _parse_amount_from_actions(actions: Any) -> int:
    if not isinstance(actions, Sequence):
        return 0
    for step in actions:
        if not isinstance(step, dict):
            continue
        params = step.get("params")
        if not isinstance(params, dict):
            continue
        if "amountLamports" in params:
            lamports = _safe_int(params.get("amountLamports"))
            if lamports > 0:
                return lamports
        amt = params.get("amount")
        lamports = _parse_amount_lamports(amt)
        if lamports > 0:
            return lamports
    return 0


def _extract_desired_lamports(plan: Optional[Dict[str, Any]]) -> int:
    if not isinstance(plan, dict):
        return 0

    sizing = plan.get("sizing")
    if isinstance(sizing, dict):
        lamports = _safe_int(sizing.get("desired_lamports"))
        if lamports > 0:
            return lamports
        sol_amt = _safe_float(sizing.get("desired_sol"))
        if sol_amt > 0:
            return int(sol_amt * LAMPORTS_PER_SOL)

    budget = plan.get("budget")
    if isinstance(budget, dict):
        for key in ("per_trade_sol", "per_action_cap_sol", "per_tick_sol"):
            sol_amt = _safe_float(budget.get(key))
            if sol_amt > 0:
                return int(sol_amt * LAMPORTS_PER_SOL)

    lamports = _parse_amount_from_actions(plan.get("actions"))
    if lamports > 0:
        return lamports

    options = plan.get("options")
    if isinstance(options, Sequence):
        for opt in options:
            lamports = _parse_amount_from_actions(getattr(opt, "get", lambda _: None)("plan"))
            if lamports > 0:
                return lamports
    return 0


def _goal_text(default_goal: str, plan: Optional[Dict[str, Any]]) -> str:
    if isinstance(plan, dict):
        understanding = plan.get("understanding")
        if isinstance(understanding, dict):
            for key in ("goal_rewrite", "goal", "summary"):
                value = understanding.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return default_goal


def _collect_risks(plan: Optional[Dict[str, Any]]) -> List[str]:
    if isinstance(plan, dict):
        risks = plan.get("risks")
        if isinstance(risks, (list, tuple, set)):
            return [str(r) for r in risks if r]
        if isinstance(risks, str) and risks.strip():
            return [risks.strip()]
    return []


def _prepare_options(raw_options: Any) -> List[Dict[str, Any]]:
    options_list: List[Any] = raw_options if isinstance(raw_options, Sequence) else []
    prepared: List[Dict[str, Any]] = []
    for idx, bucket in enumerate(CSA_BUCKETS):
        source = options_list[idx] if idx < len(options_list) else {}
        option = dict(source) if isinstance(source, dict) else {}
        option.setdefault("name", bucket)
        option["bucket"] = bucket
        plan_steps = option.get("plan")
        if isinstance(plan_steps, list):
            option["plan"] = plan_steps
        elif isinstance(plan_steps, Sequence):
            option["plan"] = list(plan_steps)
        else:
            option["plan"] = []
        strategy = option.get("strategy") or option.get("summary") or option.get("plan_summary")
        if not strategy:
            verbs = [
                str(step.get("verb") or "").upper()
                for step in option["plan"]
                if isinstance(step, dict) and step.get("verb")
            ]
            if verbs:
                strategy = " → ".join(verbs)
        option["strategy"] = strategy or f"{bucket} scenario"
        option.setdefault("rationale", option.get("rationale") or "")
        option.setdefault("tradeoffs", option.get("tradeoffs") or {})
        prepared.append(option)
    return prepared


def _build_playback_text(goal: str, summary: str, frame: str, options: List[Dict[str, Any]], risks: List[str], final_sol: float) -> str:
    lines: List[str] = []
    lines.append(f"<b>Goal:</b> {html.escape(goal)}")
    if summary:
        lines.append(f"<b>Summary:</b> {html.escape(summary)}")
    if final_sol:
        lines.append(f"<b>Size:</b> {final_sol:.4f} SOL after buffers")
    frame_label = frame or "CSA"
    lines.append(f"<b>Frame:</b> {html.escape(frame_label)} (Conservative · Standard · Aggressive)")
    if options:
        lines.append("<b>Paths:</b>")
        for opt in options:
            name = html.escape(str(opt.get("name") or "Scenario"))
            bucket = html.escape(str(opt.get("bucket") or ""))
            strategy = html.escape(str(opt.get("strategy") or bucket))
            lines.append(f"• <b>{name}</b>: {strategy}")
            rationale = opt.get("rationale")
            if isinstance(rationale, str) and rationale.strip():
                lines.append(f"  · Why: {html.escape(rationale.strip())}")
    if risks:
        lines.append("<b>Risks:</b> " + html.escape(", ".join(risks)))
    lines.append("Simulate before executing")
    return "\n".join(lines)


def build_exec_ready_plan(
    goal: str,
    raw_plan: Optional[Dict[str, Any]],
    wallet_state: Optional[Dict[str, Any]] = None,
    desired_lamports: Optional[int] = None,
) -> Dict[str, Any]:
    """Return a normalized plan with sizing, playback text, and CSA scenarios."""

    plan_data = raw_plan if isinstance(raw_plan, dict) else {}

    balance_lamports = 0
    if wallet_state:
        balance_lamports = _safe_int(wallet_state.get("lamports"))
        if not balance_lamports:
            sol_balance = _safe_float(wallet_state.get("sol_balance"))
            balance_lamports = int(sol_balance * LAMPORTS_PER_SOL)

    desired = desired_lamports if desired_lamports is not None else _extract_desired_lamports(plan_data)
    desired = max(0, int(desired))

    final_lamports = decide_size(balance_lamports, desired)
    final_sol = final_lamports / LAMPORTS_PER_SOL

    frame = str(plan_data.get("frame") or "CSA").strip() or "CSA"
    options = _prepare_options(plan_data.get("options"))
    summary = str(plan_data.get("summary") or "").strip()
    goal_text = _goal_text(goal, plan_data)
    risks = _collect_risks(plan_data)
    playback_text = _build_playback_text(goal_text, summary, frame, options, risks, final_sol)

    baseline = plan_data.get("baseline")
    if not isinstance(baseline, dict):
        baseline = {"name": "Hold SOL"}

    simulation = plan_data.get("simulation") if isinstance(plan_data.get("simulation"), dict) else {}
    horizon = _safe_int((simulation or {}).get("horizon_days"), _safe_int(plan_data.get("horizon_days"), 30))
    if horizon <= 0:
        horizon = 30

    sizing = {
        "desired_lamports": desired,
        "desired_sol": desired / LAMPORTS_PER_SOL,
        "final_lamports": final_lamports,
        "final_sol": final_sol,
        "buffers_lamports": BUFFERS_LAMPORTS,
    }

    exec_plan: Dict[str, Any] = {
        "goal": goal_text,
        "summary": summary,
        "frame": frame,
        "options": options,
        "baseline": baseline,
        "sizing": sizing,
        "playback_text": playback_text,
        "risks": risks,
        "horizon_days": horizon,
    }

    account = plan_data.get("account") or plan_data.get("wallet")
    if account:
        exec_plan["account"] = account

    exec_plan["raw_plan"] = plan_data
    return exec_plan
