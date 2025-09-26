#!/bin/bash
# Test script for goblin-solana-agent
# Run this before pushing changes to ensure everything works

echo "🧪 Running tests for goblin-solana-agent..."
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Please install Python 3.12+"
    exit 1
fi

# Check if pytest is available
if ! python3 -c "import pytest" &> /dev/null; then
    echo "📦 Installing pytest..."
    python3 -m pip install pytest
fi

# Run the tests
echo "🔍 Running emoji pattern tests..."
python3 -m pytest tests/ -v

# Check exit code
if [ $? -eq 0 ]; then
    echo ""
    echo "✅ All tests passed! Safe to push changes."
    echo "🚀 You can now run: git push"
else
    echo ""
    echo "❌ Tests failed! Please fix issues before pushing."
    echo "🔧 Fix the issues and run ./test.sh again"
    exit 1
fi
