# ===== tests/test_dex_clients.py =====
"""Tests for DEX client modules"""

import pytest
import asyncio
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch

from src.modules.dex_clients import (
    JupiterClient,
    RaydiumClient,
    DexScreenerClient,
    UnifiedDEXClient,
    QuoteResponse
)
from src.constants import USDC_MINT, WRAPPED_SOL_MINT

class TestJupiterClient:
    """Test Jupiter client"""
    
    @pytest.fixture
    def jupiter_client(self):
        return JupiterClient()
    
    @pytest.mark.asyncio
    async def test_get_quote_success(self, jupiter_client):
        """Test successful quote retrieval"""
        mock_response = {
            'inputMint': WRAPPED_SOL_MINT,
            'outputMint': USDC_MINT,
            'inAmount': '1000000000',
            'outAmount': '150000000',  # 150 USDC
            'priceImpactPct': '0.1',
            'routePlan': [
                {
                    'swapInfo': {
                        'label': 'Orca',
                        'inputMint': WRAPPED_SOL_MINT,
                        'outputMint': USDC_MINT
                    }
                }
            ]
        }
        
        with patch.object(jupiter_client, '_request', return_value=mock_response):
            quote = await jupiter_client.get_quote(
                WRAPPED_SOL_MINT,
                USDC_MINT,
                1000000000
            )
            
            assert quote is not None
            assert quote.output_amount == 150000000
            assert quote.price_impact == Decimal('0.001')
            assert 'Orca' in quote.route
    
    @pytest.mark.asyncio
    async def test_get_quote_failure(self, jupiter_client):
        """Test quote retrieval failure"""
        with patch.object(jupiter_client, '_request', return_value=None):
            quote = await jupiter_client.get_quote(
                WRAPPED_SOL_MINT,
                USDC_MINT,
                1000000000
            )
            
            assert quote is None

class TestDexScreenerClient:
    """Test DexScreener client"""
    
    @pytest.fixture
    def dexscreener_client(self):
        return DexScreenerClient()
    
    @pytest.mark.asyncio
    async def test_get_token_prices_by_dex(self, dexscreener_client):
        """Test getting token prices from multiple DEXs"""
        mock_response = {
            'pairs': [
                {
                    'dexId': 'raydium',
                    'priceUsd': '150.5',
                    'liquidity': {'usd': '1000000'}
                },
                {
                    'dexId': 'orca',
                    'priceUsd': '150.3',
                    'liquidity': {'usd': '500000'}
                }
            ]
        }
        
        with patch.object(dexscreener_client, '_request', return_value=mock_response):
            prices = await dexscreener_client.get_token_prices_by_dex(WRAPPED_SOL_MINT)
            
            assert 'raydium' in prices
            assert prices['raydium'][0] == Decimal('150.5')
            assert prices['raydium'][1] == Decimal('1000000')

class TestUnifiedDEXClient:
    """Test unified DEX client"""
    
    @pytest.mark.asyncio
    async def test_get_best_quote(self):
        """Test getting best quote from multiple DEXs"""
        # Create mock clients
        mock_jupiter = Mock()
        mock_raydium = Mock()
        
        # Set up mock quotes
        jupiter_quote = QuoteResponse(
            input_mint=WRAPPED_SOL_MINT,
            output_mint=USDC_MINT,
            input_amount=1000000000,
            output_amount=150000000,
            price=Decimal('150'),
            price_impact=Decimal('0.001'),
            fee=Decimal('0.003'),
            route=['Jupiter'],
            raw_response={}
        )
        
        raydium_quote = QuoteResponse(
            input_mint=WRAPPED_SOL_MINT,
            output_mint=USDC_MINT,
            input_amount=1000000000,
            output_amount=151000000,  # Better price
            price=Decimal('151'),
            price_impact=Decimal('0.002'),
            fee=Decimal('0.0025'),
            route=['Raydium'],
            raw_response={}
        )
        
        mock_jupiter.get_quote = AsyncMock(return_value=jupiter_quote)
        mock_raydium.get_quote = AsyncMock(return_value=raydium_quote)
        
        unified_client = UnifiedDEXClient()
        unified_client.clients = {
            'jupiter': mock_jupiter,
            'raydium': mock_raydium
        }
        
        best_dex, best_quote = await unified_client.get_best_quote(
            WRAPPED_SOL_MINT,
            USDC_MINT,
            1000000000
        )
        
        assert best_dex == 'raydium'
        assert best_quote.output_amount == 151000000