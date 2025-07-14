# ===== tests/__init__.py =====
"""
Test suite for Solana Arbitrage Bot

Run all tests with: pytest tests/
"""

import os
import sys

# Add parent directory to Python path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Test configuration
TEST_CONFIG = {
    "rpc_endpoint": "https://api.devnet.solana.com",
    "test_mode": True,
    "skip_transactions": True
}