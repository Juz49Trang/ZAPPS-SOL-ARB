# ===== scripts/analyze_opportunities.py =====
#!/usr/bin/env python3
"""Analyze historical arbitrage opportunities from CSV files"""

import csv
import os
from datetime import datetime
from collections import defaultdict
import statistics

def analyze_opportunities():
    """Analyze arbitrage opportunities from CSV files"""
    # Find all CSV files
    csv_files = [f for f in os.listdir('.') if f.startswith('arbitrage_opportunities_') and f.endswith('.csv')]
    
    if not csv_files:
        print("No opportunity CSV files found!")
        return
    
    all_opportunities = []
    
    # Read all CSV files
    for csv_file in csv_files:
        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                row['net_profit'] = float(row['net_profit'])
                row['price_diff_pct'] = float(row['price_diff_pct'])
                all_opportunities.append(row)
    
    if not all_opportunities:
        print("No opportunities found in CSV files!")
        return
    
    # Analyze by token
    by_token = defaultdict(list)
    by_route = defaultdict(list)
    
    for opp in all_opportunities:
        by_token[opp['symbol']].append(opp)
        route = f"{opp['buy_on']} -> {opp['sell_on']}"
        by_route[route].append(opp)
    
    print(f"📊 Arbitrage Opportunity Analysis")
    print(f"📅 Total opportunities found: {len(all_opportunities)}")
    print("=" * 60)
    
    # Token analysis
    print("\n💰 By Token:")
    print(f"{'Token':<10} {'Count':<10} {'Avg Profit':<12} {'Max Profit':<12} {'Avg Diff %'}")
    print("-" * 60)
    
    for token, opps in sorted(by_token.items(), key=lambda x: len(x[1]), reverse=True):
        profits = [o['net_profit'] for o in opps]
        diffs = [o['price_diff_pct'] for o in opps]
        print(f"{token:<10} {len(opps):<10} ${statistics.mean(profits):<11.2f} ${max(profits):<11.2f} {statistics.mean(diffs):.2f}%")
    
    # Route analysis
    print("\n🔄 By Route:")
    print(f"{'Route':<20} {'Count':<10} {'Avg Profit':<12} {'Total Profit'}")
    print("-" * 60)
    
    for route, opps in sorted(by_route.items(), key=lambda x: len(x[1]), reverse=True):
        profits = [o['net_profit'] for o in opps]
        total_profit = sum(profits)
        print(f"{route:<20} {len(opps):<10} ${statistics.mean(profits):<11.2f} ${total_profit:.2f}")
    
    # Time analysis
    print("\n⏰ Best Times:")
    by_hour = defaultdict(list)
    for opp in all_opportunities:
        hour = datetime.strptime(opp['timestamp'], '%Y-%m-%d %H:%M:%S').hour
        by_hour[hour].append(opp['net_profit'])
    
    print(f"{'Hour (UTC)':<12} {'Opportunities':<15} {'Avg Profit'}")
    print("-" * 40)
    
    for hour in sorted(by_hour.keys()):
        profits = by_hour[hour]
        print(f"{hour:02d}:00-{hour:02d}:59  {len(profits):<15} ${statistics.mean(profits):.2f}")

if __name__ == "__main__":
    analyze_opportunities()