# ===== src/__init__.py =====
"""
Solana Arbitrage Bot Package

A production-ready arbitrage bot for Solana DEXs.
"""

__version__ = "2.0.0"
__author__ = "Solana Arbitrage Bot Team"

# Import main components for easier access
from .arbitrage_bot import ProductionArbitrageBot
from .simple_monitor import SimpleArbitrageMonitor
from .config import get_config, initialize_config
from .constants import *

__all__ = [
    'ProductionArbitrageBot',
    'SimpleArbitrageMonitor',
    'get_config',
    'initialize_config',
]