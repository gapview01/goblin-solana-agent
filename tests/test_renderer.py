from bot.renderers.plan_renderer import render_plan_simple


def test_render_plan_simple_basic():
    goal = "Grow SOL safely"
    plan = {
        "options": [
            {"name": "Standard", "strategy": "Stake for yield", "plan": []},
            {"name": "Stable", "strategy": "Hold USDC", "plan": []},
        ],
        "sizing": {"final_sol": 0.05},
        "frame": "CSA",
        "risks": ["Market volatility", "Protocol risk"],
    }
    text = render_plan_simple(goal, plan)
    assert "Goal:" in text
    assert "Plan Summary" in text
    assert "Strategies" in text
    assert "Risks" in text
    assert "/simulate" in text
    # Bound length to fit Telegram message limits (defensive)
    assert len(text) < 2000


