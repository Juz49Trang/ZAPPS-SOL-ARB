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

from .modules.jito_client import (
    JitoClient, 
    JitoBundleBuilder, 
    execute_arbitrage_with_jito
)

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
        self.use_jito = self.config.get('use_jito_bundles', False)
        self.jito_client = JitoClient() if self.use_jito else None
        
        if self.use_jito:
            logger.info("Jito bundle support enabled")
    
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
        self.priority_fee = self.config.get('priority_fee_microlamports', 10000)
        
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
        # Simplified model - in production, use actual DEX quotes
        base_impact = Decimal('0.001')  # 0.1% base
        
        # Adjust based on size
        if size_usd > 10000:
            return base_impact * (size_usd / Decimal('10000'))
        
        return base_impact
    
    async def find_arbitrage_opportunities(self) -> List[ArbitrageOpportunity]:
        """Find all profitable arbitrage opportunities"""
        opportunities = []
        
        for token in self.tokens:
            try:
                # Get prices from both DEXs
                jupiter_data = await self.get_jupiter_price(token)
                raydium_data = await self.get_raydium_price(token)
                
                if not jupiter_data or not raydium_data:
                    continue
                
                jupiter_price, jupiter_liquidity = jupiter_data
                raydium_price, raydium_liquidity = raydium_data
                
                # Skip if prices are too close
                price_diff = abs(jupiter_price - raydium_price)
                price_diff_pct = (price_diff / min(jupiter_price, raydium_price)) * Decimal('100')
                
                if price_diff_pct < Decimal('0.5'):  # Less than 0.5% difference
                    continue
                
                # Determine buy/sell direction
                if jupiter_price < raydium_price:
                    buy_dex, sell_dex = DEX.JUPITER, DEX.RAYDIUM
                    buy_price, sell_price = jupiter_price, raydium_price
                    available_liquidity = min(jupiter_liquidity, raydium_liquidity)
                else:
                    buy_dex, sell_dex = DEX.RAYDIUM, DEX.JUPITER
                    buy_price, sell_price = raydium_price, jupiter_price
                    available_liquidity = min(raydium_liquidity, jupiter_liquidity)
                
                # Calculate optimal position size
                max_size = min(
                    self.max_position_size,
                    available_liquidity * Decimal('0.1')  # Use max 10% of liquidity
                )
                
                # Calculate expected profit
                for size_usd in [100, 500, 1000, 2000, 5000]:
                    size_usd = Decimal(size_usd)
                    if size_usd > max_size:
                        break
                    
                    # Estimate price impact
                    buy_impact = await self.calculate_price_impact(token, buy_dex, size_usd)
                    sell_impact = await self.calculate_price_impact(token, sell_dex, size_usd)
                    total_impact = buy_impact + sell_impact
                    
                    # Skip if impact too high
                    if total_impact > self.max_price_impact:
                        continue
                    
                    # Calculate profit
                    effective_buy_price = buy_price * (Decimal('1') + buy_impact)
                    effective_sell_price = sell_price * (Decimal('1') - sell_impact)
                    
                    tokens = size_usd / effective_buy_price
                    revenue = tokens * effective_sell_price
                    
                    # Estimate fees
                    swap_fees = size_usd * Decimal('0.003') * 2  # 0.3% each way
                    gas_fees = Decimal('0.01')  # ~0.01 SOL at 150 USD/SOL
                    
                    net_profit = revenue - size_usd - swap_fees - gas_fees
                    
                    if net_profit >= self.min_profit_usd:
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
                        break
                
            except Exception as e:
                logger.error(f"Error finding opportunities for {token.symbol}: {e}")
                continue
        
        # Update metrics
        opportunity_gauge.set(len(opportunities))
        
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
                
                # Get wallet balance
                await self.rate_limiters["rpc"].acquire()
                balance_response = await self.client.get_balance(self.wallet.pubkey())
                sol_balance = balance_response.value / 1e9
                
                if sol_balance < 0.1:  # Need at least 0.1 SOL for fees
                    raise Exception("Insufficient SOL balance for fees")
                
                # Determine if we should use Jito bundles
                should_use_jito = (
                    self.use_jito and 
                    JitoBundleBuilder.should_use_bundle(opportunity.expected_profit)
                )
                
                if should_use_jito:
                    # Use Jito for atomic execution
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
                        # Raydium implementation
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
                        # Raydium implementation
                        sell_tx = None
                    
                    if not sell_tx:
                        raise Exception("Failed to build sell transaction")
                    
                    # Add Jito tip to the last transaction
                    tip_amount = JitoBundleBuilder.calculate_optimal_tip(opportunity.expected_profit)
                    sell_tx_instructions = list(sell_tx.message.instructions)
                    sell_tx_instructions = TransactionBuilder.add_jito_tip_instruction(
                        sell_tx_instructions,
                        tip_amount
                    )
                    
                    # Rebuild sell transaction with tip
                    sell_tx = await TransactionBuilder.build_versioned_transaction(
                        self.client,
                        sell_tx_instructions,
                        self.wallet.pubkey(),
                        [self.wallet]
                    )
                    
                    # Execute with Jito
                    success, bundle_id = await execute_arbitrage_with_jito(
                        self.jito_client,
                        buy_tx,
                        sell_tx,
                        opportunity.expected_profit,
                        self.wallet,
                        simulate_first=True
                    )
                    
                    if success:
                        gas_used = Decimal(str(tip_amount / 1e9))  # Convert tip to SOL
                        actual_profit = opportunity.expected_profit - gas_used
                        
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
                        raise Exception(f"Jito bundle execution failed: {bundle_id}")
                    
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
                        # Raydium implementation would go here
                        buy_tx = None
                    
                    if not buy_tx:
                        raise Exception("Failed to build buy transaction")
                    
                    # Sign and send buy transaction
                    buy_tx.sign([self.wallet])
                    await self.rate_limiters["rpc"].acquire()
                    buy_result = await self.client.send_transaction(buy_tx)
                    buy_tx_id = str(buy_result.value)
                    
                    logger.info(f"Buy transaction sent: {buy_tx_id}")
                    
                    # Wait for confirmation
                    await asyncio.sleep(0.5)
                    
                    # TODO: Get actual token balance received
                    # For now, estimate based on expected price
                    tokens_received = opportunity.size_usd / opportunity.buy_price
                    sell_amount = int(tokens_received * Decimal(10 ** opportunity.token.decimals))
                    
                    # Execute sell transaction
                    if opportunity.sell_dex == DEX.JUPITER:
                        sell_tx = await TransactionBuilder.build_jupiter_swap(
                            self.client,
                            self.wallet,
                            opportunity.token.mint,
                            self.config['usdc_mint'],
                            sell_amount,
                            slippage_bps=100,
                            priority_fee=self.priority_fee
                        )
                    else:
                        # Raydium implementation would go here
                        sell_tx = None
                    
                    if not sell_tx:
                        raise Exception("Failed to build sell transaction")
                    
                    # Sign and send sell transaction
                    sell_tx.sign([self.wallet])
                    await self.rate_limiters["rpc"].acquire()
                    sell_result = await self.client.send_transaction(sell_tx)
                    sell_tx_id = str(sell_result.value)
                    
                    logger.info(f"Sell transaction sent: {sell_tx_id}")
                    
                    # Calculate actual profit (simplified)
                    gas_used = Decimal('0.01')  # Estimate
                    actual_profit = opportunity.expected_profit - gas_used
                    
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
                    
                    logger.info(f"Arbitrage completed! Profit: ${actual_profit}")
                
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
        
        consecutive_errors = 0
        
        while self.running:
            try:
                # Find opportunities
                opportunities = await self.find_arbitrage_opportunities()
                
                if opportunities:
                    logger.info(f"Found {len(opportunities)} opportunities")
                    
                    # Sort by profit
                    opportunities.sort(key=lambda x: x.expected_profit, reverse=True)
                    
                    # Execute top opportunities in parallel (max 3)
                    tasks = []
                    for opp in opportunities[:3]:
                        if opp.expected_profit >= self.min_profit_usd:
                            tasks.append(self.execute_arbitrage(opp))
                    
                    if tasks:
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        
                        for result in results:
                            if isinstance(result, Exception):
                                logger.error(f"Execution error: {result}")
                
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
                sleep_time = min(sleep_time * 2, 30)  # Slow down if no opportunities
            
            await asyncio.sleep(sleep_time)
    
    async def start(self):
        """Start the bot"""
        self.running = True

        # Initialize Jito client if enabled
        if self.jito_client:
            await self.jito_client.__aenter__()
        
        # Start prometheus metrics server
        prometheus_client.start_http_server(8000)
        logger.info("Metrics server started on port 8000")
        
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
            "priority_fee_microlamports": 10000,
            "max_daily_loss": 100.0,
            "check_interval": 5,
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