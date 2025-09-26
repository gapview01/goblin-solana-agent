from planner.exec_ready_planner import build_exec_ready_plan


def test_exec_ready_has_required_fields():
    plan = {
        "summary": "Test",
        "options": [
            {"name": "Standard", "plan": [{"verb": "quote", "params": {"in": "SOL", "out": "USDC", "amount": "0.05"}}]}
        ],
        "baseline": {"name": "Hold SOL"},
        "horizon_days": 30,
    }
    wallet = {"sol_balance": 0.5}
    out = build_exec_ready_plan(goal="Test", raw_plan=plan, wallet_state=wallet)
    assert isinstance(out, dict)
    for key in ("options", "sizing", "frame", "baseline", "playback_text"):
        assert key in out
    # actions should be a short list and include 'balance'
    assert any(a.get("verb") == "balance" for a in out.get("actions", []))


