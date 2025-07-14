"""
Simple Arbitrage Monitor - Start with this before using the full bot!
This version only monitors and logs opportunities without executing trades.
"""

import aiohttp
import asyncio
import json
import csv
from datetime import datetime
from decimal import Decimal
import os
from typing import Dict, List, Optional

class SimpleArbitrageMonitor:
    """Monitors price differences between Jupiter and Raydium"""
    
    def __init__(self):
        # API endpoints
        self.jupiter_api = "https://quote-api.jup.ag/v6/quote"
        self.dexscreener_api = "https://api.dexscreener.com/latest/dex/tokens"
        
        # Token addresses
        self.sol_mint = 'So11111111111111111111111111111111111111112'
        self.usdc_mint = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
        
        # Popular tokens to monitor with their decimals
        self.tokens = {
            "BONK": {
                "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                "decimals": 5
            },
            "WIF": {
                "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
                "decimals": 6
            },
            "JTO": {
                "mint": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
                "decimals": 9
            },
            "PYTH": {
                "mint": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
                "decimals": 6
            },
            "JUP": {
                "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
                "decimals": 6
            }
        }
        
        # Monitoring parameters
        self.min_price_diff_pct = 0.5  # Minimum 0.5% difference to log (more realistic)
        self.max_price_diff_pct = 10.0  # Maximum 10% difference (anything higher is likely a bug)
        self.check_interval = 30  # Check every 30 seconds
        
        print(f"Monitor initialized. Tracking {len(self.tokens)} tokens")
        print(f"Minimum price difference: {self.min_price_diff_pct}%")
        print(f"Maximum price difference: {self.max_price_diff_pct}% (higher differences ignored as likely errors)")
        print("-" * 50)
    
    async def get_jupiter_price(self, token_name: str, token_info: Dict) -> Optional[float]:
        """Get token price from Jupiter (in USDC)"""
        try:
            async with aiohttp.ClientSession() as session:
                # Get price for 1 token in USDC
                # Use the correct amount based on decimals
                amount = 10 ** token_info['decimals']  # 1 token
                
                token_params = {
                    'inputMint': token_info['mint'],
                    'outputMint': self.usdc_mint,
                    'amount': str(amount),
                    'slippageBps': 50
                }
                
                async with session.get(self.jupiter_api, params=token_params) as response:
                    if response.status != 200:
                        print(f"    Jupiter API error for {token_name}: {response.status}")
                        return None
                    
                    data = await response.json()
                    
                    # Get the output amount in USDC
                    out_amount = int(data.get('outAmount', 0))
                    if out_amount == 0:
                        return None
                    
                    # Convert to USDC price (USDC has 6 decimals)
                    price = out_amount / (10 ** 6)
                    
                    # Sanity check
                    if price > 1000000:  # No token should be worth > $1M
                        print(f"    Warning: Jupiter price for {token_name} seems too high: ${price}")
                        return None
                    
                    return price
                    
        except Exception as e:
            print(f"    Jupiter API error for {token_name}: {e}")
            return None
    
    async def get_dexscreener_prices(self, token_name: str, token_info: Dict) -> Dict[str, float]:
        """Get token prices from DexScreener for different DEXs"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'User-Agent': 'Mozilla/5.0',
                    'Accept': 'application/json'
                }
                
                url = f"{self.dexscreener_api}/{token_info['mint']}"
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        return {}
                    
                    data = await response.json()
                    pairs = data.get('pairs', [])
                    
                    # Extract prices by DEX
                    prices = {}
                    for pair in pairs:
                        dex = pair.get('dexId', '').lower()
                        price_str = pair.get('priceUsd', '0')
                        
                        try:
                            price = float(price_str)
                            
                            # Sanity check
                            if 0 < price < 1000000 and dex in ['raydium', 'orca', 'meteora']:
                                # Only keep the highest liquidity pair per DEX
                                liquidity = float(pair.get('liquidity', {}).get('usd', 0))
                                if dex not in prices or liquidity > prices[dex]['liquidity']:
                                    prices[dex] = {
                                        'price': price,
                                        'liquidity': liquidity,
                                        'pair_address': pair.get('pairAddress')
                                    }
                        except (ValueError, TypeError):
                            continue
                    
                    return {dex: info['price'] for dex, info in prices.items()}
                    
        except Exception as e:
            print(f"    DexScreener API error for {token_name}: {e}")
            return {}
    
    async def check_arbitrage_opportunity(self, symbol: str, token_info: Dict) -> Optional[Dict]:
        """Check for arbitrage opportunities for a single token"""
        # Get Jupiter price
        jupiter_price = await self.get_jupiter_price(symbol, token_info)
        if not jupiter_price:
            return None
        
        # Get prices from other DEXs
        dex_prices = await self.get_dexscreener_prices(symbol, token_info)
        if not dex_prices:
            return None
        
        opportunities = []
        
        # Compare Jupiter with each DEX
        for dex, dex_price in dex_prices.items():
            if dex_price <= 0:
                continue
                
            # Calculate price difference
            price_diff = abs(jupiter_price - dex_price)
            price_diff_pct = (price_diff / min(jupiter_price, dex_price)) * 100
            
            # Skip if difference is too small or too large (likely error)
            if price_diff_pct < self.min_price_diff_pct or price_diff_pct > self.max_price_diff_pct:
                continue
            
            if jupiter_price < dex_price:
                buy_on = "Jupiter"
                sell_on = dex.capitalize()
                buy_price = jupiter_price
                sell_price = dex_price
            else:
                buy_on = dex.capitalize()
                sell_on = "Jupiter"
                buy_price = dex_price
                sell_price = jupiter_price
            
            # Calculate potential profit
            position_size = 1000  # $1000 position
            tokens = position_size / buy_price
            gross_profit = (sell_price - buy_price) * tokens
            
            # Estimate fees (simplified)
            swap_fees = position_size * 0.003 * 2  # 0.3% each way
            sol_fees = 0.00025 * 150 * 2  # ~$0.075 for 2 transactions at $150 SOL
            net_profit = gross_profit - swap_fees - sol_fees
            
            # Only include if profit is positive and reasonable
            if net_profit > 0 and net_profit < position_size * 0.1:  # Max 10% profit (sanity check)
                opportunities.append({
                    'symbol': symbol,
                    'token_mint': token_info['mint'],
                    'buy_on': buy_on,
                    'sell_on': sell_on,
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'price_diff_pct': price_diff_pct,
                    'gross_profit': gross_profit,
                    'net_profit': net_profit,
                    'timestamp': datetime.now()
                })
        
        # Return best opportunity
        if opportunities:
            return max(opportunities, key=lambda x: x['net_profit'])
        return None
    
    def save_to_csv(self, opportunities: List[Dict]):
        """Save opportunities to CSV file"""
        if not opportunities:
            return
            
        filename = f"arbitrage_opportunities_{datetime.now().strftime('%Y%m%d')}.csv"
        file_exists = os.path.exists(filename)
        
        with open(filename, 'a', newline='') as f:
            fieldnames = [
                'timestamp', 'symbol', 'buy_on', 'sell_on',
                'buy_price', 'sell_price', 'price_diff_pct',
                'gross_profit', 'net_profit'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            if not file_exists:
                writer.writeheader()
            
            for opp in opportunities:
                writer.writerow({
                    'timestamp': opp['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                    'symbol': opp['symbol'],
                    'buy_on': opp['buy_on'],
                    'sell_on': opp['sell_on'],
                    'buy_price': f"{opp['buy_price']:.8f}",
                    'sell_price': f"{opp['sell_price']:.8f}",
                    'price_diff_pct': f"{opp['price_diff_pct']:.2f}",
                    'gross_profit': f"{opp['gross_profit']:.2f}",
                    'net_profit': f"{opp['net_profit']:.2f}"
                })
    
    async def monitor_loop(self):
        """Main monitoring loop"""
        print("Starting price monitoring...")
        print("Press Ctrl+C to stop\n")
        
        check_count = 0
        total_opportunities = 0
        
        while True:
            try:
                check_count += 1
                print(f"\n[Check #{check_count}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                
                opportunities = []
                
                # Check each token
                for symbol, token_info in self.tokens.items():
                    print(f"  Checking {symbol}...", end="", flush=True)
                    
                    opportunity = await self.check_arbitrage_opportunity(symbol, token_info)
                    
                    if opportunity and opportunity['net_profit'] > 1.0:  # Only log if >$1 profit
                        opportunities.append(opportunity)
                        print(f" ✅ OPPORTUNITY FOUND!")
                        print(f"    Buy on {opportunity['buy_on']} at ${opportunity['buy_price']:.6f}")
                        print(f"    Sell on {opportunity['sell_on']} at ${opportunity['sell_price']:.6f}")
                        print(f"    Price difference: {opportunity['price_diff_pct']:.2f}%")
                        print(f"    Net profit: ${opportunity['net_profit']:.2f}")
                    else:
                        print(" ❌ No profitable opportunity")
                    
                    await asyncio.sleep(0.5)  # Small delay between tokens
                
                # Save opportunities
                if opportunities:
                    self.save_to_csv(opportunities)
                    total_opportunities += len(opportunities)
                    print(f"\n💰 Found {len(opportunities)} opportunities this check!")
                    print(f"📊 Total opportunities found: {total_opportunities}")
                else:
                    print("\nNo profitable opportunities found this check.")
                
                # Show price summary
                print("\n📈 Price Summary:")
                for symbol, token_info in self.tokens.items():
                    jupiter_price = await self.get_jupiter_price(symbol, token_info)
                    if jupiter_price:
                        print(f"  {symbol}: ${jupiter_price:.6f} (Jupiter)")
                
                # Wait before next check
                print(f"\nNext check in {self.check_interval} seconds...")
                await asyncio.sleep(self.check_interval)
                
            except KeyboardInterrupt:
                print("\n\nMonitoring stopped by user.")
                print(f"Total opportunities found: {total_opportunities}")
                break
            except Exception as e:
                print(f"\nError: {e}")
                print("Continuing in 10 seconds...")
                await asyncio.sleep(10)

async def main():
    """Main entry point"""
    monitor = SimpleArbitrageMonitor()
    await monitor.monitor_loop()

if __name__ == "__main__":
    print("=" * 50)
    print("SIMPLE ARBITRAGE MONITOR")
    print("=" * 50)
    print("\nThis monitor will:")
    print("1. Check prices on Jupiter and other DEXs")
    print("2. Calculate potential arbitrage profits")
    print("3. Log opportunities to CSV file")
    print("4. NOT execute any trades (monitoring only)")
    print("\n" + "=" * 50)
    
    asyncio.run(main())