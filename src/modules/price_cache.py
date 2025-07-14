# ===== src/modules/price_cache.py =====
"""
Price caching module for efficient price lookups
"""

import time
from typing import Any, Optional, Dict, Tuple
from decimal import Decimal
import asyncio
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)

class PriceCache:
    """Thread-safe in-memory price cache with TTL and size limits"""
    
    def __init__(self, ttl_seconds: int = 5, max_size: int = 1000):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self.cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self.lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        async with self.lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                
                # Check if expired
                if time.time() - timestamp < self.ttl:
                    # Move to end (LRU)
                    self.cache.move_to_end(key)
                    self.hits += 1
                    return value
                else:
                    # Expired, remove it
                    del self.cache[key]
            
            self.misses += 1
            return None
    
    async def set(self, key: str, value: Any):
        """Set value in cache"""
        async with self.lock:
            # Remove oldest items if cache is full
            while len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
            
            self.cache[key] = (value, time.time())
            self.cache.move_to_end(key)
    
    async def clear_expired(self):
        """Remove all expired entries"""
        async with self.lock:
            current_time = time.time()
            expired_keys = [
                key for key, (_, timestamp) in self.cache.items()
                if current_time - timestamp >= self.ttl
            ]
            
            for key in expired_keys:
                del self.cache[key]
            
            return len(expired_keys)
    
    async def clear(self):
        """Clear entire cache"""
        async with self.lock:
            self.cache.clear()
            self.hits = 0
            self.misses = 0
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_requests = self.hits + self.misses
        hit_rate = self.hits / total_requests if total_requests > 0 else 0
        
        return {
            'size': len(self.cache),
            'max_size': self.max_size,
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': hit_rate,
            'ttl_seconds': self.ttl
        }
    
    def make_key(self, *args) -> str:
        """Create cache key from arguments"""
        return ':'.join(str(arg) for arg in args)

class MultiLevelCache:
    """Multi-level cache with different TTLs for different data types"""
    
    def __init__(self):
        self.caches = {
            'price': PriceCache(ttl_seconds=3, max_size=500),
            'quote': PriceCache(ttl_seconds=5, max_size=200),
            'token_info': PriceCache(ttl_seconds=300, max_size=100),
            'pool_info': PriceCache(ttl_seconds=60, max_size=200),
        }
    
    async def get_price(self, token_mint: str, dex: str) -> Optional[Decimal]:
        """Get cached price"""
        cache = self.caches['price']
        key = cache.make_key('price', token_mint, dex)
        return await cache.get(key)
    
    async def set_price(self, token_mint: str, dex: str, price: Decimal):
        """Cache price"""
        cache = self.caches['price']
        key = cache.make_key('price', token_mint, dex)
        await cache.set(key, price)
    
    async def get_quote(self, input_mint: str, output_mint: str, amount: int, dex: str) -> Optional[Dict]:
        """Get cached quote"""
        cache = self.caches['quote']
        key = cache.make_key('quote', input_mint, output_mint, amount, dex)
        return await cache.get(key)
    
    async def set_quote(self, input_mint: str, output_mint: str, amount: int, dex: str, quote: Dict):
        """Cache quote"""
        cache = self.caches['quote']
        key = cache.make_key('quote', input_mint, output_mint, amount, dex)
        await cache.set(key, quote)
    
    async def clear_all_expired(self):
        """Clear expired entries from all caches"""
        total_cleared = 0
        for name, cache in self.caches.items():
            cleared = await cache.clear_expired()
            total_cleared += cleared
            logger.debug(f"Cleared {cleared} expired entries from {name} cache")
        
        return total_cleared
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """Get statistics for all caches"""
        return {
            name: cache.get_stats()
            for name, cache in self.caches.items()
        }
