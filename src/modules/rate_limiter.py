# ===== src/modules/rate_limiter.py =====
"""
Rate limiting module for API calls
"""

import asyncio
import time
from typing import Optional, Dict, Any
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class RateLimitConfig:
    """Rate limit configuration"""
    calls_per_second: float
    burst: int
    name: str

class RateLimiter:
    """Token bucket rate limiter with burst support"""
    
    def __init__(self, calls_per_second: float, burst: int = 5, name: str = "default"):
        self.calls_per_second = calls_per_second
        self.burst = burst
        self.name = name
        self.tokens = float(burst)
        self.last_update = time.time()
        self.lock = asyncio.Lock()
        self.total_requests = 0
        self.total_wait_time = 0.0
    
    async def acquire(self, tokens: int = 1):
        """Acquire tokens, waiting if necessary"""
        async with self.lock:
            self.total_requests += 1
            start_wait = time.time()
            
            # Update available tokens
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.calls_per_second)
            self.last_update = now
            
            # Wait if not enough tokens
            if self.tokens < tokens:
                wait_time = (tokens - self.tokens) / self.calls_per_second
                logger.debug(f"Rate limiter {self.name}: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                
                # Update tokens after wait
                now = time.time()
                elapsed = now - self.last_update
                self.tokens = min(self.burst, self.tokens + elapsed * self.calls_per_second)
                self.last_update = now
            
            # Consume tokens
            self.tokens -= tokens
            
            # Track wait time
            wait_duration = time.time() - start_wait
            self.total_wait_time += wait_duration
            
            if wait_duration > 0.1:  # Log significant waits
                logger.info(f"Rate limiter {self.name}: waited {wait_duration:.2f}s")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics"""
        avg_wait = self.total_wait_time / self.total_requests if self.total_requests > 0 else 0
        
        return {
            'name': self.name,
            'total_requests': self.total_requests,
            'total_wait_time': self.total_wait_time,
            'average_wait_time': avg_wait,
            'current_tokens': self.tokens,
            'calls_per_second': self.calls_per_second,
            'burst': self.burst
        }

class RateLimiterGroup:
    """Manage multiple rate limiters"""
    
    def __init__(self, configs: Dict[str, RateLimitConfig]):
        self.limiters = {
            name: RateLimiter(
                config.calls_per_second,
                config.burst,
                config.name
            )
            for name, config in configs.items()
        }
    
    def get(self, name: str) -> Optional[RateLimiter]:
        """Get a specific rate limiter"""
        return self.limiters.get(name)
    
    async def acquire(self, name: str, tokens: int = 1):
        """Acquire from a specific rate limiter"""
        limiter = self.limiters.get(name)
        if limiter:
            await limiter.acquire(tokens)
        else:
            logger.warning(f"Unknown rate limiter: {name}")
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """Get statistics for all rate limiters"""
        return {
            name: limiter.get_stats()
            for name, limiter in self.limiters.items()
        }

# Create default rate limiters
DEFAULT_RATE_LIMITS = {
    'jupiter': RateLimitConfig(calls_per_second=10, burst=20, name='jupiter'),
    'raydium': RateLimitConfig(calls_per_second=5, burst=10, name='raydium'),
    'dexscreener': RateLimitConfig(calls_per_second=3, burst=5, name='dexscreener'),
    'rpc': RateLimitConfig(calls_per_second=40, burst=50, name='rpc'),
    'transaction': RateLimitConfig(calls_per_second=5, burst=10, name='transaction')
}
