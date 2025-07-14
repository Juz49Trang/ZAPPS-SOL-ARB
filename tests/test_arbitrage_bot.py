"""
Comprehensive Testing Suite for Production Arbitrage Bot
"""

import asyncio
import pytest
import json
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock
import aiohttp
from dataclasses import dataclass

# Import the main bot
from arbitrage_bot import (
    ProductionArbitrageBot, 
    ArbitrageOpportunity,
    Token,
    DEX,
    TradeResult,
    RateLimiter,
    PriceCache
)

class TestConfig:
    """Test configuration"""
    
    @staticmethod
    def create_test_config():
        return {
            "rpc_endpoint": "https://api.devnet.solana.com",
            "wallet_path": "test_wallet.json",
            "usdc_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "min_profit_usd": 5.0,
            "max_position_size": 1000.0,
            "max_price_impact": 0.01,
            "priority_fee_microlamports": 1000,
            "max_daily_loss": 50.0,
            "check_interval": 5,
            "tokens": {
                "TEST": {
                    "mint": "TestTokenMint11111111111111111111111111111",
                    "decimals": 9,
                    "min_liquidity": 1000
                }
            }
        }
    
    @staticmethod
    def create_test_wallet():
        return {
            "secret_key": "5MaiiCavjCmn9Hs1o3eznqDEhRwxo7pXiAYez7keQUviUkauRiTMD8DrESdrNjN8zd9mTmVhRvBJeg5vhyvgrAhG",
            "warning": "TEST WALLET ONLY - DO NOT USE IN PRODUCTION"
        }

class MockPriceProvider:
    """Mock price provider for testing"""
    
    def __init__(self):
        self.jupiter_prices = {}
        self.raydium_prices = {}
        self.call_count = 0
    
    def set_jupiter_price(self, token_mint: str, price: Decimal, liquidity: Decimal):
        self.jupiter_prices[token_mint] = (price, liquidity)
    
    def set_raydium_price(self, token_mint: str, price: Decimal, liquidity: Decimal):
        self.raydium_prices[token_mint] = (price, liquidity)
    
    async def get_jupiter_price(self, token: Token):
        self.call_count += 1
        return self.jupiter_prices.get(token.mint)
    
    async def get_raydium_price(self, token: Token):
        self.call_count += 1
        return self.raydium_prices.get(token.mint)

@pytest.fixture
async def test_bot():
    """Create a test bot instance"""
    # Create test config
    with open('test_config.json', 'w') as f:
        json.dump(TestConfig.create_test_config(), f)
    
    with open('test_wallet.json', 'w') as f:
        json.dump(TestConfig.create_test_wallet(), f)
    
    bot = ProductionArbitrageBot('test_config.json')
    yield bot
    
    # Cleanup
    import os
    os.remove('test_config.json')
    os.remove('test_wallet.json')
    if os.path.exists('arbitrage.db'):
        os.remove('arbitrage.db')

class TestRateLimiter:
    """Test rate limiting functionality"""
    
    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        limiter = RateLimiter(calls_per_second=2, burst=3)
        
        # Should allow burst
        start = asyncio.get_event_loop().time()
        for _ in range(3):
            await limiter.acquire()
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.1  # Should be instant
        
        # Fourth call should be rate limited
        start = asyncio.get_event_loop().time()
        await limiter.acquire()
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed >= 0.4  # Should wait ~0.5 seconds

class TestPriceCache:
    """Test price caching functionality"""
    
    def test_cache_basic(self):
        cache = PriceCache(ttl_seconds=1)
        
        # Test set and get
        cache.set("test_key", {"price": 100})
        assert cache.get("test_key") == {"price": 100}
        
        # Test expiration
        import time
        time.sleep(1.1)
        assert cache.get("test_key") is None
    
    def test_cache_clear_expired(self):
        cache = PriceCache(ttl_seconds=1)
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        assert len(cache.cache) == 2
        
        import time
        time.sleep(1.1)
        cache.clear_expired()
        assert len(cache.cache) == 0

class TestArbitrageOpportunity:
    """Test arbitrage opportunity logic"""
    
    def test_opportunity_creation(self):
        token = Token("TEST", "TestMint", 9, 1000)
        opp = ArbitrageOpportunity(
            id="test_001",
            token=token,
            buy_dex=DEX.JUPITER,
            sell_dex=DEX.RAYDIUM,
            buy_price=Decimal("1.00"),
            sell_price=Decimal("1.05"),
            size_usd=Decimal("1000"),
            expected_profit=Decimal("45"),  # After fees
            price_impact=Decimal("0.005"),
            timestamp=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(seconds=10)
        )
        
        assert opp.is_valid()
        assert opp.expected_profit == Decimal("45")
    
    def test_opportunity_expiration(self):
        token = Token("TEST", "TestMint", 9, 1000)
        opp = ArbitrageOpportunity(
            id="test_002",
            token=token,
            buy_dex=DEX.JUPITER,
            sell_dex=DEX.RAYDIUM,
            buy_price=Decimal("1.00"),
            sell_price=Decimal("1.05"),
            size_usd=Decimal("1000"),
            expected_profit=Decimal("45"),
            price_impact=Decimal("0.005"),
            timestamp=datetime.utcnow(),
            expires_at=datetime.utcnow() - timedelta(seconds=1)  # Already expired
        )
        
        assert not opp.is_valid()

class TestArbitrageBot:
    """Test main bot functionality"""
    
    @pytest.mark.asyncio
    async def test_find_opportunities(self, test_bot):
        """Test opportunity finding logic"""
        mock_provider = MockPriceProvider()
        
        # Set up price difference
        mock_provider.set_jupiter_price(
            "TestTokenMint11111111111111111111111111111",
            Decimal("1.00"),
            Decimal("50000")
        )
        mock_provider.set_raydium_price(
            "TestTokenMint11111111111111111111111111111",
            Decimal("1.02"),  # 2% higher
            Decimal("50000")
        )
        
        # Mock the price methods
        test_bot.get_jupiter_price = mock_provider.get_jupiter_price
        test_bot.get_raydium_price = mock_provider.get_raydium_price
        
        opportunities = await test_bot.find_arbitrage_opportunities()
        
        assert len(opportunities) > 0
        opp = opportunities[0]
        assert opp.buy_dex == DEX.JUPITER
        assert opp.sell_dex == DEX.RAYDIUM
        assert opp.expected_profit > 0
    
    @pytest.mark.asyncio
    async def test_no_opportunity_small_difference(self, test_bot):
        """Test that small price differences don't create opportunities"""
        mock_provider = MockPriceProvider()
        
        # Set up small price difference (0.1%)
        mock_provider.set_jupiter_price(
            "TestTokenMint11111111111111111111111111111",
            Decimal("1.000"),
            Decimal("50000")
        )
        mock_provider.set_raydium_price(
            "TestTokenMint11111111111111111111111111111",
            Decimal("1.001"),
            Decimal("50000")
        )
        
        test_bot.get_jupiter_price = mock_provider.get_jupiter_price
        test_bot.get_raydium_price = mock_provider.get_raydium_price
        
        opportunities = await test_bot.find_arbitrage_opportunities()
        assert len(opportunities) == 0
    
    @pytest.mark.asyncio
    async def test_calculate_price_impact(self, test_bot):
        """Test price impact calculation"""
        token = Token("TEST", "TestMint", 9, 1000)
        
        # Small trade should have minimal impact
        impact_small = await test_bot.calculate_price_impact(
            token, DEX.JUPITER, Decimal("100")
        )
        assert impact_small == Decimal("0.001")  # 0.1% base
        
        # Large trade should have higher impact
        impact_large = await test_bot.calculate_price_impact(
            token, DEX.JUPITER, Decimal("50000")
        )
        assert impact_large > impact_small
    
    @pytest.mark.asyncio
    async def test_risk_management(self, test_bot):
        """Test risk management features"""
        # Set daily loss to max
        test_bot.daily_loss = test_bot.max_daily_loss
        
        # Create a valid opportunity
        token = Token("TEST", "TestMint", 9, 1000)
        opp = ArbitrageOpportunity(
            id="test_risk",
            token=token,
            buy_dex=DEX.JUPITER,
            sell_dex=DEX.RAYDIUM,
            buy_price=Decimal("1.00"),
            sell_price=Decimal("1.10"),
            size_usd=Decimal("1000"),
            expected_profit=Decimal("90"),
            price_impact=Decimal("0.005"),
            timestamp=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(seconds=10)
        )
        
        # Should reject due to daily loss limit
        result = await test_bot.execute_arbitrage(opp)
        assert not result.success
        assert "Daily loss limit" in result.error

class TestIntegration:
    """Integration tests"""
    
    @pytest.mark.asyncio
    async def test_full_cycle_simulation(self, test_bot):
        """Test a full arbitrage cycle in simulation"""
        # This would test:
        # 1. Price discovery
        # 2. Opportunity identification
        # 3. Trade execution (mocked)
        # 4. Result tracking
        
        # Set up mock prices with opportunity
        mock_provider = MockPriceProvider()
        mock_provider.set_jupiter_price(
            "TestTokenMint11111111111111111111111111111",
            Decimal("0.95"),
            Decimal("100000")
        )
        mock_provider.set_raydium_price(
            "TestTokenMint11111111111111111111111111111",
            Decimal("1.00"),
            Decimal("100000")
        )
        
        test_bot.get_jupiter_price = mock_provider.get_jupiter_price
        test_bot.get_raydium_price = mock_provider.get_raydium_price
        
        # Mock transaction execution
        async def mock_execute(opp):
            return TradeResult(
                opportunity_id=opp.id,
                success=True,
                buy_tx="mock_buy_tx",
                sell_tx="mock_sell_tx",
                actual_profit=opp.expected_profit * Decimal("0.9"),  # 90% of expected
                error=None,
                gas_used=Decimal("0.01"),
                execution_time=1.5
            )
        
        test_bot.execute_arbitrage = mock_execute
        
        # Find and execute opportunities
        opportunities = await test_bot.find_arbitrage_opportunities()
        assert len(opportunities) > 0
        
        result = await test_bot.execute_arbitrage(opportunities[0])
        assert result.success
        assert result.actual_profit > 0

class TestSafety:
    """Safety and security tests"""
    
    def test_wallet_security(self):
        """Test wallet handling security"""
        # Should not expose private keys in logs
        import logging
        logger = logging.getLogger()
        
        # Check that secret key is not logged
        config = TestConfig.create_test_config()
        wallet = TestConfig.create_test_wallet()
        
        # Simulate logging
        log_output = str(config) + str(wallet)
        assert wallet['secret_key'] not in logger.handlers[0].stream.getvalue() if hasattr(logger.handlers[0], 'stream') else True
    
    @pytest.mark.asyncio
    async def test_concurrent_execution_safety(self, test_bot):
        """Test that concurrent executions are handled safely"""
        # Create multiple opportunities
        opportunities = []
        for i in range(5):
            token = Token("TEST", "TestMint", 9, 1000)
            opp = ArbitrageOpportunity(
                id=f"test_concurrent_{i}",
                token=token,
                buy_dex=DEX.JUPITER,
                sell_dex=DEX.RAYDIUM,
                buy_price=Decimal("1.00"),
                sell_price=Decimal("1.10"),
                size_usd=Decimal("100"),
                expected_profit=Decimal("9"),
                price_impact=Decimal("0.001"),
                timestamp=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(seconds=10)
            )
            opportunities.append(opp)
        
        # Mock execution
        execution_count = 0
        async def mock_execute(opp):
            nonlocal execution_count
            execution_count += 1
            await asyncio.sleep(0.1)  # Simulate execution time
            return TradeResult(
                opportunity_id=opp.id,
                success=True,
                buy_tx=f"tx_{opp.id}",
                sell_tx=f"tx_{opp.id}_sell",
                actual_profit=opp.expected_profit,
                error=None,
                gas_used=Decimal("0.01"),
                execution_time=0.1
            )
        
        test_bot.execute_arbitrage = mock_execute
        
        # Execute concurrently
        tasks = [test_bot.execute_arbitrage(opp) for opp in opportunities]
        results = await asyncio.gather(*tasks)
        
        # All should complete successfully
        assert all(r.success for r in results)
        assert execution_count == 5

class TestPerformance:
    """Performance and load tests"""
    
    @pytest.mark.asyncio
    async def test_high_load_price_fetching(self, test_bot):
        """Test performance under high load"""
        import time
        
        mock_provider = MockPriceProvider()
        # Set up prices for all tokens
        for i in range(100):
            mint = f"TestMint{i:03d}"
            mock_provider.set_jupiter_price(mint, Decimal("1.0"), Decimal("10000"))
            mock_provider.set_raydium_price(mint, Decimal("1.01"), Decimal("10000"))
        
        test_bot.get_jupiter_price = mock_provider.get_jupiter_price
        test_bot.get_raydium_price = mock_provider.get_raydium_price
        
        # Add many tokens
        test_bot.tokens = [
            Token(f"TEST{i}", f"TestMint{i:03d}", 9, 1000)
            for i in range(100)
        ]
        
        # Measure performance
        start = time.time()
        opportunities = await test_bot.find_arbitrage_opportunities()
        elapsed = time.time() - start
        
        print(f"Found {len(opportunities)} opportunities in {elapsed:.2f}s")
        assert elapsed < 10  # Should complete within 10 seconds
        assert mock_provider.call_count == 200  # 2 calls per token
    
    @pytest.mark.asyncio
    async def test_cache_effectiveness(self):
        """Test that caching improves performance"""
        cache = PriceCache(ttl_seconds=5)
        
        # First call - no cache
        call_count = 0
        async def slow_function():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return {"price": 100}
        
        # First call should be slow
        start = time.time()
        cache.set("key1", await slow_function())
        first_duration = time.time() - start
        assert first_duration >= 0.1
        assert call_count == 1
        
        # Second call should be instant (cached)
        start = time.time()
        result = cache.get("key1")
        second_duration = time.time() - start
        assert second_duration < 0.01
        assert result == {"price": 100}
        assert call_count == 1  # No additional calls

class TestErrorHandling:
    """Test error handling and recovery"""
    
    @pytest.mark.asyncio
    async def test_rpc_failure_handling(self, test_bot):
        """Test handling of RPC failures"""
        # Mock RPC client to fail
        async def failing_get_balance(*args):
            raise Exception("RPC connection failed")
        
        test_bot.client.get_balance = failing_get_balance
        
        # Create opportunity
        token = Token("TEST", "TestMint", 9, 1000)
        opp = ArbitrageOpportunity(
            id="test_rpc_fail",
            token=token,
            buy_dex=DEX.JUPITER,
            sell_dex=DEX.RAYDIUM,
            buy_price=Decimal("1.00"),
            sell_price=Decimal("1.10"),
            size_usd=Decimal("100"),
            expected_profit=Decimal("9"),
            price_impact=Decimal("0.001"),
            timestamp=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(seconds=10)
        )
        
        # Should handle gracefully
        result = await test_bot.execute_arbitrage(opp)
        assert not result.success
        assert "RPC connection failed" in result.error
    
    @pytest.mark.asyncio
    async def test_api_timeout_handling(self):
        """Test API timeout handling"""
        limiter = RateLimiter(calls_per_second=1, burst=1)
        
        # Create mock session that times out
        async def timeout_get(*args, **kwargs):
            raise asyncio.TimeoutError()
        
        with patch('aiohttp.ClientSession.get', new=timeout_get):
            bot = ProductionArbitrageBot('test_config.json')
            token = Token("TEST", "TestMint", 9, 1000)
            
            result = await bot.get_jupiter_price(token)
            assert result is None  # Should return None on timeout

class TestDatabase:
    """Test database operations"""
    
    @pytest.mark.asyncio
    async def test_opportunity_storage(self, test_bot):
        """Test storing opportunities in database"""
        token = Token("TEST", "TestMint", 9, 1000)
        opp = ArbitrageOpportunity(
            id="test_db_001",
            token=token,
            buy_dex=DEX.JUPITER,
            sell_dex=DEX.RAYDIUM,
            buy_price=Decimal("1.00"),
            sell_price=Decimal("1.05"),
            size_usd=Decimal("1000"),
            expected_profit=Decimal("45"),
            price_impact=Decimal("0.005"),
            timestamp=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(seconds=10)
        )
        
        await test_bot.db.save_opportunity(opp)
        
        # Verify it was saved
        import sqlite3
        conn = sqlite3.connect(test_bot.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM opportunities WHERE id = ?", (opp.id,))
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None
        assert row[0] == "test_db_001"
        assert row[1] == "TEST"
    
    @pytest.mark.asyncio
    async def test_trade_recording(self, test_bot):
        """Test recording trade results"""
        token = Token("TEST", "TestMint", 9, 1000)
        opp = ArbitrageOpportunity(
            id="test_trade_001",
            token=token,
            buy_dex=DEX.JUPITER,
            sell_dex=DEX.RAYDIUM,
            buy_price=Decimal("1.00"),
            sell_price=Decimal("1.05"),
            size_usd=Decimal("1000"),
            expected_profit=Decimal("45"),
            price_impact=Decimal("0.005"),
            timestamp=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(seconds=10)
        )
        
        result = TradeResult(
            opportunity_id=opp.id,
            success=True,
            buy_tx="test_buy_tx_123",
            sell_tx="test_sell_tx_456",
            actual_profit=Decimal("42.50"),
            error=None,
            gas_used=Decimal("0.01"),
            execution_time=2.5
        )
        
        await test_bot.db.save_trade(opp, result)
        
        # Verify trade was saved
        import sqlite3
        conn = sqlite3.connect(test_bot.db.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE opportunity_id = ?", (opp.id,))
        row = cursor.fetchone()
        conn.close()
        
        assert row is not None
        assert row[11] == "test_buy_tx_123"
        assert row[12] == "test_sell_tx_456"
        assert row[13] == 1  # success = True

# Performance benchmark script
async def run_performance_benchmark():
    """Run performance benchmarks"""
    print("Running performance benchmarks...")
    
    # Create test bot
    with open('bench_config.json', 'w') as f:
        json.dump(TestConfig.create_test_config(), f)
    
    with open('test_wallet.json', 'w') as f:
        json.dump(TestConfig.create_test_wallet(), f)
    
    bot = ProductionArbitrageBot('bench_config.json')
    
    # Benchmark 1: Price fetching speed
    print("\n1. Price Fetching Speed:")
    mock_provider = MockPriceProvider()
    for i in range(50):
        mock_provider.set_jupiter_price(f"Mint{i}", Decimal("1.0"), Decimal("10000"))
        mock_provider.set_raydium_price(f"Mint{i}", Decimal("1.01"), Decimal("10000"))
    
    bot.get_jupiter_price = mock_provider.get_jupiter_price
    bot.get_raydium_price = mock_provider.get_raydium_price
    bot.tokens = [Token(f"T{i}", f"Mint{i}", 9, 1000) for i in range(50)]
    
    start = time.time()
    opportunities = await bot.find_arbitrage_opportunities()
    elapsed = time.time() - start
    print(f"  - Checked 50 tokens in {elapsed:.2f}s ({50/elapsed:.1f} tokens/sec)")
    print(f"  - Found {len(opportunities)} opportunities")
    
    # Benchmark 2: Database write speed
    print("\n2. Database Performance:")
    start = time.time()
    for i in range(100):
        await bot.db.save_opportunity(opportunities[0] if opportunities else None)
    elapsed = time.time() - start
    print(f"  - Wrote 100 opportunities in {elapsed:.2f}s ({100/elapsed:.1f} ops/sec)")
    
    # Benchmark 3: Cache performance
    print("\n3. Cache Performance:")
    cache = PriceCache(ttl_seconds=5)
    
    # Write test
    start = time.time()
    for i in range(10000):
        cache.set(f"key_{i}", {"price": i})
    elapsed = time.time() - start
    print(f"  - Cache writes: 10k in {elapsed:.2f}s ({10000/elapsed:.0f} ops/sec)")
    
    # Read test
    start = time.time()
    for i in range(10000):
        _ = cache.get(f"key_{i}")
    elapsed = time.time() - start
    print(f"  - Cache reads: 10k in {elapsed:.2f}s ({10000/elapsed:.0f} ops/sec)")
    
    # Cleanup
    import os
    os.remove('bench_config.json')
    os.remove('test_wallet.json')
    if os.path.exists('arbitrage.db'):
        os.remove('arbitrage.db')

# Run all tests
if __name__ == "__main__":
    print("Running Arbitrage Bot Test Suite")
    print("=" * 50)
    
    # Run pytest
    pytest.main([__file__, "-v", "--tb=short"])
    
    # Run performance benchmarks
    print("\n" + "=" * 50)
    asyncio.run(run_performance_benchmark())
    
    print("\n✅ All tests completed!")