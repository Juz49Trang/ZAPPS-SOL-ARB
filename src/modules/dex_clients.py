"""
DEX API clients for Jupiter, Raydium, Orca, etc.
"""

import aiohttp
import asyncio
from typing import Dict, List, Optional, Tuple, Any
from decimal import Decimal
import logging
from dataclasses import dataclass
import base64

from ..constants import (
    API_ENDPOINTS, 
    DEFAULT_SLIPPAGE_BPS,
    WRAPPED_SOL_MINT,
    USDC_MINT
)

logger = logging.getLogger(__name__)

@dataclass
class QuoteResponse:
    """Standardized quote response"""
    input_mint: str
    output_mint: str
    input_amount: int
    output_amount: int
    price: Decimal
    price_impact: Decimal
    fee: Decimal
    route: List[str]
    raw_response: Dict[str, Any]

class BaseDEXClient:
    """Base class for DEX clients"""
    
    def __init__(self, rate_limiter=None):
        self.rate_limiter = rate_limiter
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def _request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        """Make HTTP request with rate limiting"""
        if self.rate_limiter:
            await self.rate_limiter.acquire()
        
        try:
            if not self.session:
                self.session = aiohttp.ClientSession()
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.request(
                method, url, timeout=timeout, **kwargs
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.error(f"Request failed: {response.status} - {url}")
                    return None
                    
        except asyncio.TimeoutError:
            logger.error(f"Request timeout: {url}")
            return None
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None

class JupiterClient(BaseDEXClient):
    """Jupiter aggregator client"""
    
    def __init__(self, rate_limiter=None):
        super().__init__(rate_limiter)
        self.base_url = API_ENDPOINTS["jupiter_quote"]
        self.swap_url = API_ENDPOINTS["jupiter_swap"]
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS
    ) -> Optional[QuoteResponse]:
        """Get quote from Jupiter"""
        params = {
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': amount,
            'slippageBps': slippage_bps,
            'onlyDirectRoutes': 'false',
            'asLegacyTransaction': 'false'
        }
        
        data = await self._request('GET', self.base_url, params=params)
        if not data:
            return None
        
        try:
            # Parse response
            output_amount = int(data['outAmount'])
            price = Decimal(output_amount) / Decimal(amount)
            
            # Calculate price impact
            price_impact = Decimal(data.get('priceImpactPct', '0'))
            
            # Extract fee
            total_fee = sum(
                Decimal(fee.get('amount', '0')) 
                for route in data.get('routePlan', [])
                for fee in route.get('fees', {}).values()
            )
            
            # Extract route
            route_names = []
            for route in data.get('routePlan', []):
                swap_info = route.get('swapInfo', {})
                label = swap_info.get('label', 'Unknown')
                route_names.append(label)
            
            return QuoteResponse(
                input_mint=input_mint,
                output_mint=output_mint,
                input_amount=amount,
                output_amount=output_amount,
                price=price,
                price_impact=price_impact / 100,  # Convert to decimal
                fee=total_fee,
                route=route_names,
                raw_response=data
            )
            
        except Exception as e:
            logger.error(f"Error parsing Jupiter quote: {e}")
            return None
    
    async def get_swap_transaction(
        self,
        quote_response: Dict[str, Any],
        user_public_key: str,
        wrap_unwrap_sol: bool = True,
        compute_unit_price: Optional[int] = None
    ) -> Optional[str]:
        """Get swap transaction from Jupiter"""
        swap_data = {
            'quoteResponse': quote_response,
            'userPublicKey': user_public_key,
            'wrapAndUnwrapSol': wrap_unwrap_sol,
            'dynamicComputeUnitLimit': True,
            'prioritizationFeeLamports': 'auto'
        }
        
        if compute_unit_price:
            swap_data['computeUnitPriceMicroLamports'] = compute_unit_price
        
        data = await self._request(
            'POST',
            self.swap_url,
            json=swap_data,
            headers={'Content-Type': 'application/json'}
        )
        
        if data and 'swapTransaction' in data:
            return data['swapTransaction']
        
        return None
    
    async def get_token_price(self, token_mint: str) -> Optional[Decimal]:
        """Get token price in USDC"""
        quote = await self.get_quote(
            input_mint=token_mint,
            output_mint=USDC_MINT,
            amount=10 ** 9  # 1 token with 9 decimals
        )
        
        if quote:
            return quote.price
        
        return None

class RaydiumClient(BaseDEXClient):
    """Raydium DEX client"""
    
    def __init__(self, rate_limiter=None):
        super().__init__(rate_limiter)
        self.base_url = API_ENDPOINTS["raydium_api"]
    
    async def get_pools(self) -> Optional[List[Dict]]:
        """Get all Raydium pools"""
        data = await self._request('GET', f"{self.base_url}/main/pairs")
        return data if data else []
    
    async def get_pool_by_mints(
        self,
        mint1: str,
        mint2: str
    ) -> Optional[Dict]:
        """Find pool by token mints"""
        pools = await self.get_pools()
        if not pools:
            return None
        
        for pool in pools:
            pool_mints = {pool.get('baseMint'), pool.get('quoteMint')}
            if {mint1, mint2} == pool_mints:
                return pool
        
        return None
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS
    ) -> Optional[QuoteResponse]:
        """Get quote from Raydium"""
        # Find the pool
        pool = await self.get_pool_by_mints(input_mint, output_mint)
        if not pool:
            return None
        
        try:
            # Calculate output amount (simplified)
            # In production, you'd use the actual AMM math
            base_reserve = Decimal(pool.get('baseReserve', 0))
            quote_reserve = Decimal(pool.get('quoteReserve', 0))
            
            if pool.get('baseMint') == input_mint:
                # Selling base for quote
                output_amount = (Decimal(amount) * quote_reserve) / (base_reserve + Decimal(amount))
            else:
                # Selling quote for base
                output_amount = (Decimal(amount) * base_reserve) / (quote_reserve + Decimal(amount))
            
            # Calculate price and impact
            price = output_amount / Decimal(amount)
            price_impact = Decimal(amount) / (base_reserve + quote_reserve) * 100
            
            return QuoteResponse(
                input_mint=input_mint,
                output_mint=output_mint,
                input_amount=amount,
                output_amount=int(output_amount),
                price=price,
                price_impact=price_impact / 100,
                fee=Decimal('0.0025'),  # 0.25% fee
                route=['Raydium'],
                raw_response=pool
            )
            
        except Exception as e:
            logger.error(f"Error calculating Raydium quote: {e}")
            return None

class DexScreenerClient(BaseDEXClient):
    """DexScreener API client for price data"""
    
    def __init__(self, rate_limiter=None):
        super().__init__(rate_limiter)
        self.base_url = API_ENDPOINTS["dexscreener"]
    
    async def get_token_info(self, token_mint: str) -> Optional[Dict]:
        """Get token information from DexScreener"""
        url = f"{self.base_url}/tokens/{token_mint}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        
        return await self._request('GET', url, headers=headers)
    
    async def get_token_prices_by_dex(
        self,
        token_mint: str
    ) -> Dict[str, Tuple[Decimal, Decimal]]:
        """Get token prices from different DEXs
        Returns: {dex_name: (price, liquidity)}
        """
        data = await self.get_token_info(token_mint)
        if not data:
            return {}
        
        prices_by_dex = {}
        pairs = data.get('pairs', [])
        
        for pair in pairs:
            dex = pair.get('dexId', '').lower()
            price = Decimal(pair.get('priceUsd', '0'))
            liquidity = Decimal(pair.get('liquidity', {}).get('usd', '0'))
            
            if price > 0 and liquidity > 0:
                # Keep the highest liquidity pair per DEX
                if dex not in prices_by_dex or liquidity > prices_by_dex[dex][1]:
                    prices_by_dex[dex] = (price, liquidity)
        
        return prices_by_dex
    
    async def get_pair_info(self, pair_address: str) -> Optional[Dict]:
        """Get specific pair information"""
        url = f"{self.base_url}/pairs/solana/{pair_address}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        
        data = await self._request('GET', url, headers=headers)
        if data and 'pair' in data:
            return data['pair']
        
        return None

class OrcaClient(BaseDEXClient):
    """Orca Whirlpool client"""
    
    def __init__(self, rate_limiter=None):
        super().__init__(rate_limiter)
        # Orca doesn't have a public HTTP API, so we'd need to interact
        # with the on-chain program directly. This is a placeholder.
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS
    ) -> Optional[QuoteResponse]:
        """Get quote from Orca (placeholder)"""
        # In production, this would interact with Orca's SDK
        logger.warning("Orca client not implemented - using placeholder")
        return None

class MeteoraClient(BaseDEXClient):
    """Meteora DLMM client"""
    
    def __init__(self, rate_limiter=None):
        super().__init__(rate_limiter)
        # Meteora API endpoint would go here
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS
    ) -> Optional[QuoteResponse]:
        """Get quote from Meteora (placeholder)"""
        logger.warning("Meteora client not implemented - using placeholder")
        return None

class UnifiedDEXClient:
    """Unified interface for all DEX clients"""
    
    def __init__(self, rate_limiters: Dict[str, Any] = None):
        self.rate_limiters = rate_limiters or {}
        
        # Initialize all DEX clients
        self.clients = {
            'jupiter': JupiterClient(self.rate_limiters.get('jupiter')),
            'raydium': RaydiumClient(self.rate_limiters.get('raydium')),
            'orca': OrcaClient(self.rate_limiters.get('orca')),
            'meteora': MeteoraClient(self.rate_limiters.get('meteora'))
        }
        
        # DexScreener for price discovery
        self.dexscreener = DexScreenerClient(self.rate_limiters.get('dexscreener'))
    
    async def __aenter__(self):
        for client in self.clients.values():
            await client.__aenter__()
        await self.dexscreener.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        for client in self.clients.values():
            await client.__aexit__(exc_type, exc_val, exc_tb)
        await self.dexscreener.__aexit__(exc_type, exc_val, exc_tb)
    
    async def get_best_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
        dexs: List[str] = None
    ) -> Optional[Tuple[str, QuoteResponse]]:
        """Get best quote from all DEXs
        Returns: (dex_name, quote)
        """
        if dexs is None:
            dexs = list(self.clients.keys())
        
        # Get quotes from all DEXs in parallel
        tasks = []
        for dex in dexs:
            if dex in self.clients:
                client = self.clients[dex]
                task = client.get_quote(input_mint, output_mint, amount, slippage_bps)
                tasks.append((dex, task))
        
        # Wait for all quotes
        results = []
        for dex, task in tasks:
            try:
                quote = await task
                if quote:
                    results.append((dex, quote))
            except Exception as e:
                logger.error(f"Error getting quote from {dex}: {e}")
        
        # Find best quote (highest output amount)
        if not results:
            return None
        
        best_dex, best_quote = max(results, key=lambda x: x[1].output_amount)
        return best_dex, best_quote
    
    async def get_all_prices(
        self,
        token_mint: str
    ) -> Dict[str, Decimal]:
        """Get token prices from all sources"""
        prices = {}
        
        # Get from DexScreener
        dex_prices = await self.dexscreener.get_token_prices_by_dex(token_mint)
        for dex, (price, _) in dex_prices.items():
            prices[f"{dex}_spot"] = price
        
        # Get from Jupiter
        jup_price = await self.clients['jupiter'].get_token_price(token_mint)
        if jup_price:
            prices['jupiter_quote'] = jup_price
        
        return prices