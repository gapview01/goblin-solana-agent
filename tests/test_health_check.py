#!/usr/bin/env python3
"""
Health check tests for production monitoring
These tests ensure the planner service is always available and working
"""
import pytest
import sys
import os
import time
import json
from unittest.mock import patch

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

class TestHealthCheck:
    """Critical health checks that must always pass"""
    
    def test_planner_service_available(self):
        """CRITICAL: Planner service must be available"""
        try:
            from planner.planner import plan as legacy_plan
            from planner.llm_planner import plan as llm_plan
            assert callable(legacy_plan)
            assert callable(llm_plan)
        except Exception as e:
            pytest.fail(f"CRITICAL: Planner service unavailable: {e}")
    
    def test_planner_responds_to_requests(self):
        """CRITICAL: Planner must respond to requests"""
        from planner.planner import plan as legacy_plan
        
        # Test basic request
        result = legacy_plan("test goal")
        assert isinstance(result, str)
        assert len(result) > 0
        
        # Test that it's not just returning an error message
        assert "error" not in result.lower() or "planner error" in result.lower()
    
    def test_telegram_service_imports(self):
        """CRITICAL: Telegram service must import without errors"""
        try:
            from telegram_service.server import (
                start, ping, plan_cmd, balance_cmd, quote_cmd, 
                swap_cmd, stake_cmd, unstake_cmd
            )
            assert callable(start)
            assert callable(ping)
            assert callable(plan_cmd)
            assert callable(balance_cmd)
            assert callable(quote_cmd)
            assert callable(swap_cmd)
            assert callable(stake_cmd)
            assert callable(unstake_cmd)
        except Exception as e:
            pytest.fail(f"CRITICAL: Telegram service import failed: {e}")
    
    def test_telegram_utility_functions_work(self):
        """CRITICAL: Core utility functions must work"""
        from telegram_service.server import _fmt_list_or_str, _normalize_symbol_guess, _parse_amount
        
        # Test _fmt_list_or_str
        assert _fmt_list_or_str(['a', 'b']) == "a, b"
        assert _fmt_list_or_str("string") == "string"
        assert _fmt_list_or_str(None) == ""
        
        # Test _normalize_symbol_guess
        assert _normalize_symbol_guess("SOL") == "SOL"
        assert _normalize_symbol_guess("jito") == "JITOSOL"
        
        # Test _parse_amount
        assert _parse_amount("0.1") == 0.1
        assert _parse_amount("1.5") == 1.5
    
    def test_system_memory_stable(self):
        """CRITICAL: System memory usage must be stable"""
        import gc
        
        # Force garbage collection
        gc.collect()
        initial_objects = len(gc.get_objects())
        
        # Run some operations
        from planner.planner import plan as legacy_plan
        for i in range(10):
            result = legacy_plan(f"health check {i}")
            assert isinstance(result, str)
        
        # Force garbage collection
        gc.collect()
        final_objects = len(gc.get_objects())
        
        # Memory growth should be reasonable
        growth = final_objects - initial_objects
        assert growth < 500, f"Excessive memory growth: {growth} objects"
    
    def test_response_time_acceptable(self):
        """CRITICAL: Response time must be acceptable"""
        from planner.planner import plan as legacy_plan
        
        start_time = time.time()
        result = legacy_plan("health check response time")
        end_time = time.time()
        
        response_time = end_time - start_time
        
        # Should respond within 5 seconds
        assert response_time < 5.0, f"Response time too slow: {response_time:.2f}s"
        assert isinstance(result, str)
        assert len(result) > 0
    
    def test_error_handling_robust(self):
        """CRITICAL: System must handle errors gracefully"""
        from planner.planner import plan as legacy_plan
        
        # Test various error conditions
        error_inputs = [
            "",  # Empty input
            None,  # None input
            "!@#$%^&*()",  # Special characters
            "x" * 1000,  # Very long input
        ]
        
        for error_input in error_inputs:
            try:
                result = legacy_plan(error_input)
                # Should not crash, should return something
                assert isinstance(result, str)
                assert len(result) > 0
            except Exception as e:
                # If it does crash, it should be a known error type
                assert isinstance(e, (ValueError, TypeError, AttributeError))
    
    def test_concurrent_requests_stable(self):
        """CRITICAL: System must handle concurrent requests"""
        import threading
        import time
        
        results = []
        errors = []
        
        def worker(worker_id):
            try:
                from planner.planner import plan as legacy_plan
                result = legacy_plan(f"concurrent health check {worker_id}")
                results.append((worker_id, result))
            except Exception as e:
                errors.append((worker_id, str(e)))
        
        # Start multiple concurrent requests
        threads = []
        for i in range(3):  # Reduced for health check
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        # Wait for completion
        for t in threads:
            t.join(timeout=10)
        
        # Should have results and minimal errors
        assert len(results) >= 2, f"Too few successful results: {len(results)}"
        assert len(errors) <= 1, f"Too many errors: {errors}"
    
    def test_environment_resilience(self):
        """CRITICAL: System must work in various environments"""
        # Test with minimal environment
        with patch.dict(os.environ, {'PATH': os.environ.get('PATH', '')}, clear=True):
            from planner.planner import plan as legacy_plan
            result = legacy_plan("environment test")
            assert isinstance(result, str)
            assert len(result) > 0

class TestProductionMonitoring:
    """Tests for production monitoring and alerting"""
    
    def test_planner_uptime_check(self):
        """Monitor: Planner service uptime"""
        from planner.planner import plan as legacy_plan
        
        # This should always work
        result = legacy_plan("uptime check")
        assert isinstance(result, str)
        assert len(result) > 0
        
        # Should not be an error message
        error_indicators = ["crash", "exception", "failed", "unavailable"]
        result_lower = result.lower()
        for indicator in error_indicators:
            assert indicator not in result_lower, f"Uptime check failed: {result}"
    
    def test_planner_performance_check(self):
        """Monitor: Planner performance"""
        from planner.planner import plan as legacy_plan
        
        start_time = time.time()
        result = legacy_plan("performance check")
        end_time = time.time()
        
        response_time = end_time - start_time
        
        # Performance thresholds
        assert response_time < 3.0, f"Performance degraded: {response_time:.2f}s"
        assert isinstance(result, str)
        assert len(result) > 0
    
    def test_planner_memory_check(self):
        """Monitor: Planner memory usage"""
        import gc
        
        gc.collect()
        initial_objects = len(gc.get_objects())
        
        from planner.planner import plan as legacy_plan
        result = legacy_plan("memory check")
        
        gc.collect()
        final_objects = len(gc.get_objects())
        
        growth = final_objects - initial_objects
        assert growth < 100, f"Memory leak detected: {growth} objects"
        assert isinstance(result, str)
        assert len(result) > 0

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
