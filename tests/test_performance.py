# ===== tests/test_performance.py =====
"""Performance and load tests"""

import pytest
import asyncio
import time
from decimal import Decimal

from src.modules.price_cache import PriceCache, MultiLevelCache
from src.modules.rate_limiter import RateLimiter, RateLimiterGroup
from src.constants import DEFAULT_RATE_LIMITS

class TestCachePerformance:
    """Test cache performance"""
    
    @pytest.mark.asyncio
    async def test_cache_throughput(self):
        """Test cache read/write throughput"""
        cache = PriceCache(ttl_seconds=60, max_size=10000)
        
        # Write test
        start = time.time()
        for i in range(10000):
            await cache.set(f"key_{i}", {"price": i})
        write_duration = time.time() - start
        writes_per_second = 10000 / write_duration
        
        assert writes_per_second > 1000  # Should handle >1000 writes/sec
        
        # Read test
        start = time.time()
        for i in range(10000):
            value = await cache.get(f"key_{i}")
            assert value is not None
        read_duration = time.time() - start
        reads_per_second = 10000 / read_duration
        
        assert reads_per_second > 5000  # Should handle >5000 reads/sec
        
        print(f"\nCache Performance:")
        print(f"  Writes: {writes_per_second:.0f} ops/sec")
        print(f"  Reads: {reads_per_second:.0f} ops/sec")
    
    @pytest.mark.asyncio
    async def test_multi_level_cache(self):
        """Test multi-level cache performance"""
        cache = MultiLevelCache()
        
        # Test different cache levels
        token_mint = "TestMint123"
        
        # Price cache (3 second TTL)
        await cache.set_price(token_mint, "jupiter", Decimal("150.5"))
        price = await cache.get_price(token_mint, "jupiter")
        assert price == Decimal("150.5")
        
        # Quote cache (5 second TTL)
        quote_data = {"output_amount": 1000000}
        await cache.set_quote(token_mint, "USDC", 1000, "jupiter", quote_data)
        quote = await cache.get_quote(token_mint, "USDC", 1000, "jupiter")
        assert quote == quote_data
        
        # Test expiration
        await asyncio.sleep(3.1)
        price = await cache.get_price(token_mint, "jupiter")
        assert price is None  # Should be expired
        
        quote = await cache.get_quote(token_mint, "USDC", 1000, "jupiter")
        assert quote is not None  # Should still be valid

class TestRateLimiterPerformance:
    """Test rate limiter performance"""
    
    @pytest.mark.asyncio
    async def test_rate_limiter_accuracy(self):
        """Test rate limiter accuracy"""
        limiter = RateLimiter(calls_per_second=10, burst=5)
        
        # Test burst
        start = time.time()
        for _ in range(5):
            await limiter.acquire()
        burst_duration = time.time() - start
        
        assert burst_duration < 0.1  # Burst should be instant
        
        # Test rate limiting
        start = time.time()
        for _ in range(10):
            await limiter.acquire()
        rate_duration = time.time() - start
        
        # Should take ~0.5 seconds (5 burst already used + 5 more at 10/sec)
        assert 0.4 < rate_duration < 0.7
    
    @pytest.mark.asyncio
    async def test_rate_limiter_group(self):
        """Test rate limiter group"""
        group = RateLimiterGroup(DEFAULT_RATE_LIMITS)
        
        # Test different limiters
        start = time.time()
        await group.acquire('jupiter')  # 10/sec
        await group.acquire('raydium')  # 5/sec
        await group.acquire('dexscreener')  # 3/sec
        duration = time.time() - start
        
        assert duration < 0.1  # All should be instant (burst)
        
        # Get stats
        stats = group.get_all_stats()
        assert stats['jupiter']['total_requests'] == 1
        assert stats['raydium']['total_requests'] == 1
        assert stats['dexscreener']['total_requests'] == 1