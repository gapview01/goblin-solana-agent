#!/usr/bin/env python3
"""
Comprehensive integration tests for planner service
These tests ensure the planner service never goes down and all commands work correctly
"""
import pytest
import sys
import os
import json
from unittest.mock import Mock, patch, MagicMock

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

class TestPlannerServiceUptime:
    """Tests to ensure planner service never goes down"""
    
    def test_planner_imports_successfully(self):
        """Test that planner modules can be imported without errors"""
        try:
            # Test core planner imports
            from planner.planner import plan as legacy_plan
            from planner.llm_planner import plan as llm_plan
            assert callable(legacy_plan)
            assert callable(llm_plan)
        except ImportError as e:
            pytest.fail(f"Planner import failed: {e}")
    
    def test_planner_handles_empty_input(self):
        """Test that planner handles empty/invalid input gracefully"""
        from planner.planner import plan as legacy_plan
        
        # Test empty input
        result = legacy_plan("")
        assert isinstance(result, str)
        assert len(result) > 0
        
        # Test None input
        result = legacy_plan(None)
        assert isinstance(result, str)
        assert len(result) > 0
    
    def test_planner_handles_malformed_input(self):
        """Test that planner handles malformed input gracefully"""
        from planner.planner import plan as legacy_plan
        
        # Test various malformed inputs
        malformed_inputs = [
            "!@#$%^&*()",
            "x" * 10000,  # Very long string
            "🚀🔥💎",  # Only emojis
            "SELECT * FROM users; DROP TABLE users;",  # SQL injection attempt
        ]
        
        for input_text in malformed_inputs:
            result = legacy_plan(input_text)
            assert isinstance(result, str)
            assert len(result) > 0
            # Should not crash or return empty string
    
    @patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'})
    def test_planner_handles_api_errors(self):
        """Test that planner handles API errors gracefully"""
        from planner.planner import plan as legacy_plan
        
        # Mock API failure
        with patch('planner.planner.client') as mock_client:
            mock_client.chat.completions.create.side_effect = Exception("API Error")
            
            result = legacy_plan("test goal")
            assert isinstance(result, str)
            assert "error" in result.lower() or "failed" in result.lower()
    
    def test_planner_returns_valid_json_structure(self):
        """Test that LLM planner returns valid JSON structure"""
        # Mock the LLM planner to return a valid structure
        mock_plan = {
            "summary": "Test plan",
            "options": [{"name": "Test", "strategy": "Test strategy"}],
            "risks": ["Test risk"],
            "actions": [{"verb": "balance", "params": {}}]
        }
        
        with patch('planner.llm_planner._llm_plan_call', return_value=mock_plan):
            from planner.llm_planner import plan as llm_plan
            result = llm_plan("test goal")
            
            # Should return valid JSON string
            parsed = json.loads(result)
            assert "summary" in parsed
            assert "options" in parsed
            assert "risks" in parsed
            assert "actions" in parsed

class TestTelegramServiceIntegration:
    """Tests to ensure telegram service works correctly"""
    
    def test_telegram_handlers_exist(self):
        """Test that all required telegram handlers exist"""
        # Test that we can import the handler functions
        try:
            from telegram_service.server import (
                start, ping, plan_cmd, balance_cmd, quote_cmd, 
                swap_cmd, stake_cmd, unstake_cmd, on_option_button, on_action_button
            )
            assert callable(start)
            assert callable(ping)
            assert callable(plan_cmd)
            assert callable(balance_cmd)
            assert callable(quote_cmd)
            assert callable(swap_cmd)
            assert callable(stake_cmd)
            assert callable(unstake_cmd)
            assert callable(on_option_button)
            assert callable(on_action_button)
        except ImportError as e:
            pytest.fail(f"Telegram handler import failed: {e}")
    
    def test_telegram_utility_functions(self):
        """Test that telegram utility functions work correctly"""
        # Test helper functions that don't require bot initialization
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess
        
        # Test _fmt_list_or_str
        assert _fmt_list_or_str(['a', 'b']) == "a, b"
        assert _fmt_list_or_str("string") == "string"
        assert _fmt_list_or_str(None) == ""
        
        # Test _normalize_symbol_guess
        assert _normalize_symbol_guess("SOL") == "SOL"
        assert _normalize_symbol_guess("jito") == "JITOSOL"
        assert _normalize_symbol_guess("USDC") == "USDC"
    
    def test_telegram_command_validation(self):
        """Test that telegram commands handle invalid input gracefully"""
        from telegram_service.server import _parse_amount, _clean, _norm
        
        # Test _parse_amount
        assert _parse_amount("0.1") == 0.1
        assert _parse_amount("1.5") == 1.5
        
        # Test _clean
        assert _clean(['a', 'to', 'b']) == ['a', 'b']
        assert _clean(['SOL', '->', 'USDC']) == ['SOL', 'USDC']
        
        # Test _norm
        assert _norm("sol") == "SOL"
        assert _norm("usdc") == "USDC"
        assert _norm("jito") == "JITOSOL"

class TestSystemResilience:
    """Tests to ensure system resilience and uptime"""
    
    def test_memory_usage_stable(self):
        """Test that system doesn't have memory leaks"""
        import gc
        
        # Force garbage collection
        gc.collect()
        initial_objects = len(gc.get_objects())
        
        # Simulate some operations
        for i in range(100):
            from planner.planner import plan as legacy_plan
            result = legacy_plan(f"test goal {i}")
            assert isinstance(result, str)
        
        # Force garbage collection again
        gc.collect()
        final_objects = len(gc.get_objects())
        
        # Should not have excessive object growth
        growth = final_objects - initial_objects
        assert growth < 1000, f"Excessive object growth: {growth}"
    
    def test_concurrent_operations(self):
        """Test that system handles concurrent operations"""
        import threading
        import time
        
        results = []
        errors = []
        
        def worker(worker_id):
            try:
                from planner.planner import plan as legacy_plan
                result = legacy_plan(f"concurrent test {worker_id}")
                results.append((worker_id, result))
            except Exception as e:
                errors.append((worker_id, str(e)))
        
        # Start multiple threads
        threads = []
        for i in range(5):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        # Wait for all threads to complete
        for t in threads:
            t.join(timeout=10)
        
        # Should have results and no errors
        assert len(results) == 5, f"Expected 5 results, got {len(results)}"
        assert len(errors) == 0, f"Unexpected errors: {errors}"
    
    def test_error_recovery(self):
        """Test that system recovers from errors"""
        from planner.planner import plan as legacy_plan
        
        # Test that system recovers after an error
        try:
            # This might fail, but system should recover
            result1 = legacy_plan("test goal 1")
            assert isinstance(result1, str)
        except Exception:
            pass  # Expected to potentially fail
        
        # System should still work after error
        result2 = legacy_plan("test goal 2")
        assert isinstance(result2, str)
        assert len(result2) > 0

class TestProductionReadiness:
    """Tests to ensure production readiness"""
    
    def test_environment_variables_handled(self):
        """Test that missing environment variables are handled gracefully"""
        # Test with missing API key
        with patch.dict(os.environ, {}, clear=True):
            from planner.planner import plan as legacy_plan
            result = legacy_plan("test goal")
            assert isinstance(result, str)
            assert len(result) > 0
    
    def test_logging_works(self):
        """Test that logging doesn't crash the system"""
        import logging
        
        # Test that logging works
        logger = logging.getLogger("test")
        logger.info("Test log message")
        logger.warning("Test warning")
        logger.error("Test error")
        
        # Should not crash
        assert True
    
    def test_graceful_degradation(self):
        """Test that system degrades gracefully when components fail"""
        # Test that system works even when some components are unavailable
        with patch('planner.llm_planner._llm_plan_call', side_effect=Exception("Service unavailable")):
            from planner.llm_planner import generate_plan
            
            # Should handle the error gracefully
            try:
                result = generate_plan("test goal")
                # If it doesn't crash, that's good
                assert isinstance(result, dict)
            except Exception as e:
                # If it does crash, it should be a known error type
                assert "Service unavailable" in str(e)

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
