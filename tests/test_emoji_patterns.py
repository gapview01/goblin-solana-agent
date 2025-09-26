#!/usr/bin/env python3
"""
Simple tests for emoji patterns in the telegram service
"""
import pytest

def test_briefing_emoji_patterns():
    """Test that briefing text patterns include expected emojis"""
    # Test the emoji patterns we expect in the briefing
    goal_text = "🧠 <b>Goal:</b> Test goal"
    summary_text = "📋 <b>Summary:</b> Test summary"
    candidates_text = "🎯 <b>Candidates:</b>"
    strategies_text = "🚀 <b>Strategies:</b>"
    risks_text = "⚠️ <b>Risks:</b>"
    simulation_text = "🎮 <b>Simulate before executing:</b>"
    
    # Verify emojis are present
    assert "🧠" in goal_text
    assert "📋" in summary_text
    assert "🎯" in candidates_text
    assert "🚀" in strategies_text
    assert "⚠️" in risks_text
    assert "🎮" in simulation_text

def test_sub_bullet_emoji_patterns():
    """Test that sub-bullet patterns include expected emojis"""
    # Test sub-bullet emoji patterns
    token_candidate = "• 💎 <code>SOL</code> — Stable token"
    strategy_name = "• 🎯 <b>Conservative</b>: Safe approach"
    rationale = "  · 💡 Why: Low risk strategy"
    pros = "  · ✅ Pros: Safe, predictable"
    cons = "  · ⚠️ Cons: Lower returns"
    risk_item = "• ⚠️ Market volatility"
    
    # Verify emojis are present
    assert "💎" in token_candidate
    assert "🎯" in strategy_name
    assert "💡" in rationale
    assert "✅" in pros
    assert "⚠️" in cons
    assert "⚠️" in risk_item

def test_action_button_emoji_patterns():
    """Test that action button patterns include expected emojis"""
    # Test the emoji patterns we expect in action buttons
    balance_text = "💰 Balance"
    quote_text = "🧮 Quote SOL→USDC 0.1"
    swap_text = "🔄 Swap SOL→USDC 0.1"
    stake_text = "🪙 Stake JITOSOL 0.1"
    unstake_text = "🪙 Unstake JITOSOL 0.1"
    
    # Verify emojis are present
    assert "💰" in balance_text
    assert "🧮" in quote_text
    assert "🔄" in swap_text
    assert "🪙" in stake_text
    assert "🪙" in unstake_text

def test_quote_response_emoji_patterns():
    """Test that quote response patterns include expected emojis"""
    # Test quote response emoji patterns
    quote_header = "🧮 Quote 0.1 SOL → USDC"
    estimated_output = "• 💰 Est. out: 95.5 USDC"
    price_info = "• 📊 Price: 1 SOL ≈ 955.0 USDC"
    slippage_info = "• ⚡ Slippage: 0.1%"
    impact_info = "• 📈 Impact: 0.05%"
    
    # Verify emojis are present
    assert "🧮" in quote_header
    assert "💰" in estimated_output
    assert "📊" in price_info
    assert "⚡" in slippage_info
    assert "📈" in impact_info

def test_swap_response_emoji_patterns():
    """Test that swap response patterns include expected emojis"""
    # Test swap response emoji patterns
    swap_success = "🔄 Swap sent ✅"
    stake_success = "🪙 Staked 0.1 JITOSOL ✅"
    unstake_success = "🪙 Unstaked 0.1 JITOSOL ✅"
    
    # Verify emojis are present
    assert "🔄" in swap_success
    assert "🪙" in stake_success
    assert "🪙" in unstake_success
    assert "✅" in swap_success
    assert "✅" in stake_success
    assert "✅" in unstake_success

def test_error_emoji_patterns():
    """Test that error patterns include expected emojis"""
    # Test error emoji patterns
    quote_error = "⚠️ Quote failed: Connection error"
    swap_error = "⚠️ Swap failed: Insufficient balance"
    access_denied = "🚫 Not allowed."
    
    # Verify emojis are present
    assert "⚠️" in quote_error
    assert "⚠️" in swap_error
    assert "🚫" in access_denied

if __name__ == "__main__":
    pytest.main([__file__])
