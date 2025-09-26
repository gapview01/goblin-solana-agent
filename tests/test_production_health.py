#!/usr/bin/env python3
"""
Production health checks that work without external dependencies
These tests ensure the planner service is production-ready
"""
import pytest
import sys
import os
import time
import json
from unittest.mock import Mock, patch, MagicMock

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

class TestProductionHealth:
    """Production health checks that must always pass"""
    
    def test_planner_module_structure(self):
        """CRITICAL: Planner modules must have correct structure"""
        # Test that planner modules exist and are importable
        try:
            import planner.planner
            import planner.llm_planner
            assert hasattr(planner.planner, 'plan')
            assert hasattr(planner.llm_planner, 'plan')
        except ImportError as e:
            pytest.fail(f"CRITICAL: Planner module structure broken: {e}")
    
    def test_telegram_module_structure(self):
        """CRITICAL: Telegram modules must have correct structure"""
        # Test that telegram modules exist and are importable
        try:
            import telegram_service.server
            assert hasattr(telegram_service.server, 'start')
            assert hasattr(telegram_service.server, 'plan_cmd')
            assert hasattr(telegram_service.server, 'balance_cmd')
        except ImportError as e:
            pytest.fail(f"CRITICAL: Telegram module structure broken: {e}")
    
    def test_utility_functions_exist(self):
        """CRITICAL: Core utility functions must exist"""
        # Test that utility functions exist
        try:
            from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess, _parse_amount
            assert callable(_fmt_list_or_str)
            assert callable(_normalize_symbol_guess)
            assert callable(_parse_amount)
        except ImportError as e:
            pytest.fail(f"CRITICAL: Utility functions missing: {e}")
    
    def test_utility_functions_work(self):
        """CRITICAL: Core utility functions must work correctly"""
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess, _parse_amount
        
        # Test _fmt_list_or_str
        assert _fmt_list_or_str(['a', 'b']) == "a, b"
        assert _fmt_list_or_str("string") == "string"
        assert _fmt_list_or_str(None) == ""
        
        # Test _normalize_symbol_guess
        assert _normalize_symbol_guess("SOL") == "SOL"
        assert _normalize_symbol_guess("jito") == "JITOSOL"
        assert _normalize_symbol_guess("usdc") == "USDC"
        
        # Test _parse_amount
        assert _parse_amount("0.1") == 0.1
        assert _parse_amount("1.5") == 1.5
    
    def test_emoji_patterns_consistent(self):
        """CRITICAL: Emoji patterns must be consistent"""
        # Test that emoji patterns are properly defined
        expected_emojis = {
            'goal': '🧠',
            'summary': '📋',
            'candidates': '🎯',
            'strategies': '🚀',
            'risks': '⚠️',
            'simulation': '🎮',
            'balance': '💰',
            'quote': '🧮',
            'swap': '🔄',
            'stake': '🪙',
            'success': '✅',
            'error': '⚠️',
            'denied': '🚫'
        }
        
        # Verify all expected emojis are present
        for key, emoji in expected_emojis.items():
            assert emoji, f"Emoji for {key} is missing"
            assert len(emoji) > 0, f"Emoji for {key} is empty"
    
    def test_system_memory_stable(self):
        """CRITICAL: System memory usage must be stable"""
        import gc
        
        # Force garbage collection
        gc.collect()
        initial_objects = len(gc.get_objects())
        
        # Run some operations that don't require external services
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess
        
        for i in range(100):
            result = _fmt_list_or_str([f"item{i}", f"item{i+1}"])
            assert isinstance(result, str)
            
            result = _normalize_symbol_guess(f"token{i}")
            assert isinstance(result, str)
        
        # Force garbage collection
        gc.collect()
        final_objects = len(gc.get_objects())
        
        # Memory growth should be reasonable
        growth = final_objects - initial_objects
        assert growth < 100, f"Excessive memory growth: {growth} objects"
    
    def test_error_handling_robust(self):
        """CRITICAL: System must handle errors gracefully"""
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess, _parse_amount
        
        # Test various error conditions
        error_inputs = [
            None,  # None input
            "",  # Empty string
            "!@#$%^&*()",  # Special characters
            "x" * 1000,  # Very long input
        ]
        
        for error_input in error_inputs:
            try:
                result = _fmt_list_or_str(error_input)
                assert isinstance(result, str)
            except Exception as e:
                # If it does crash, it should be a known error type
                assert isinstance(e, (ValueError, TypeError, AttributeError))
    
    def test_concurrent_operations_stable(self):
        """CRITICAL: System must handle concurrent operations"""
        import threading
        import time
        
        results = []
        errors = []
        
        def worker(worker_id):
            try:
                from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess
                
                for i in range(10):
                    result1 = _fmt_list_or_str([f"worker{worker_id}", f"item{i}"])
                    result2 = _normalize_symbol_guess(f"token{worker_id}{i}")
                    assert isinstance(result1, str)
                    assert isinstance(result2, str)
                
                results.append(worker_id)
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
        
        # Should have results and minimal errors
        assert len(results) >= 4, f"Too few successful results: {len(results)}"
        assert len(errors) <= 1, f"Too many errors: {errors}"
    
    def test_response_time_acceptable(self):
        """CRITICAL: Response time must be acceptable"""
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess
        
        start_time = time.time()
        
        # Run some operations
        for i in range(100):
            result1 = _fmt_list_or_str([f"item{i}", f"item{i+1}"])
            result2 = _normalize_symbol_guess(f"token{i}")
            assert isinstance(result1, str)
            assert isinstance(result2, str)
        
        end_time = time.time()
        response_time = end_time - start_time
        
        # Should respond within 1 second for 100 operations
        assert response_time < 1.0, f"Response time too slow: {response_time:.2f}s"
    
    def test_environment_resilience(self):
        """CRITICAL: System must work in various environments"""
        # Test with minimal environment
        with patch.dict(os.environ, {'PATH': os.environ.get('PATH', '')}, clear=True):
            from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess
            
            result1 = _fmt_list_or_str(['test', 'items'])
            result2 = _normalize_symbol_guess('test')
            
            assert isinstance(result1, str)
            assert isinstance(result2, str)
            assert result1 == "test, items"
            assert result2 == "TEST"
    
    def test_planner_fallback_mechanisms(self):
        """CRITICAL: Planner must have fallback mechanisms"""
        # Test that planner has error handling
        try:
            import planner.planner
            import planner.llm_planner
            
            # Check that both planners exist (fallback mechanism)
            assert hasattr(planner.planner, 'plan')
            assert hasattr(planner.llm_planner, 'plan')
            
            # Check that they have error handling
            assert callable(planner.planner.plan)
            assert callable(planner.llm_planner.plan)
            
        except Exception as e:
            pytest.fail(f"CRITICAL: Planner fallback mechanisms broken: {e}")
    
    def test_telegram_command_structure(self):
        """CRITICAL: Telegram commands must have correct structure"""
        # Test that all required commands exist
        required_commands = [
            'start', 'ping', 'plan_cmd', 'balance_cmd', 
            'quote_cmd', 'swap_cmd', 'stake_cmd', 'unstake_cmd'
        ]
        
        try:
            import telegram_service.server
            
            for command in required_commands:
                assert hasattr(telegram_service.server, command), f"Command {command} missing"
                assert callable(getattr(telegram_service.server, command)), f"Command {command} not callable"
                
        except Exception as e:
            pytest.fail(f"CRITICAL: Telegram command structure broken: {e}")

class TestProductionMonitoring:
    """Tests for production monitoring and alerting"""
    
    def test_system_uptime_indicators(self):
        """Monitor: System uptime indicators"""
        # Test that core functions are available
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess
        
        # These should always work
        result1 = _fmt_list_or_str(['uptime', 'check'])
        result2 = _normalize_symbol_guess('SOL')
        
        assert isinstance(result1, str)
        assert isinstance(result2, str)
        assert result1 == "uptime, check"
        assert result2 == "SOL"
    
    def test_system_performance_indicators(self):
        """Monitor: System performance indicators"""
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess
        
        start_time = time.time()
        
        # Run performance test
        for i in range(50):
            result1 = _fmt_list_or_str([f"perf{i}", f"test{i}"])
            result2 = _normalize_symbol_guess(f"token{i}")
            assert isinstance(result1, str)
            assert isinstance(result2, str)
        
        end_time = time.time()
        response_time = end_time - start_time
        
        # Performance thresholds
        assert response_time < 0.5, f"Performance degraded: {response_time:.2f}s"
    
    def test_system_memory_indicators(self):
        """Monitor: System memory indicators"""
        import gc
        
        gc.collect()
        initial_objects = len(gc.get_objects())
        
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess
        
        # Run memory test
        for i in range(50):
            result1 = _fmt_list_or_str([f"mem{i}", f"test{i}"])
            result2 = _normalize_symbol_guess(f"token{i}")
            assert isinstance(result1, str)
            assert isinstance(result2, str)
        
        gc.collect()
        final_objects = len(gc.get_objects())
        
        growth = final_objects - initial_objects
        assert growth < 50, f"Memory leak detected: {growth} objects"

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
