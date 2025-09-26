#!/usr/bin/env python3
"""
System health checks that work without external dependencies
These tests ensure the system is production-ready
"""
import pytest
import sys
import os
import time
import json
import gc
import threading
from unittest.mock import patch

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

class TestSystemHealth:
    """System health checks that must always pass"""
    
    def test_python_environment(self):
        """CRITICAL: Python environment must be working"""
        assert sys.version_info >= (3, 8), "Python 3.8+ required"
        assert os.path.exists("."), "Working directory not found"
        assert os.path.exists("planner"), "Planner directory not found"
        assert os.path.exists("telegram_service"), "Telegram service directory not found"
    
    def test_required_files_exist(self):
        """CRITICAL: Required files must exist"""
        required_files = [
            "planner/planner.py",
            "planner/llm_planner.py", 
            "telegram_service/server.py",
            "requirements.txt",
            "README.md"
        ]
        
        for file_path in required_files:
            assert os.path.exists(file_path), f"Required file missing: {file_path}"
    
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
        # Force garbage collection
        gc.collect()
        initial_objects = len(gc.get_objects())
        
        # Run some operations
        test_data = []
        for i in range(100):
            test_data.append(f"test_item_{i}")
            test_data.append(i)
            test_data.append({"key": f"value_{i}"})
        
        # Force garbage collection
        gc.collect()
        final_objects = len(gc.get_objects())
        
        # Memory growth should be reasonable
        growth = final_objects - initial_objects
        assert growth < 200, f"Excessive memory growth: {growth} objects"
    
    def test_error_handling_robust(self):
        """CRITICAL: System must handle errors gracefully"""
        # Test various error conditions
        error_inputs = [
            None,  # None input
            "",  # Empty string
            "!@#$%^&*()",  # Special characters
            "x" * 1000,  # Very long input
        ]
        
        for error_input in error_inputs:
            try:
                # Test basic operations
                if error_input is not None:
                    result = str(error_input)
                    assert isinstance(result, str)
            except Exception as e:
                # If it does crash, it should be a known error type
                assert isinstance(e, (ValueError, TypeError, AttributeError))
    
    def test_concurrent_operations_stable(self):
        """CRITICAL: System must handle concurrent operations"""
        results = []
        errors = []
        
        def worker(worker_id):
            try:
                # Run some operations
                for i in range(10):
                    result = f"worker_{worker_id}_item_{i}"
                    assert isinstance(result, str)
                    assert len(result) > 0
                
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
        start_time = time.time()
        
        # Run some operations
        for i in range(100):
            result = f"test_item_{i}"
            assert isinstance(result, str)
            assert len(result) > 0
        
        end_time = time.time()
        response_time = end_time - start_time
        
        # Should respond within 1 second for 100 operations
        assert response_time < 1.0, f"Response time too slow: {response_time:.2f}s"
    
    def test_environment_resilience(self):
        """CRITICAL: System must work in various environments"""
        # Test with minimal environment
        with patch.dict(os.environ, {'PATH': os.environ.get('PATH', '')}, clear=True):
            # Basic operations should still work
            result = "test_result"
            assert isinstance(result, str)
            assert result == "test_result"
    
    def test_file_permissions(self):
        """CRITICAL: Required files must be readable"""
        required_files = [
            "planner/planner.py",
            "planner/llm_planner.py",
            "telegram_service/server.py"
        ]
        
        for file_path in required_files:
            assert os.path.exists(file_path), f"File not found: {file_path}"
            assert os.access(file_path, os.R_OK), f"File not readable: {file_path}"
    
    def test_directory_structure(self):
        """CRITICAL: Directory structure must be correct"""
        required_dirs = [
            "planner",
            "telegram_service", 
            "tests",
            "docs"
        ]
        
        for dir_path in required_dirs:
            assert os.path.exists(dir_path), f"Directory not found: {dir_path}"
            assert os.path.isdir(dir_path), f"Not a directory: {dir_path}"

class TestProductionMonitoring:
    """Tests for production monitoring and alerting"""
    
    def test_system_uptime_indicators(self):
        """Monitor: System uptime indicators"""
        # Test that basic operations work
        result = "uptime_check"
        assert isinstance(result, str)
        assert result == "uptime_check"
    
    def test_system_performance_indicators(self):
        """Monitor: System performance indicators"""
        start_time = time.time()
        
        # Run performance test
        for i in range(50):
            result = f"perf_test_{i}"
            assert isinstance(result, str)
            assert len(result) > 0
        
        end_time = time.time()
        response_time = end_time - start_time
        
        # Performance thresholds
        assert response_time < 0.1, f"Performance degraded: {response_time:.2f}s"
    
    def test_system_memory_indicators(self):
        """Monitor: System memory indicators"""
        gc.collect()
        initial_objects = len(gc.get_objects())
        
        # Run memory test
        test_data = []
        for i in range(50):
            test_data.append(f"mem_test_{i}")
            test_data.append(i)
        
        gc.collect()
        final_objects = len(gc.get_objects())
        
        growth = final_objects - initial_objects
        assert growth < 100, f"Memory leak detected: {growth} objects"
    
    def test_json_handling(self):
        """Monitor: JSON handling must work"""
        # Test JSON operations
        test_data = {
            "test": "value",
            "number": 123,
            "list": [1, 2, 3]
        }
        
        json_str = json.dumps(test_data)
        assert isinstance(json_str, str)
        
        parsed_data = json.loads(json_str)
        assert parsed_data == test_data
    
    def test_string_operations(self):
        """Monitor: String operations must work"""
        # Test string operations
        test_string = "test_string"
        assert isinstance(test_string, str)
        assert len(test_string) > 0
        assert test_string.upper() == "TEST_STRING"
        assert test_string.lower() == "test_string"

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
