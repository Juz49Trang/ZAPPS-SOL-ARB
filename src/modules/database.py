"""
Database module for storing trades and analytics - FIXED VERSION
"""

import sqlite3
import asyncio
import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from decimal import Decimal
import json
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class ArbitrageDatabase:
    """SQLite database for trade history and analytics"""
    
    def __init__(self, db_path: str = "data/arbitrage.db"):
        self.db_path = db_path
        # Create data directory if it doesn't exist
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize database tables"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Trades table
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
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT
                )
            """)
            
            # Opportunities table
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
                    executed BOOLEAN DEFAULT FALSE,
                    metadata TEXT
                )
            """)
            
            # Price history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_mint TEXT,
                    dex TEXT,
                    price REAL,
                    liquidity REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Performance metrics table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    date DATE PRIMARY KEY,
                    total_trades INTEGER,
                    successful_trades INTEGER,
                    total_volume REAL,
                    total_profit REAL,
                    total_gas REAL,
                    best_trade_profit REAL,
                    worst_trade_loss REAL
                )
            """)
            
            # Create indices
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_mint)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_success ON trades(success)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_timestamp ON opportunities(discovered_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_executed ON opportunities(executed)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_price_history_token ON price_history(token_mint, timestamp)")
            
            conn.commit()
            
        logger.info(f"Database initialized at {self.db_path}")
    
    @contextmanager
    def _get_connection(self):
        """Get database connection with proper error handling"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            if conn:
                conn.close()
    
    async def save_opportunity(self, opportunity: Dict[str, Any]):
        """Save discovered opportunity - FIXED"""
        def _save():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO opportunities 
                    (id, token_symbol, token_mint, buy_dex, sell_dex, buy_price, 
                     sell_price, size_usd, expected_profit, price_impact, 
                     discovered_at, expires_at, executed, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    opportunity['id'],
                    opportunity['token']['symbol'],
                    opportunity['token']['mint'],
                    opportunity['buy_dex'].value if hasattr(opportunity['buy_dex'], 'value') else opportunity['buy_dex'],
                    opportunity['sell_dex'].value if hasattr(opportunity['sell_dex'], 'value') else opportunity['sell_dex'],
                    float(opportunity['buy_price']),
                    float(opportunity['sell_price']),
                    float(opportunity['size_usd']),
                    float(opportunity['expected_profit']),
                    float(opportunity['price_impact']),
                    opportunity['timestamp'],
                    opportunity['expires_at'],
                    False,
                    json.dumps(opportunity.get('metadata', {}))
                ))
                conn.commit()
        
        await asyncio.get_event_loop().run_in_executor(None, _save)
    
    async def save_trade(self, opportunity: Dict[str, Any], result: Dict[str, Any]):
        """Save executed trade - FIXED"""
        def _save():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO trades 
                    (id, opportunity_id, token_symbol, token_mint, buy_dex, sell_dex,
                     buy_price, sell_price, size_usd, expected_profit, actual_profit,
                     buy_tx, sell_tx, success, error, gas_used, execution_time, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"trade_{int(datetime.now().timestamp() * 1000000)}",
                    result.opportunity_id if hasattr(result, 'opportunity_id') else result['opportunity_id'],
                    opportunity['token']['symbol'],
                    opportunity['token']['mint'],
                    opportunity['buy_dex'].value if hasattr(opportunity['buy_dex'], 'value') else opportunity['buy_dex'],
                    opportunity['sell_dex'].value if hasattr(opportunity['sell_dex'], 'value') else opportunity['sell_dex'],
                    float(opportunity['buy_price']),
                    float(opportunity['sell_price']),
                    float(opportunity['size_usd']),
                    float(opportunity['expected_profit']),
                    float(result.actual_profit if hasattr(result, 'actual_profit') else result.get('actual_profit', 0)),
                    result.buy_tx if hasattr(result, 'buy_tx') else result.get('buy_tx'),
                    result.sell_tx if hasattr(result, 'sell_tx') else result.get('sell_tx'),
                    result.success if hasattr(result, 'success') else result['success'],
                    result.error if hasattr(result, 'error') else result.get('error'),
                    float(result.gas_used if hasattr(result, 'gas_used') else result.get('gas_used', 0)),
                    float(result.execution_time if hasattr(result, 'execution_time') else result.get('execution_time', 0)),
                    json.dumps({})
                ))
                
                # Mark opportunity as executed
                cursor.execute(
                    "UPDATE opportunities SET executed = TRUE WHERE id = ?",
                    (opportunity['id'],)
                )
                
                # Update daily metrics
                self._update_daily_metrics(cursor, opportunity, result)
                
                conn.commit()
        
        await asyncio.get_event_loop().run_in_executor(None, _save)
    
    def _update_daily_metrics(self, cursor, opportunity, result):
        """Update daily performance metrics"""
        date = datetime.now().date()
        
        # Get current metrics
        cursor.execute(
            "SELECT * FROM daily_metrics WHERE date = ?",
            (date,)
        )
        row = cursor.fetchone()
        
        actual_profit = float(result.actual_profit if hasattr(result, 'actual_profit') else result.get('actual_profit', 0))
        success = result.success if hasattr(result, 'success') else result['success']
        
        if row:
            # Update existing
            cursor.execute("""
                UPDATE daily_metrics SET
                    total_trades = total_trades + 1,
                    successful_trades = successful_trades + ?,
                    total_volume = total_volume + ?,
                    total_profit = total_profit + ?,
                    total_gas = total_gas + ?,
                    best_trade_profit = MAX(best_trade_profit, ?),
                    worst_trade_loss = MIN(worst_trade_loss, ?)
                WHERE date = ?
            """, (
                1 if success else 0,
                float(opportunity['size_usd']),
                actual_profit,
                float(result.gas_used if hasattr(result, 'gas_used') else result.get('gas_used', 0)),
                actual_profit,
                actual_profit,
                date
            ))
        else:
            # Insert new
            cursor.execute("""
                INSERT INTO daily_metrics 
                (date, total_trades, successful_trades, total_volume, 
                 total_profit, total_gas, best_trade_profit, worst_trade_loss)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date,
                1,
                1 if success else 0,
                float(opportunity['size_usd']),
                actual_profit,
                float(result.gas_used if hasattr(result, 'gas_used') else result.get('gas_used', 0)),
                actual_profit,
                actual_profit
            ))
    
    async def save_price(self, token_mint: str, dex: str, price: Decimal, liquidity: Decimal):
        """Save price history"""
        def _save():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO price_history (token_mint, dex, price, liquidity)
                    VALUES (?, ?, ?, ?)
                """, (token_mint, dex, float(price), float(liquidity)))
                conn.commit()
        
        await asyncio.get_event_loop().run_in_executor(None, _save)
    
    async def get_recent_trades(self, limit: int = 100) -> List[Dict]:
        """Get recent trades"""
        def _get():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM trades 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (limit,))
                
                return [dict(row) for row in cursor.fetchall()]
        
        return await asyncio.get_event_loop().run_in_executor(None, _get)
    
    async def get_daily_metrics(self, days: int = 30) -> List[Dict]:
        """Get daily performance metrics"""
        def _get():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM daily_metrics 
                    WHERE date >= date('now', '-' || ? || ' days')
                    ORDER BY date DESC
                """, (days,))
                
                return [dict(row) for row in cursor.fetchall()]
        
        return await asyncio.get_event_loop().run_in_executor(None, _get)
    
    async def get_token_stats(self, token_mint: str) -> Dict:
        """Get statistics for a specific token"""
        def _get():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Get trade stats
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_trades,
                        SUM(CASE WHEN success THEN 1 ELSE 0 END) as successful_trades,
                        SUM(actual_profit) as total_profit,
                        AVG(actual_profit) as avg_profit,
                        MAX(actual_profit) as best_profit,
                        SUM(size_usd) as total_volume
                    FROM trades
                    WHERE token_mint = ?
                """, (token_mint,))
                
                stats = dict(cursor.fetchone())
                
                # Get opportunity stats
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_opportunities,
                        SUM(CASE WHEN executed THEN 1 ELSE 0 END) as executed_opportunities
                    FROM opportunities
                    WHERE token_mint = ?
                """, (token_mint,))
                
                opp_stats = dict(cursor.fetchone())
                stats.update(opp_stats)
                
                return stats
        
        return await asyncio.get_event_loop().run_in_executor(None, _get)
    
    async def cleanup_old_data(self, days: int = 30):
        """Clean up old data"""
        def _cleanup():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Delete old price history
                cursor.execute("""
                    DELETE FROM price_history 
                    WHERE timestamp < datetime('now', '-' || ? || ' days')
                """, (days,))
                
                # Delete old unexecuted opportunities
                cursor.execute("""
                    DELETE FROM opportunities 
                    WHERE executed = FALSE 
                    AND discovered_at < datetime('now', '-1 day')
                """)
                
                conn.commit()
                
                # Vacuum to reclaim space
                cursor.execute("VACUUM")
                
                return cursor.rowcount
        
        deleted = await asyncio.get_event_loop().run_in_executor(None, _cleanup)
        logger.info(f"Cleaned up {deleted} old records")
        return deleted