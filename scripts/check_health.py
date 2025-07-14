#!/usr/bin/env python3
"""Health check script for the arbitrage bot"""

import requests
import json
import sys
from datetime import datetime

def check_health():
    """Check bot health via metrics endpoint"""
    try:
        # Check metrics endpoint
        response = requests.get('http://localhost:8000/metrics', timeout=5)
        if response.status_code != 200:
            print(f"❌ Metrics endpoint returned {response.status_code}")
            return False
        
        # Parse metrics
        metrics = response.text
        
        # Check key metrics
        checks = {
            'Bot Running': 'arbitrage_' in metrics,
            'Trades Metric': 'arbitrage_trades_total' in metrics,
            'Balance Metric': 'wallet_balance_usd' in metrics,
            'Opportunities Metric': 'arbitrage_opportunities_active' in metrics
        }
        
        all_good = True
        for check, result in checks.items():
            status = "✅" if result else "❌"
            print(f"{status} {check}")
            if not result:
                all_good = False
        
        return all_good
        
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to bot metrics endpoint")
        return False
    except Exception as e:
        print(f"❌ Error checking health: {e}")
        return False

if __name__ == "__main__":
    print(f"🏥 Health Check - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)
    
    if check_health():
        print("\n✅ Bot is healthy!")
        sys.exit(0)
    else:
        print("\n❌ Bot health check failed!")
        sys.exit(1)