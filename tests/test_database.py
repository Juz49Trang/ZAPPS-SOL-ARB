
# ===== tests/test_database.py =====
"""Tests for database module"""

import pytest
import os
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal

from src.modules.database import ArbitrageDatabase

class TestArbitrageDatabase:
    """Test database operations"""
    
    @pytest.fixture
    def test_db(self):
        """Create temporary test database"""
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        db = ArbitrageDatabase(db_path)
        yield db
        
        # Cleanup
        os.unlink(db_path)
    
    @pytest.mark.asyncio
    async def test_save_opportunity(self, test_db):
        """Test saving opportunity"""
        opportunity = {
            'id': 'test_opp_001',
            'token': {
                'symbol': 'TEST',
                'mint': 'TestMint123',
                'decimals': 9
            },
            'buy_dex': 'jupiter',
            'sell_dex': 'raydium',
            'buy_price': Decimal('1.00'),
            'sell_price': Decimal('1.05'),
            'size_usd': Decimal('1000'),
            'expected_profit': Decimal('45'),
            'price_impact': Decimal('0.005'),
            'timestamp': datetime.utcnow(),
            'expires_at': datetime.utcnow() + timedelta(seconds=10),
            'metadata': {'test': True}
        }
        
        await test_db.save_opportunity(opportunity)
        
        # Verify it was saved
        with test_db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM opportunities WHERE id = ?", ('test_opp_001',))
            row = cursor.fetchone()
            
            assert row is not None
            assert row['token_symbol'] == 'TEST'
            assert row['expected_profit'] == 45.0
    
    @pytest.mark.asyncio
    async def test_save_trade(self, test_db):
        """Test saving trade"""
        trade = {
            'id': 'test_trade_001',
            'opportunity_id': 'test_opp_001',
            'token_symbol': 'TEST',
            'token_mint': 'TestMint123',
            'buy_dex': 'jupiter',
            'sell_dex': 'raydium',
            'buy_price': Decimal('1.00'),
            'sell_price': Decimal('1.05'),
            'size_usd': Decimal('1000'),
            'expected_profit': Decimal('45'),
            'actual_profit': Decimal('42'),
            'buy_tx': 'buy_sig_123',
            'sell_tx': 'sell_sig_456',
            'success': True,
            'gas_used': Decimal('0.01'),
            'execution_time': 2.5
        }
        
        await test_db.save_trade(trade)
        
        # Verify it was saved
        trades = await test_db.get_recent_trades(limit=1)
        assert len(trades) == 1
        assert trades[0]['id'] == 'test_trade_001'
        assert trades[0]['actual_profit'] == 42.0
    
    @pytest.mark.asyncio
    async def test_get_token_stats(self, test_db):
        """Test getting token statistics"""
        # Save some test data
        for i in range(5):
            trade = {
                'id': f'trade_{i}',
                'opportunity_id': f'opp_{i}',
                'token_symbol': 'TEST',
                'token_mint': 'TestMint123',
                'buy_dex': 'jupiter',
                'sell_dex': 'raydium',
                'buy_price': Decimal('1.00'),
                'sell_price': Decimal('1.05'),
                'size_usd': Decimal('1000'),
                'expected_profit': Decimal('45'),
                'actual_profit': Decimal('40') + Decimal(i),
                'success': True,
                'gas_used': Decimal('0.01'),
                'execution_time': 2.0
            }
            await test_db.save_trade(trade)
        
        # Get stats
        stats = await test_db.get_token_stats('TestMint123')
        
        assert stats['total_trades'] == 5
        assert stats['successful_trades'] == 5
        assert stats['total_profit'] == 210.0  # 40+41+42+43+44