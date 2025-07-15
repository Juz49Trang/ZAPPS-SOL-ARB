"""
Production-Ready Solana Arbitrage Bot
Author: Solana Arbitrage System
Version: 2.0.0
"""

import asyncio
import aiohttp
import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import logging
from concurrent.futures import ThreadPoolExecutor
import signal
import sys

# Solana imports
from solana.rpc import types
from solana.rpc.async_api import AsyncClient
from solana.transaction import Transaction
from solana.rpc.commitment import Confirmed, Finalized
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.instruction import Instruction, AccountMeta
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
import base58
import base64

# Database
import sqlite3
from contextlib import asynccontextmanager

# Monitoring
import prometheus_client
from prometheus_client import Counter, Histogram, Gauge

# Import Jito client - Fixed import
try:
    from modules.jito_client import JitoClient, JitoConfig
except ImportError:
    # If running from src directory
    try:
        from .modules.jito_client import JitoClient, JitoConfig
    except ImportError:
        # Jito not available - will run without it
        JitoClient = None
        JitoConfig = None

# Setup structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/arbitrage_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Metrics
trade_counter = Counter('arbitrage_trades_total', 'Total number of arbitrage trades')
profit_histogram = Histogram('arbitrage_profit_usd', 'Profit distribution in USD')
opportunity_gauge = Gauge('arbitrage_opportunities_active', 'Current number of active opportunities')
balance_gauge = Gauge('wallet_balance_usd', 'Current wallet balance in USD')

class DEX(Enum):
    JUPITER = "jupiter"
    RAYDIUM = "raydium"
    ORCA = "orca"
    METEORA = "meteora"

@dataclass
class Token:
    symbol: str
    mint: str
    decimals: int
    min_liquidity: float = 10000.0  # Minimum liquidity in USD

@dataclass
class ArbitrageOpportunity:
    id: str
    token: Token
    buy_dex: DEX
    sell_dex: DEX
    buy_price: Decimal
    sell_price: Decimal
    size_usd: Decimal
    expected_profit: Decimal
    price_impact: Decimal
    timestamp: datetime
    expires_at: datetime
    
    def is_valid(self) -> bool:
        return datetime.utcnow() < self.expires_at

@dataclass
class TradeResult:
    opportunity_id: str
    success: bool
    buy_tx: Optional[str]
    sell_tx: Optional[str]
    actual_profit: Optional[Decimal]
    error: Optional[str]
    gas_used: Decimal
    execution_time: float

class RateLimiter:
    """Advanced rate limiter with burst support"""
    def __init__(self, calls_per_second: int, burst: int = 5):
        self.calls_per_second = calls_per_second
        self.burst = burst
        self.tokens = burst
        self.last_update = time.time()
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.calls_per_second)
            self.last_update = now
            
            if self.tokens < 1:
                sleep_time = (1 - self.tokens) / self.calls_per_second
                await asyncio.sleep(sleep_time)
                self.tokens = 1
            
            self.tokens -= 1

class PriceCache:
    """In-memory price cache with TTL"""
    def __init__(self, ttl_seconds: int = 5):
        self.cache = {}
        self.ttl = ttl_seconds
    
    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            del self.cache[key]
        return None
    
    def set(self, key: str, value: Any):
        self.cache[key] = (value, time.time())
    
    def clear_expired(self):
        current_time = time.time()
        expired_keys = [
            key for key, (_, timestamp) in self.cache.items()
            if current_time - timestamp >= self.ttl
        ]
        for key in expired_keys:
            del self.cache[key]

class TransactionBuilder:
    """Build optimized transactions with priority fees"""
    
    @staticmethod
    async def build_jupiter_swap(
        client: AsyncClient,
        wallet: Keypair,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50,
        priority_fee: int = 10000  # microlamports
    ) -> Optional[VersionedTransaction]:
        """Build Jupiter swap transaction with priority fees"""
        try:
            # Get quote
            async with aiohttp.ClientSession() as session:
                quote_url = "https://quote-api.jup.ag/v6/quote"
                params = {
                    'inputMint': input_mint,
                    'outputMint': output_mint,
                    'amount': amount,
                    'slippageBps': slippage_bps,
                    'maxAccounts': 64
                }
                
                async with session.get(quote_url, params=params) as response:
                    if response.status != 200:
                        return None
                    quote = await response.json()
                
                # Get swap transaction
                swap_url = "https://quote-api.jup.ag/v6/swap"
                swap_data = {
                    'quoteResponse': quote,
                    'userPublicKey': str(wallet.pubkey()),
                    'wrapAndUnwrapSol': True,
                    'computeUnitPriceMicroLamports': priority_fee,
                    'dynamicComputeUnitLimit': True
                }
                
                async with session.post(swap_url, json=swap_data) as response:
                    if response.status != 200:
                        return None
                    swap_response = await response.json()
                
                # Deserialize transaction
                tx_data = base64.b64decode(swap_response['swapTransaction'])
                return VersionedTransaction.from_bytes(tx_data)
                
        except Exception as e:
            logger.error(f"Error building Jupiter swap: {e}")
            return None
    
    @staticmethod
    async def build_raydium_swap_via_jupiter(
        client: AsyncClient,
        wallet: Keypair,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50,
        priority_fee: int = 10000
    ) -> Optional[VersionedTransaction]:
        """Build Raydium swap using Jupiter's routing (which includes Raydium)"""
        try:
            # Jupiter will automatically route through Raydium if it's the best price
            async with aiohttp.ClientSession() as session:
                quote_url = "https://quote-api.jup.ag/v6/quote"
                params = {
                    'inputMint': input_mint,
                    'outputMint': output_mint,
                    'amount': amount,
                    'slippageBps': slippage_bps,
                    'onlyDirectRoutes': 'true',  # Fixed: Changed from True to 'true'
                    'maxAccounts': 64
                }
                
                async with session.get(quote_url, params=params) as response:
                    if response.status != 200:
                        return None
                    quote = await response.json()
                
                # Get swap transaction
                swap_url = "https://quote-api.jup.ag/v6/swap"
                swap_data = {
                    'quoteResponse': quote,
                    'userPublicKey': str(wallet.pubkey()),
                    'wrapAndUnwrapSol': True,
                    'computeUnitPriceMicroLamports': priority_fee,
                    'dynamicComputeUnitLimit': True
                }
                
                async with session.post(swap_url, json=swap_data) as response:
                    if response.status != 200:
                        return None
                    swap_response = await response.json()
                
                # Deserialize transaction
                tx_data = base64.b64decode(swap_response['swapTransaction'])
                return VersionedTransaction.from_bytes(tx_data)
                
        except Exception as e:
            logger.error(f"Error building Raydium swap via Jupiter: {e}")
            return None

class ArbitrageDatabase:
    """SQLite database for trade history and analytics"""
    
    def __init__(self, db_path: str = "data/arbitrage.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                opportunity_id TEXT,
                token_symbol TEXT,
                token_mint TEXT,
                buy_dex TEXT,
                sell_dex TEXT,
                buy_price REAL,
                sell_price REAL,
                size_usd REAL,
                expected_profit REAL,
                actual_profit REAL,
                buy_tx TEXT,
                sell_tx TEXT,
                success BOOLEAN,
                error TEXT,
                gas_used REAL,
                execution_time REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY,
                token_symbol TEXT,
                token_mint TEXT,
                buy_dex TEXT,
                sell_dex TEXT,
                buy_price REAL,
                sell_price REAL,
                size_usd REAL,
                expected_profit REAL,
                price_impact REAL,
                discovered_at DATETIME,
                expires_at DATETIME,
                executed BOOLEAN DEFAULT FALSE
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_mint)
        """)
        
        conn.commit()
        conn.close()
    
    async def save_opportunity(self, opp: ArbitrageOpportunity):
        """Save discovered opportunity"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO opportunities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            opp.id,
            opp.token.symbol,
            opp.token.mint,
            opp.buy_dex.value,
            opp.sell_dex.value,
            float(opp.buy_price),
            float(opp.sell_price),
            float(opp.size_usd),
            float(opp.expected_profit),
            float(opp.price_impact),
            opp.timestamp,
            opp.expires_at,
            False
        ))
        
        conn.commit()
        conn.close()
    
    async def save_trade(self, opp: ArbitrageOpportunity, result: TradeResult):
        """Save executed trade"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"trade_{int(time.time() * 1000000)}",
            result.opportunity_id,
            opp.token.symbol,
            opp.token.mint,
            opp.buy_dex.value,
            opp.sell_dex.value,
            float(opp.buy_price),
            float(opp.sell_price),
            float(opp.size_usd),
            float(opp.expected_profit),
            float(result.actual_profit) if result.actual_profit else None,
            result.buy_tx,
            result.sell_tx,
            result.success,
            result.error,
            float(result.gas_used),
            result.execution_time,
            datetime.utcnow()
        ))
        
        # Mark opportunity as executed
        cursor.execute("""
            UPDATE opportunities SET executed = TRUE WHERE id = ?
        """, (opp.id,))
        
        conn.commit()
        conn.close()
        
        # Update metrics
        trade_counter.inc()
        if result.actual_profit:
            profit_histogram.observe(float(result.actual_profit))

class ProductionArbitrageBot:
    """Production-ready arbitrage bot with all features"""
    
    def __init__(self, config_path: str = "config/config.json"):
        """Initialize the production bot"""
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        # Initialize components
        self.wallet = self._load_wallet()
        self.client = AsyncClient(
            self.config['rpc_endpoint'],
            commitment=Confirmed
        )
        
        # Tokens to monitor
        self.tokens = self._load_tokens()
        
        # Rate limiters for each API
        self.rate_limiters = {
            DEX.JUPITER: RateLimiter(calls_per_second=10, burst=20),
            DEX.RAYDIUM: RateLimiter(calls_per_second=5, burst=10),
            "rpc": RateLimiter(calls_per_second=40, burst=50)
        }
        
        # Price cache
        self.price_cache = PriceCache(ttl_seconds=3)
        
        # Database
        self.db = ArbitrageDatabase()
        
        # Trading parameters
        self.min_profit_usd = Decimal(str(self.config.get('min_profit_usd', 10.0)))
        self.max_position_size = Decimal(str(self.config.get('max_position_size', 5000.0)))
        self.max_price_impact = Decimal(str(self.config.get('max_price_impact', 0.01)))  # 1%
        self.min_price_difference = Decimal(str(self.config.get('min_price_difference', 0.007)))  # 0.7% default
        self.priority_fee = self.config.get('priority_fee_microlamports', 10000)
        
        # TEMPORARY: Lower min profit for testing
        if self.config.get('test_mode', True):
            self.min_profit_usd = Decimal('0.001')  # $0.001 minimum for testing
            logger.warning("TEST MODE: Minimum profit set to $0.001")
        
        # Risk management
        self.max_daily_loss = Decimal(str(self.config.get('max_daily_loss', 100.0)))
        self.daily_loss = Decimal('0')
        self.last_loss_reset = datetime.utcnow()
        
        # State
        self.running = False
        self.active_opportunities = {}
        self.execution_lock = asyncio.Lock()
        
        # Thread pool for CPU-intensive tasks
        self.executor = ThreadPoolExecutor(max_workers=4)
        
        # Initialize Jito client if available
        self.use_jito = self.config.get('use_jito_bundles', False) and JitoClient is not None
        self.jito_client = None
        
        if self.use_jito:
            try:
                jito_config = JitoConfig()
                self.jito_client = JitoClient(jito_config, self.client)
                logger.info("Jito bundle support enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize Jito client: {e}")
                self.use_jito = False
                self.jito_client = None
        
        logger.info(f"Production bot initialized. Wallet: {self.wallet.pubkey()}")
    
    def _load_wallet(self) -> Keypair:
        """Load wallet with proper security"""
        wallet_path = self.config.get('wallet_path', 'wallet.json')
        
        # Try environment variable first (more secure)
        if 'SOLANA_PRIVATE_KEY' in os.environ:
            secret_key = base58.b58decode(os.environ['SOLANA_PRIVATE_KEY'])
            return Keypair.from_bytes(secret_key)
        
        # Fall back to file
        if os.path.exists(wallet_path):
            with open(wallet_path, 'r') as f:
                wallet_data = json.load(f)
                # Handle both array format and object format
                if isinstance(wallet_data, list):
                    # Direct array of bytes
                    return Keypair.from_bytes(wallet_data)
                else:
                    # Object with secret_key field
                    secret_key = base58.b58decode(wallet_data['secret_key'])
                    return Keypair.from_bytes(secret_key)
        
        raise ValueError("No wallet found. Set SOLANA_PRIVATE_KEY or create wallet.json")
    
    def _load_tokens(self) -> List[Token]:
        """Load tokens from configuration"""
        tokens = []
        token_config = self.config.get('tokens', {})
        
        for symbol, info in token_config.items():
            tokens.append(Token(
                symbol=symbol,
                mint=info['mint'],
                decimals=info['decimals'],
                min_liquidity=info.get('min_liquidity', 10000.0)
            ))
        
        return tokens
    
    async def get_jupiter_price(self, token: Token) -> Optional[Tuple[Decimal, Decimal]]:
        """Get token price and liquidity from Jupiter"""
        cache_key = f"jupiter_{token.mint}"
        cached = self.price_cache.get(cache_key)
        if cached:
            return cached
        
        await self.rate_limiters[DEX.JUPITER].acquire()
        
        try:
            async with aiohttp.ClientSession() as session:
                # Get price for 1 token worth in USD
                amount = 10 ** token.decimals
                
                params = {
                    'inputMint': token.mint,
                    'outputMint': self.config['usdc_mint'],
                    'amount': amount,
                    'slippageBps': 50
                }
                
                async with session.get(
                    "https://quote-api.jup.ag/v6/quote",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    output_amount = Decimal(data['outAmount']) / Decimal(10 ** 6)  # USDC decimals
                    price = output_amount
                    
                    # Estimate available liquidity (simplified)
                    routes = data.get('routePlan', [])
                    total_liquidity = sum(
                        Decimal(route.get('outAmount', 0)) for route in routes
                    ) * Decimal('100')  # Rough estimate
                    
                    result = (price, total_liquidity)
                    self.price_cache.set(cache_key, result)
                    return result
                    
        except Exception as e:
            logger.error(f"Jupiter price error for {token.symbol}: {e}")
            return None
    
    async def get_raydium_price(self, token: Token) -> Optional[Tuple[Decimal, Decimal]]:
        """Get token price and liquidity from Raydium via DexScreener"""
        cache_key = f"raydium_{token.mint}"
        cached = self.price_cache.get(cache_key)
        if cached:
            return cached
        
        await self.rate_limiters[DEX.RAYDIUM].acquire()
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {'User-Agent': 'ArbitrageBot/2.0'}
                
                async with session.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{token.mint}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    pairs = data.get('pairs', [])
                    
                    # Find Raydium USDC pair with highest liquidity
                    raydium_pairs = [
                        p for p in pairs 
                        if p.get('dexId') == 'raydium' and 
                        p.get('quoteToken', {}).get('symbol') in ['USDC', 'USDT']
                    ]
                    
                    if not raydium_pairs:
                        return None
                    
                    # Sort by liquidity
                    best_pair = max(
                        raydium_pairs,
                        key=lambda p: float(p.get('liquidity', {}).get('usd', 0))
                    )
                    
                    price = Decimal(best_pair.get('priceUsd', 0))
                    liquidity = Decimal(best_pair.get('liquidity', {}).get('usd', 0))
                    
                    if price > 0 and liquidity > token.min_liquidity:
                        result = (price, liquidity)
                        self.price_cache.set(cache_key, result)
                        return result
                    
                    return None
                    
        except Exception as e:
            logger.error(f"Raydium price error for {token.symbol}: {e}")
            return None
    
    async def calculate_price_impact(
        self,
        token: Token,
        dex: DEX,
        size_usd: Decimal
    ) -> Decimal:
        """Estimate price impact for a given trade size"""
        # For small trades, use minimal impact
        if size_usd <= 100:
            return Decimal('0.0001')  # 0.01% for small trades
        elif size_usd <= 1000:
            return Decimal('0.0005')  # 0.05% for medium trades
        else:
            # Simplified model - in production, use actual DEX quotes
            base_impact = Decimal('0.001')  # 0.1% base
            return base_impact * (size_usd / Decimal('10000'))
    
    async def get_usdc_balance(self) -> float:
        """Get USDC balance for the wallet"""
        try:
            from solana.rpc import types
            
            # Create proper opts object
            opts = types.TokenAccountOpts(
                mint=Pubkey.from_string(self.config['usdc_mint'])
            )
            
            response = await self.client.get_token_accounts_by_owner_json_parsed(
                self.wallet.pubkey(),
                opts
            )
            
            if response.value:
                for account in response.value:
                    try:
                        parsed_info = account.account.data.parsed['info']
                        balance = parsed_info['tokenAmount']['uiAmount']
                        if balance and balance > 0:
                            return float(balance)
                    except Exception as e:
                        logger.error(f"Error parsing token account: {e}")
            return 0.0
        except Exception as e:
            logger.error(f"Error getting USDC balance: {e}")
            return 0.0
    
    async def get_orca_price(self, token: Token) -> Optional[Tuple[Decimal, Decimal]]:
        """Get token price from Orca via DexScreener"""
        cache_key = f"orca_{token.mint}"
        cached = self.price_cache.get(cache_key)
        if cached:
            return cached
        
        await self.rate_limiters[DEX.RAYDIUM].acquire()  # Use same limiter
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {'User-Agent': 'ArbitrageBot/2.0'}
                
                async with session.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{token.mint}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    pairs = data.get('pairs', [])
                    
                    # Find Orca USDC pair
                    orca_pairs = [
                        p for p in pairs 
                        if p.get('dexId') == 'orca' and 
                        p.get('quoteToken', {}).get('symbol') in ['USDC', 'USDT']
                    ]
                    
                    if not orca_pairs:
                        return None
                    
                    best_pair = max(
                        orca_pairs,
                        key=lambda p: float(p.get('liquidity', {}).get('usd', 0))
                    )
                    
                    price = Decimal(best_pair.get('priceUsd', 0))
                    liquidity = Decimal(best_pair.get('liquidity', {}).get('usd', 0))
                    
                    if price > 0 and liquidity > token.min_liquidity:
                        result = (price, liquidity)
                        self.price_cache.set(cache_key, result)
                        return result
                    
                    return None
                    
        except Exception as e:
            logger.error(f"Orca price error for {token.symbol}: {e}")
            return None
    
    async def get_meteora_price(self, token: Token) -> Optional[Tuple[Decimal, Decimal]]:
        """Get token price from Meteora via DexScreener"""
        cache_key = f"meteora_{token.mint}"
        cached = self.price_cache.get(cache_key)
        if cached:
            return cached
        
        await self.rate_limiters[DEX.RAYDIUM].acquire()
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {'User-Agent': 'ArbitrageBot/2.0'}
                
                async with session.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{token.mint}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status != 200:
                        return None
                    
                    data = await response.json()
                    pairs = data.get('pairs', [])
                    
                    # Find Meteora USDC pair
                    meteora_pairs = [
                        p for p in pairs 
                        if p.get('dexId') == 'meteora' and 
                        p.get('quoteToken', {}).get('symbol') in ['USDC', 'USDT']
                    ]
                    
                    if not meteora_pairs:
                        return None
                    
                    best_pair = max(
                        meteora_pairs,
                        key=lambda p: float(p.get('liquidity', {}).get('usd', 0))
                    )
                    
                    price = Decimal(best_pair.get('priceUsd', 0))
                    liquidity = Decimal(best_pair.get('liquidity', {}).get('usd', 0))
                    
                    if price > 0 and liquidity > token.min_liquidity:
                        result = (price, liquidity)
                        self.price_cache.set(cache_key, result)
                        return result
                    
                    return None
                    
        except Exception as e:
            logger.error(f"Meteora price error for {token.symbol}: {e}")
            return None
        """Get USDC balance for the wallet"""
        try:
            from solana.rpc import types
            
            # Create proper opts object
            opts = types.TokenAccountOpts(
                mint=Pubkey.from_string(self.config['usdc_mint'])
            )
            
            response = await self.client.get_token_accounts_by_owner_json_parsed(
                self.wallet.pubkey(),
                opts
            )
            
            if response.value:
                for account in response.value:
                    try:
                        parsed_info = account.account.data.parsed['info']
                        balance = parsed_info['tokenAmount']['uiAmount']
                        if balance and balance > 0:
                            return float(balance)
                    except Exception as e:
                        logger.error(f"Error parsing token account: {e}")
            return 0.0
        except Exception as e:
            logger.error(f"Error getting USDC balance: {e}")
            return 0.0
    
    async def find_arbitrage_opportunities(self) -> List[ArbitrageOpportunity]:
        """Find all profitable arbitrage opportunities"""
        opportunities = []
        
        # Get current USDC balance to limit position sizes
        usdc_balance = await self.get_usdc_balance()
        logger.debug(f"Current USDC balance: ${usdc_balance:.2f}")
        
        for token in self.tokens:
            try:
                # Get prices from all DEXs
                jupiter_data = await self.get_jupiter_price(token)
                raydium_data = await self.get_raydium_price(token)
                orca_data = await self.get_orca_price(token)
                meteora_data = await self.get_meteora_price(token)
                
                # Collect all valid prices
                all_prices = []
                if jupiter_data:
                    all_prices.append((DEX.JUPITER, jupiter_data[0], jupiter_data[1]))
                if raydium_data:
                    all_prices.append((DEX.RAYDIUM, raydium_data[0], raydium_data[1]))
                if orca_data:
                    all_prices.append((DEX.ORCA, orca_data[0], orca_data[1]))
                if meteora_data:
                    all_prices.append((DEX.METEORA, meteora_data[0], meteora_data[1]))
                
                # Need at least 2 DEXs to arbitrage
                if len(all_prices) < 2:
                    logger.debug(f"{token.symbol}: Not enough DEX prices ({len(all_prices)} DEXs)")
                    continue
                
                # Find best arbitrage opportunity across all DEX pairs
                best_opportunity = None
                best_diff_pct = Decimal('0')
                
                for i in range(len(all_prices)):
                    for j in range(i + 1, len(all_prices)):
                        dex1, price1, liquidity1 = all_prices[i]
                        dex2, price2, liquidity2 = all_prices[j]
                        
                        price_diff = abs(price1 - price2)
                        price_diff_pct = (price_diff / min(price1, price2)) * Decimal('100')
                        
                        if price_diff_pct > best_diff_pct and price_diff_pct >= self.min_price_difference * 100:
                            if price1 < price2:
                                buy_dex, sell_dex = dex1, dex2
                                buy_price, sell_price = price1, price2
                            else:
                                buy_dex, sell_dex = dex2, dex1
                                buy_price, sell_price = price2, price1
                            
                            available_liquidity = min(liquidity1, liquidity2)
                            best_diff_pct = price_diff_pct
                            best_opportunity = (buy_dex, sell_dex, buy_price, sell_price, available_liquidity, price_diff_pct)
                
                if not best_opportunity:
                    logger.debug(f"{token.symbol}: No profitable DEX pairs found")
                    continue
                
                buy_dex, sell_dex, buy_price, sell_price, available_liquidity, price_diff_pct = best_opportunity
                
                logger.info(f"{token.symbol}: Buy on {buy_dex.value} at ${buy_price:.8f}, Sell on {sell_dex.value} at ${sell_price:.8f} ({price_diff_pct:.2f}%)")
                
                # Calculate optimal position size
                max_size_by_balance = Decimal(str(usdc_balance)) * Decimal('0.5')  # Use max 50% of balance to avoid getting stuck
                max_size_by_config = self.max_position_size
                max_size_by_liquidity = available_liquidity * Decimal('0.1')  # Use max 10% of liquidity
                
                # For tokens with low liquidity estimates from Jupiter, use a minimum
                if token.symbol in ['BONK', 'WIF', 'POPCAT', 'MEME']:
                    max_size_by_liquidity = max(max_size_by_liquidity, Decimal('1000'))  # At least $1000
                
                max_size = min(max_size_by_balance, max_size_by_config, max_size_by_liquidity)
                
                logger.info(f"{token.symbol}: Max size - Balance: ${max_size_by_balance:.2f}, Config: ${max_size_by_config:.2f}, Liquidity: ${max_size_by_liquidity:.2f} -> Using: ${max_size:.2f}")
                
                # Dynamic trade sizes based on available balance
                if usdc_balance < 100:
                    trade_sizes = [10, 15, 20, 25, 30, 40, 50]
                elif usdc_balance < 500:
                    trade_sizes = [20, 50, 100, 150, 200, 300]
                else:
                    trade_sizes = [50, 100, 200, 500, 1000, 2000]
                
                # Calculate expected profit for different sizes
                for size in trade_sizes:
                    size_usd = Decimal(str(size))
                    if size_usd > max_size:
                        logger.debug(f"{token.symbol}: Size ${size_usd} exceeds max size ${max_size}")
                        break
                    
                    # Estimate price impact
                    buy_impact = await self.calculate_price_impact(token, buy_dex, size_usd)
                    sell_impact = await self.calculate_price_impact(token, sell_dex, size_usd)
                    total_impact = buy_impact + sell_impact
                    
                    # Skip if impact too high
                    if total_impact > self.max_price_impact:
                        logger.debug(f"{token.symbol}: Price impact too high ({total_impact:.4f} > {self.max_price_impact})")
                        continue
                    
                    # Calculate profit
                    effective_buy_price = buy_price * (Decimal('1') + buy_impact)
                    effective_sell_price = sell_price * (Decimal('1') - sell_impact)
                    
                    tokens = size_usd / effective_buy_price
                    revenue = tokens * effective_sell_price
                    
                    # Estimate fees
                    # Use realistic Jupiter fees
                    swap_fees = size_usd * Decimal('0.0025') * 2  # 0.25% each way = 0.5% total
                    # Realistic gas fees (increased for safety)
                    gas_fees = Decimal('0.00003') * 150  # 0.00003 SOL × $150 = $0.0045 per transaction × 2 = $0.009
                    
                    gross_profit = revenue - size_usd
                    net_profit = gross_profit - swap_fees - gas_fees
                    
                    # Additional profit validation
                    profit_margin = net_profit / size_usd * 100  # Profit as percentage of investment
                    
                    # Always log profit calculations for debugging
                    logger.info(f"{token.symbol}: Size ${size_usd}")
                    logger.info(f"  Buy price: ${buy_price:.8f}, Sell price: ${sell_price:.8f}")
                    logger.info(f"  Price diff: {price_diff_pct:.3f}%")
                    logger.info(f"  Gross profit: ${gross_profit:.6f}")
                    logger.info(f"  Swap fees: ${swap_fees:.6f}")
                    logger.info(f"  Gas fees: ${gas_fees:.6f}")
                    logger.info(f"  Net profit: ${net_profit:.6f}")
                    logger.info(f"  Profit margin: {profit_margin:.3f}%")
                    logger.info(f"  Min required: ${self.min_profit_usd}")
                    
                    # Extra validation: ensure profit margin is at least 1%
                    min_profit_margin = Decimal('1.0')  # 1% minimum profit margin
                    
                    if net_profit >= self.min_profit_usd and profit_margin >= min_profit_margin:
                        opportunity = ArbitrageOpportunity(
                            id=f"{token.symbol}_{int(time.time() * 1000000)}",
                            token=token,
                            buy_dex=buy_dex,
                            sell_dex=sell_dex,
                            buy_price=buy_price,
                            sell_price=sell_price,
                            size_usd=size_usd,
                            expected_profit=net_profit,
                            price_impact=total_impact,
                            timestamp=datetime.utcnow(),
                            expires_at=datetime.utcnow() + timedelta(seconds=10)
                        )
                        
                        opportunities.append(opportunity)
                        await self.db.save_opportunity(opportunity)
                        
                        logger.info(f"OPPORTUNITY FOUND: {token.symbol} - Size: ${size_usd}, Expected profit: ${net_profit:.2f} ({profit_margin:.2f}% margin)")
                        break  # Found profitable size, move to next token
                    else:
                        if net_profit < self.min_profit_usd:
                            logger.info(f"{token.symbol}: Not profitable enough. Net profit ${net_profit:.4f} < Required ${self.min_profit_usd}")
                        else:
                            logger.info(f"{token.symbol}: Profit margin too low. {profit_margin:.3f}% < Required {min_profit_margin}%")
                
            except Exception as e:
                logger.error(f"Error finding opportunities for {token.symbol}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        # Update metrics
        opportunity_gauge.set(len(opportunities))
        
        if opportunities:
            logger.info(f"Found {len(opportunities)} total opportunities")
        
        return opportunities
    
    async def execute_arbitrage(self, opportunity: ArbitrageOpportunity) -> TradeResult:
        """Execute an arbitrage opportunity with safety checks"""
        start_time = time.time()
        
        # Check if still valid
        if not opportunity.is_valid():
            return TradeResult(
                opportunity_id=opportunity.id,
                success=False,
                buy_tx=None,
                sell_tx=None,
                actual_profit=None,
                error="Opportunity expired",
                gas_used=Decimal('0'),
                execution_time=0
            )
        
        # Risk check
        if self.daily_loss >= self.max_daily_loss:
            return TradeResult(
                opportunity_id=opportunity.id,
                success=False,
                buy_tx=None,
                sell_tx=None,
                actual_profit=None,
                error="Daily loss limit reached",
                gas_used=Decimal('0'),
                execution_time=0
            )
        
        async with self.execution_lock:
            try:
                logger.info(f"Executing arbitrage: {opportunity.token.symbol}")
                logger.info(f"Buy on {opportunity.buy_dex.value} at {opportunity.buy_price}")
                logger.info(f"Sell on {opportunity.sell_dex.value} at {opportunity.sell_price}")
                logger.info(f"Expected profit: ${opportunity.expected_profit}")
                
                # Final profit validation before execution
                min_profit_margin = Decimal('1.0')  # 1% minimum
                expected_margin = (opportunity.expected_profit / opportunity.size_usd) * 100
                
                if expected_margin < min_profit_margin:
                    logger.warning(f"Skipping trade: profit margin {expected_margin:.2f}% below minimum {min_profit_margin}%")
                    raise Exception(f"Profit margin too low: {expected_margin:.2f}%")
                
                # Re-verify opportunity is still profitable before execution
                logger.info("Re-verifying prices before execution...")
                
                # Get fresh prices
                fresh_jupiter_data = await self.get_jupiter_price(opportunity.token)
                fresh_raydium_data = await self.get_raydium_price(opportunity.token)
                
                if not fresh_jupiter_data or not fresh_raydium_data:
                    raise Exception("Failed to get fresh prices for verification")
                
                fresh_jupiter_price, _ = fresh_jupiter_data
                fresh_raydium_price, _ = fresh_raydium_data
                
                # Determine current buy/sell prices
                if opportunity.buy_dex == DEX.JUPITER:
                    current_buy_price = fresh_jupiter_price
                    current_sell_price = fresh_raydium_price
                else:
                    current_buy_price = fresh_raydium_price
                    current_sell_price = fresh_jupiter_price
                
                # Calculate fresh profit
                fresh_price_diff = abs(current_sell_price - current_buy_price)
                fresh_price_diff_pct = (fresh_price_diff / current_buy_price) * Decimal('100')
                
                logger.info(f"Fresh prices - Buy: ${current_buy_price:.8f}, Sell: ${current_sell_price:.8f}, Diff: {fresh_price_diff_pct:.3f}%")
                
                # Abort if spread has narrowed too much
                min_required_spread = Decimal('1.2')  # 1.2% minimum to account for fees and slippage
                if fresh_price_diff_pct < min_required_spread:
                    raise Exception(f"Price spread too narrow: {fresh_price_diff_pct:.3f}% < {min_required_spread}% required")
                
                # Get wallet balance
                await self.rate_limiters["rpc"].acquire()
                balance_response = await self.client.get_balance(self.wallet.pubkey())
                sol_balance = balance_response.value / 1e9
                
                if sol_balance < 0.1:  # Need at least 0.1 SOL for fees
                    raise Exception("Insufficient SOL balance for fees")
                
                # Check USDC balance
                usdc_balance = await self.get_usdc_balance()
                logger.info(f"USDC Balance: ${usdc_balance:.2f}")
                
                if usdc_balance < float(opportunity.size_usd):
                    raise Exception(f"Insufficient USDC balance. Have ${usdc_balance:.2f}, need ${opportunity.size_usd}")
                
                # Use Jito if available and profitable enough
                min_profit_for_jito = Decimal(str(self.config.get('min_profit_for_jito', 50.0)))
                if self.use_jito and self.jito_client and float(opportunity.expected_profit) > float(min_profit_for_jito):
                    logger.info("Using Jito bundle for atomic execution")
                    
                    # Build both transactions
                    buy_amount = int(opportunity.size_usd * Decimal('1000000'))  # USDC has 6 decimals
                    
                    # Build buy transaction
                    if opportunity.buy_dex == DEX.JUPITER:
                        buy_tx = await TransactionBuilder.build_jupiter_swap(
                            self.client,
                            self.wallet,
                            self.config['usdc_mint'],
                            opportunity.token.mint,
                            buy_amount,
                            slippage_bps=100,
                            priority_fee=0  # No priority fee needed with Jito
                        )
                    else:
                        buy_tx = None
                    
                    if not buy_tx:
                        raise Exception("Failed to build buy transaction")
                    
                    # Build sell transaction
                    tokens_received = opportunity.size_usd / opportunity.buy_price
                    sell_amount = int(tokens_received * Decimal(10 ** opportunity.token.decimals))
                    
                    if opportunity.sell_dex == DEX.JUPITER:
                        sell_tx = await TransactionBuilder.build_jupiter_swap(
                            self.client,
                            self.wallet,
                            opportunity.token.mint,
                            self.config['usdc_mint'],
                            sell_amount,
                            slippage_bps=100,
                            priority_fee=0
                        )
                    else:
                        sell_tx = None
                    
                    if not sell_tx:
                        raise Exception("Failed to build sell transaction")
                    
                    # Calculate tip for Jito
                    expected_profit_lamports = int(float(opportunity.expected_profit) * 1e9 / 150)  # Assuming SOL = $150
                    tip_lamports = self.jito_client.calculate_optimal_tip(expected_profit_lamports)
                    
                    # Build bundle with tip in last transaction
                    bundle_txs = await self.jito_client.build_bundle_transactions(
                        [[buy_tx.message.instructions], [sell_tx.message.instructions]],
                        self.wallet,
                        tip_lamports
                    )
                    
                    # Send bundle
                    success, bundle_id, error = await self.jito_client.send_bundle(bundle_txs)
                    
                    if success:
                        # Wait for confirmation
                        confirmed = await self.jito_client.wait_for_bundle_confirmation(bundle_id, timeout=30)
                        
                        if confirmed:
                            gas_used = Decimal(str(tip_lamports / 1e9))  # Convert tip to SOL
                            actual_profit = opportunity.expected_profit - gas_used * 150  # SOL to USD
                            
                            result = TradeResult(
                                opportunity_id=opportunity.id,
                                success=True,
                                buy_tx=f"jito_bundle_{bundle_id}",
                                sell_tx=f"jito_bundle_{bundle_id}",
                                actual_profit=actual_profit,
                                error=None,
                                gas_used=gas_used,
                                execution_time=time.time() - start_time
                            )
                            
                            logger.info(f"Jito bundle executed successfully! Bundle ID: {bundle_id}")
                            logger.info(f"Actual profit: ${actual_profit}")
                        else:
                            raise Exception(f"Bundle not confirmed: {bundle_id}")
                    else:
                        raise Exception(f"Failed to send bundle: {error}")
                    
                else:
                    # Original sequential execution
                    logger.info("Using sequential transaction execution")
                    
                    # Execute buy transaction
                    buy_amount = int(opportunity.size_usd * Decimal('1000000'))  # USDC has 6 decimals

                    if opportunity.buy_dex == DEX.JUPITER:
                        buy_tx = await TransactionBuilder.build_jupiter_swap(
                            self.client,
                            self.wallet,
                            self.config['usdc_mint'],
                            opportunity.token.mint,
                            buy_amount,
                            slippage_bps=100,  # 1% slippage
                            priority_fee=self.priority_fee
                        )
                    else:
                        # Use Jupiter routing for Raydium
                        buy_tx = await TransactionBuilder.build_raydium_swap_via_jupiter(
                            self.client,
                            self.wallet,
                            self.config['usdc_mint'],
                            opportunity.token.mint,
                            buy_amount,
                            slippage_bps=100,
                            priority_fee=self.priority_fee
                        )
                    
                    if not buy_tx:
                        raise Exception("Failed to build buy transaction")
                    
                    # Sign and send buy transaction
                    try:
                        # For VersionedTransaction from Jupiter, we need to handle it carefully
                        from solders.transaction import VersionedTransaction as VT
                        from solders.keypair import Keypair as SoldersKeypair
                        
                        # Convert our keypair to solders format if needed
                        if hasattr(self.wallet, 'secret_key'):
                            signer_keypair = SoldersKeypair.from_bytes(self.wallet.secret_key)
                        else:
                            signer_keypair = self.wallet
                        
                        # Create a new VersionedTransaction with the signer
                        signed_tx = VT(buy_tx.message, [signer_keypair])
                        
                        await self.rate_limiters["rpc"].acquire()
                        logger.info(f"Sending buy transaction...")
                        
                        # Send the raw transaction
                        tx_bytes = bytes(signed_tx)
                        buy_result = await self.client.send_raw_transaction(tx_bytes)
                        buy_tx_id = str(buy_result.value)
                        
                    except Exception as e:
                        logger.error(f"Error signing/sending buy transaction: {e}")
                        raise
                    
                    logger.info(f"Buy transaction sent: {buy_tx_id}")
                    
                    # Wait for confirmation with proper error handling
                    max_retries = 15  # Reduced from 30 to speed up
                    for i in range(max_retries):
                        await asyncio.sleep(0.5)  # Reduced from 1 second
                        try:
                            status = await self.client.get_signature_statuses([buy_result.value])
                            if status.value[0] is not None:
                                if status.value[0].err:
                                    raise Exception(f"Buy transaction failed: {status.value[0].err}")
                                if status.value[0].confirmation_status in ["confirmed", "finalized"]:
                                    logger.info(f"Buy transaction confirmed after {(i+1)*0.5} seconds")
                                    break
                        except Exception as e:
                            if i == max_retries - 1:
                                raise Exception(f"Buy transaction confirmation timeout: {e}")
                            continue
                    
                    # TODO: Get actual token balance received
                    # For now, estimate based on expected price
                    tokens_received = opportunity.size_usd / opportunity.buy_price
                    sell_amount = int(tokens_received * Decimal(10 ** opportunity.token.decimals))
                    
                    # Execute sell transaction
                    logger.info(f"Building sell transaction for {sell_amount} tokens (raw amount)")
                    
                    if opportunity.sell_dex == DEX.JUPITER:
                        sell_tx = await TransactionBuilder.build_jupiter_swap(
                            self.client,
                            self.wallet,
                            opportunity.token.mint,
                            self.config['usdc_mint'],
                            sell_amount,
                            slippage_bps=200,  # Increased slippage to 2%
                            priority_fee=self.priority_fee
                        )
                    else:
                        # Use Jupiter routing for Raydium
                        sell_tx = await TransactionBuilder.build_raydium_swap_via_jupiter(
                            self.client,
                            self.wallet,
                            opportunity.token.mint,
                            self.config['usdc_mint'],
                            sell_amount,
                            slippage_bps=200,  # Increased slippage to 2%
                            priority_fee=self.priority_fee
                        )
                    
                    if not sell_tx:
                        raise Exception("Failed to build sell transaction")
                    
                    # Wait a bit before selling to ensure token balance is settled
                    await asyncio.sleep(1)  # Reduced from 3 seconds
                    
                    # Double check token balance before selling
                    logger.info("Verifying token balance before sell...")
                    opts = types.TokenAccountOpts(
                        mint=Pubkey.from_string(opportunity.token.mint)
                    )
                    
                    token_accounts = await self.client.get_token_accounts_by_owner_json_parsed(
                        self.wallet.pubkey(),
                        opts
                    )
                    
                    verified_balance = 0
                    if token_accounts.value:
                        for account in token_accounts.value:
                            try:
                                parsed_info = account.account.data.parsed['info']
                                balance = parsed_info['tokenAmount']['amount']
                                verified_balance = int(balance)
                                logger.info(f"Verified token balance: {verified_balance}")
                                break
                            except Exception as e:
                                logger.error(f"Error verifying balance: {e}")
                    
                    if verified_balance < sell_amount:
                        logger.warning(f"Adjusting sell amount from {sell_amount} to {verified_balance}")
                        sell_amount = verified_balance
                        
                        # Rebuild transaction with correct amount
                        if opportunity.sell_dex == DEX.JUPITER:
                            sell_tx = await TransactionBuilder.build_jupiter_swap(
                                self.client,
                                self.wallet,
                                opportunity.token.mint,
                                self.config['usdc_mint'],
                                sell_amount,
                                slippage_bps=200,
                                priority_fee=self.priority_fee
                            )
                        else:
                            sell_tx = await TransactionBuilder.build_raydium_swap_via_jupiter(
                                self.client,
                                self.wallet,
                                opportunity.token.mint,
                                self.config['usdc_mint'],
                                sell_amount,
                                slippage_bps=200,
                                priority_fee=self.priority_fee
                            )
                    
                    if not sell_tx:
                        raise Exception("Failed to build sell transaction")
                    
                    # Sign and send sell transaction using the same method as buy
                    try:
                        from solders.transaction import VersionedTransaction as VT
                        from solders.keypair import Keypair as SoldersKeypair
                        
                        # Convert our keypair to solders format if needed
                        if hasattr(self.wallet, 'secret_key'):
                            signer_keypair = SoldersKeypair.from_bytes(self.wallet.secret_key)
                        else:
                            signer_keypair = self.wallet
                        
                        # Create a new VersionedTransaction with the signer
                        signed_tx = VT(sell_tx.message, [signer_keypair])
                        
                        await self.rate_limiters["rpc"].acquire()
                        logger.info(f"Sending sell transaction with amount: {sell_amount}")
                        
                        # Send the raw transaction
                        tx_bytes = bytes(signed_tx)
                        sell_result = await self.client.send_raw_transaction(tx_bytes)
                        sell_tx_id = str(sell_result.value)
                        
                        logger.info(f"Sell transaction sent: {sell_tx_id}")
                        
                        # Wait for confirmation
                        await asyncio.sleep(2)
                        
                    except Exception as e:
                        logger.error(f"Error signing/sending sell transaction: {e}")
                        # Log more details about the error
                        if "Custom program error" in str(e):
                            logger.error("This usually means insufficient token balance or slippage")
                            logger.error(f"Attempted to sell {sell_amount} tokens")
                        raise
                    
                    logger.info(f"Sell transaction sent: {sell_tx_id}")
                    
                    # Wait for sell confirmation
                    await asyncio.sleep(5)
                    
                    # Get actual USDC balance after trades to calculate real profit
                    final_usdc_balance = await self.get_usdc_balance()
                    usdc_received = Decimal(str(final_usdc_balance)) - Decimal(str(usdc_balance)) + opportunity.size_usd
                    
                    # Calculate actual profit based on real results
                    gas_used = Decimal('0.00001') * 2  # Approximate gas for both transactions
                    actual_profit = usdc_received - opportunity.size_usd - (gas_used * 150)  # Convert gas to USD
                    
                    logger.info(f"Trade complete:")
                    logger.info(f"  Started with: ${opportunity.size_usd} USDC")
                    logger.info(f"  Received: ${usdc_received} USDC")
                    logger.info(f"  Gas cost: ${gas_used * 150:.4f}")
                    logger.info(f"  Actual profit/loss: ${actual_profit:.4f}")
                    
                    result = TradeResult(
                        opportunity_id=opportunity.id,
                        success=True,
                        buy_tx=buy_tx_id,
                        sell_tx=sell_tx_id,
                        actual_profit=actual_profit,
                        error=None,
                        gas_used=gas_used,
                        execution_time=time.time() - start_time
                    )
                    
                    if actual_profit < 0:
                        logger.warning(f"Trade resulted in loss of ${abs(actual_profit):.4f}")
                        self.daily_loss += abs(actual_profit)
                        
                        # Analyze why the trade failed
                        logger.warning("Trade analysis:")
                        logger.warning(f"  Expected profit: ${opportunity.expected_profit:.4f}")
                        logger.warning(f"  Actual profit: ${actual_profit:.4f}")
                        logger.warning(f"  Difference: ${opportunity.expected_profit - actual_profit:.4f}")
                        logger.warning(f"  Execution time: {result.execution_time:.1f} seconds")
                        
                        # Log current prices to see if market moved
                        current_jupiter = await self.get_jupiter_price(opportunity.token)
                        current_raydium = await self.get_raydium_price(opportunity.token)
                        if current_jupiter and current_raydium:
                            j_price, _ = current_jupiter
                            r_price, _ = current_raydium
                            logger.warning(f"  Current prices - Jupiter: ${j_price:.8f}, Raydium: ${r_price:.8f}")
                            logger.warning(f"  Original prices - Buy: ${opportunity.buy_price:.8f}, Sell: ${opportunity.sell_price:.8f}")
                
            except Exception as e:
                logger.error(f"Arbitrage execution failed: {e}")
                
                result = TradeResult(
                    opportunity_id=opportunity.id,
                    success=False,
                    buy_tx=None,
                    sell_tx=None,
                    actual_profit=None,
                    error=str(e),
                    gas_used=Decimal('0.005'),  # Failed tx still costs gas
                    execution_time=time.time() - start_time
                )
                
                # Update daily loss
                self.daily_loss += Decimal('10')  # Assume $10 loss on failed trade
            
            # Save trade result
            await self.db.save_trade(opportunity, result)
            
            return result
    
    async def check_and_rebalance_usdc(self, min_usdc_needed: float = 20.0) -> bool:
        """Check USDC balance and rebalance if needed"""
        try:
            usdc_balance = await self.get_usdc_balance()
            
            if usdc_balance < min_usdc_needed:
                logger.info(f"USDC balance ${usdc_balance:.2f} below minimum ${min_usdc_needed}")
                
                # Get all token balances
                for token in self.tokens:
                    opts = types.TokenAccountOpts(
                        mint=Pubkey.from_string(token.mint)
                    )
                    
                    token_accounts = await self.client.get_token_accounts_by_owner_json_parsed(
                        self.wallet.pubkey(),
                        opts
                    )
                    
                    if token_accounts.value:
                        for account in token_accounts.value:
                            try:
                                parsed_info = account.account.data.parsed['info']
                                balance = parsed_info['tokenAmount']['uiAmount']
                                if balance and balance > 0:
                                    # Get current price
                                    jupiter_data = await self.get_jupiter_price(token)
                                    if jupiter_data:
                                        price, _ = jupiter_data
                                        value_usd = float(balance) * float(price)
                                        
                                        if value_usd > 5:  # Only rebalance if worth more than $5
                                            logger.info(f"Found {balance} {token.symbol} worth ${value_usd:.2f}")
                                            
                                            # Sell half to USDC
                                            sell_amount = int(float(balance) * 0.5 * (10 ** token.decimals))
                                            
                                            sell_tx = await TransactionBuilder.build_jupiter_swap(
                                                self.client,
                                                self.wallet,
                                                token.mint,
                                                self.config['usdc_mint'],
                                                sell_amount,
                                                slippage_bps=100,
                                                priority_fee=self.priority_fee
                                            )
                                            
                                            if sell_tx:
                                                # Sign and send
                                                from solders.transaction import VersionedTransaction as VT
                                                signed_tx = VT(sell_tx.message, [self.wallet])
                                                tx_bytes = bytes(signed_tx)
                                                result = await self.client.send_raw_transaction(tx_bytes)
                                                
                                                logger.info(f"Rebalanced {token.symbol} to USDC: {result.value}")
                                                await asyncio.sleep(5)  # Wait for confirmation
                                                return True
                                                
                            except Exception as e:
                                logger.error(f"Error checking {token.symbol} balance: {e}")
                
            return usdc_balance >= min_usdc_needed
            
        except Exception as e:
            logger.error(f"Rebalancing error: {e}")
            return False
    
    async def health_check(self):
        """Periodic health check"""
        while self.running:
            try:
                # Check RPC connection
                await self.rate_limiters["rpc"].acquire()
                block_height = await self.client.get_block_height()
                
                # Check wallet balance
                balance_response = await self.client.get_balance(self.wallet.pubkey())
                sol_balance = balance_response.value / 1e9
                
                # Estimate USD value (simplified)
                usd_value = sol_balance * 150  # Assume $150/SOL
                balance_gauge.set(usd_value)
                
                # Reset daily loss counter if new day
                if datetime.utcnow().date() > self.last_loss_reset.date():
                    self.daily_loss = Decimal('0')
                    self.last_loss_reset = datetime.utcnow()
                
                # Clear expired cache entries
                self.price_cache.clear_expired()
                
                logger.info(f"Health check OK. Block: {block_height.value}, Balance: {sol_balance:.4f} SOL")
                
            except Exception as e:
                logger.error(f"Health check failed: {e}")
            
            await asyncio.sleep(60)  # Check every minute
    
    async def monitor_loop(self):
        """Main monitoring loop"""
        logger.info("Starting production arbitrage monitor...")
        
        # Check USDC balance at start
        usdc_balance = await self.get_usdc_balance()
        logger.info(f"Starting USDC balance: ${usdc_balance:.2f}")
        
        consecutive_errors = 0
        checks_count = 0
        
        while self.running:
            try:
                checks_count += 1
                logger.info(f"[Check #{checks_count}] Scanning for arbitrage opportunities...")
                
                # Find opportunities
                opportunities = await self.find_arbitrage_opportunities()
                
                # Check if we need to rebalance USDC before executing trades
                if len(opportunities) > 0:
                    usdc_balance = await self.get_usdc_balance()
                    if usdc_balance < 10:  # Below minimum trade size
                        logger.info("Low USDC balance, attempting to rebalance...")
                        rebalanced = await self.check_and_rebalance_usdc(min_usdc_needed=20.0)
                        if rebalanced:
                            logger.info("Rebalancing successful, continuing with trades")
                        else:
                            logger.warning("Could not rebalance, skipping this cycle")
                            await asyncio.sleep(30)
                            continue
                
                if opportunities:
                    logger.info(f"Found {len(opportunities)} opportunities")
                    
                    # Sort by profit
                    opportunities.sort(key=lambda x: x.expected_profit, reverse=True)
                    
                    # Log each opportunity
                    for i, opp in enumerate(opportunities):
                        logger.info(f"  Opportunity {i+1}: {opp.token.symbol}")
                        logger.info(f"    Buy on {opp.buy_dex.value} at ${opp.buy_price:.8f}")
                        logger.info(f"    Sell on {opp.sell_dex.value} at ${opp.sell_price:.8f}")
                        logger.info(f"    Size: ${opp.size_usd}, Expected profit: ${opp.expected_profit:.2f}")
                    
                    # Execute top opportunities in parallel (max 3)
                    tasks = []
                    for opp in opportunities[:1]:  # Changed from [:3] to [:1] to execute one at a time
                        if opp.expected_profit >= self.min_profit_usd:
                            logger.info(f"Executing arbitrage for {opp.token.symbol}...")
                            tasks.append(self.execute_arbitrage(opp))
                    
                    if tasks:
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        
                        for result in results:
                            if isinstance(result, Exception):
                                logger.error(f"Execution error: {result}")
                            elif isinstance(result, TradeResult) and result.success:
                                # If we successfully executed a trade, skip remaining opportunities
                                # to avoid overexposure
                                logger.info("Successfully executed trade, skipping remaining opportunities")
                else:
                    # Log token prices periodically
                    if checks_count % 10 == 0:  # Every 10 checks
                        logger.info("No opportunities found. Current token prices:")
                        for token in self.tokens[:3]:  # Show first 3 tokens
                            jupiter_data = await self.get_jupiter_price(token)
                            raydium_data = await self.get_raydium_price(token)
                            
                            if jupiter_data and raydium_data:
                                j_price, _ = jupiter_data
                                r_price, _ = raydium_data
                                diff_pct = abs(j_price - r_price) / min(j_price, r_price) * 100
                                logger.info(f"  {token.symbol}: Jupiter=${j_price:.8f}, Raydium=${r_price:.8f}, Diff={diff_pct:.2f}%")
                
                consecutive_errors = 0
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Monitor loop error: {e}")
                
                if consecutive_errors > 5:
                    logger.error("Too many consecutive errors, pausing...")
                    await asyncio.sleep(30)
                    consecutive_errors = 0
            
            # Dynamic sleep based on market activity
            sleep_time = self.config.get('check_interval', 5)
            if len(opportunities) == 0:
                sleep_time = min(sleep_time * 1.5, 30)  # Slow down gradually if no opportunities
            
            logger.debug(f"Sleeping for {sleep_time} seconds...")
            await asyncio.sleep(sleep_time)

    async def start(self):
        """Start the bot"""
        self.running = True

        # Initialize Jito client if enabled
        if self.jito_client:
            await self.jito_client.__aenter__()
        
        # Start prometheus metrics server - with error handling
        try:
            # Try different ports if 8000 is taken
            ports_to_try = [8000, 8001, 8002, 8003, 9090]
            metrics_started = False
            
            for port in ports_to_try:
                try:
                    prometheus_client.start_http_server(port)
                    logger.info(f"Metrics server started on port {port}")
                    metrics_started = True
                    break
                except OSError as e:
                    if "Address already in use" in str(e) or "access a socket" in str(e):
                        logger.warning(f"Port {port} is already in use, trying next port...")
                        continue
                    else:
                        raise
            
            if not metrics_started:
                logger.warning("Could not start metrics server - all ports in use. Continuing without metrics.")
        except Exception as e:
            logger.warning(f"Failed to start metrics server: {e}. Continuing without metrics.")
        
        # Create tasks
        tasks = [
            asyncio.create_task(self.monitor_loop()),
            asyncio.create_task(self.health_check())
        ]
        
        # Handle shutdown
        def signal_handler(sig, frame):
            logger.info("Shutdown signal received")
            self.running = False
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            self.running = False
            await self.client.close()
            if self.jito_client:
                await self.jito_client.__aexit__(None, None, None)
            self.executor.shutdown()
            logger.info("Bot stopped")

async def main():
    """Main entry point"""
    # Check for required files
    if not os.path.exists('config/config.json'):
        print("Creating default config.json...")
        os.makedirs('config', exist_ok=True)
        default_config = {
            "rpc_endpoint": "https://api.mainnet-beta.solana.com",
            "wallet_path": "wallet.json",
            "usdc_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "min_profit_usd": 10.0,
            "max_position_size": 5000.0,
            "max_price_impact": 0.01,
            "min_price_difference": 0.007,  # 0.7% minimum price difference
            "priority_fee_microlamports": 10000,
            "max_daily_loss": 100.0,
            "check_interval": 5,
            "use_jito_bundles": False,  # Disabled by default
            "tokens": {
                "BONK": {
                    "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
                    "decimals": 5,
                    "min_liquidity": 50000
                },
                "WIF": {
                    "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
                    "decimals": 6,
                    "min_liquidity": 100000
                },
                "JUP": {
                    "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
                    "decimals": 6,
                    "min_liquidity": 100000
                }
            }
        }
        
        with open('config/config.json', 'w') as f:
            json.dump(default_config, f, indent=2)
        
        print("Please configure config/config.json and add your wallet")
        return
    
    # Create necessary directories
    os.makedirs('logs', exist_ok=True)
    os.makedirs('data', exist_ok=True)
    
    # Start bot
    bot = ProductionArbitrageBot()
    await bot.start()

if __name__ == "__main__":
    print("=" * 60)
    print("PRODUCTION SOLANA ARBITRAGE BOT v2.0")
    print("=" * 60)
    asyncio.run(main())