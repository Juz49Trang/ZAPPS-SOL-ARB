"""
Constants and addresses for the Solana Arbitrage Bot
"""

from decimal import Decimal
from typing import Dict, List

# ===== NETWORK CONSTANTS =====
MAINNET_RPC = "https://api.mainnet-beta.solana.com"
DEVNET_RPC = "https://api.devnet.solana.com"

# Commitment levels
COMMITMENT_PROCESSED = "processed"
COMMITMENT_CONFIRMED = "confirmed"
COMMITMENT_FINALIZED = "finalized"

# ===== TOKEN ADDRESSES =====
# System Program
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"

# Token Program
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Associated Token Program
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"

# Native SOL
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

# Stable coins
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
USDC_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"

# ===== DEX PROGRAM IDS =====
DEX_PROGRAMS = {
    "jupiter_v6": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "raydium_v4": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "raydium_clmm": "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    "orca_whirlpool": "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "meteora_dlmm": "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
    "lifinity_v2": "2wT8Yq49kHgDzXuPxZSaeLaH1qbmGXtEyPy64bL7aD3c"
}

# ===== API ENDPOINTS =====
API_ENDPOINTS = {
    "jupiter_quote": "https://quote-api.jup.ag/v6/quote",
    "jupiter_swap": "https://quote-api.jup.ag/v6/swap",
    "jupiter_price": "https://price.jup.ag/v4/price",
    "dexscreener": "https://api.dexscreener.com/latest/dex",
    "birdeye": "https://public-api.birdeye.so",
    "helius": "https://api.helius.xyz/v0",
    "raydium_api": "https://api.raydium.io/v2"
}

# ===== POPULAR TOKENS =====
POPULAR_TOKENS = {
    "SOL": {
        "mint": WRAPPED_SOL_MINT,
        "decimals": 9,
        "coingecko_id": "solana"
    },
    "USDC": {
        "mint": USDC_MINT,
        "decimals": 6,
        "coingecko_id": "usd-coin"
    },
    "USDT": {
        "mint": USDT_MINT,
        "decimals": 6,
        "coingecko_id": "tether"
    },
    "BONK": {
        "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "decimals": 5,
        "coingecko_id": "bonk"
    },
    "WIF": {
        "mint": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "decimals": 6,
        "coingecko_id": "dogwifcoin"
    },
    "JTO": {
        "mint": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
        "decimals": 9,
        "coingecko_id": "jito-governance-token"
    },
    "JUP": {
        "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        "decimals": 6,
        "coingecko_id": "jupiter-exchange-solana"
    },
    "PYTH": {
        "mint": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
        "decimals": 6,
        "coingecko_id": "pyth-network"
    },
    "RAY": {
        "mint": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R",
        "decimals": 6,
        "coingecko_id": "raydium"
    },
    "ORCA": {
        "mint": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
        "decimals": 6,
        "coingecko_id": "orca"
    }
}

# ===== TRANSACTION CONSTANTS =====
# Compute units
DEFAULT_COMPUTE_UNITS = 200_000
MAX_COMPUTE_UNITS = 1_400_000

# Priority fees (in microlamports per compute unit)
PRIORITY_FEE_LEVELS = {
    "none": 0,
    "low": 1_000,      # 0.001 lamports per CU
    "medium": 10_000,   # 0.01 lamports per CU
    "high": 50_000,     # 0.05 lamports per CU
    "ultra": 100_000,   # 0.1 lamports per CU
    "max": 1_000_000    # 1 lamport per CU
}

# Transaction size limits
MAX_TRANSACTION_SIZE = 1232  # bytes
MAX_ACCOUNTS_PER_TRANSACTION = 64

# ===== TRADING CONSTANTS =====
# Decimals
LAMPORTS_PER_SOL = 10 ** 9
USDC_DECIMALS = 6

# Fee percentages (as Decimal)
DEFAULT_SWAP_FEE_BPS = 30  # 0.3%
DEFAULT_SLIPPAGE_BPS = 100  # 1%

# Position limits
MIN_POSITION_SIZE_USD = Decimal("10")
MAX_POSITION_SIZE_USD = Decimal("10000")

# Price impact thresholds
WARNING_PRICE_IMPACT = Decimal("0.01")  # 1%
MAX_PRICE_IMPACT = Decimal("0.05")      # 5%

# Timing
QUOTE_VALIDITY_SECONDS = 10
TRANSACTION_TIMEOUT_SECONDS = 30
CONFIRMATION_TIMEOUT_SECONDS = 60

# ===== JITO CONSTANTS =====
JITO_BLOCK_ENGINE_URL = "https://mainnet.block-engine.jito.wtf/api/v1"
JITO_TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT"
]

# ===== ERROR MESSAGES =====
ERROR_MESSAGES = {
    "INSUFFICIENT_BALANCE": "Insufficient balance for transaction",
    "SLIPPAGE_EXCEEDED": "Slippage tolerance exceeded",
    "TRANSACTION_TIMEOUT": "Transaction timed out",
    "RPC_ERROR": "RPC connection error",
    "INVALID_QUOTE": "Invalid or expired quote",
    "SIMULATION_FAILED": "Transaction simulation failed",
    "UNKNOWN_TOKEN": "Unknown token address"
}

# ===== MONITORING =====
METRICS_PORT = 8000
HEALTH_CHECK_INTERVAL = 60  # seconds
LOG_ROTATION_SIZE = 10 * 1024 * 1024  # 10MB
LOG_RETENTION_DAYS = 7

# ===== RATE LIMITS =====
RATE_LIMITS = {
    "jupiter_api": {"calls_per_second": 10, "burst": 20},
    "raydium_api": {"calls_per_second": 5, "burst": 10},
    "dexscreener": {"calls_per_second": 3, "burst": 5},
    "rpc_calls": {"calls_per_second": 40, "burst": 50},
    "transaction_submit": {"calls_per_second": 5, "burst": 10}
}

# ===== CACHE SETTINGS =====
PRICE_CACHE_TTL = 3  # seconds
QUOTE_CACHE_TTL = 5  # seconds
TOKEN_ACCOUNT_CACHE_TTL = 60  # seconds

# ===== DATABASE =====
DB_PATH = "data/arbitrage.db"
DB_BACKUP_INTERVAL = 3600  # seconds (1 hour)
MAX_DB_SIZE = 100 * 1024 * 1024  # 100MB

# ===== KNOWN ISSUES =====
PROBLEMATIC_TOKENS = {
    # Tokens with known issues
    "LUNA": "F6v4wfAdJB8D8p77bMXZgYt8TDKsYxLYxH5AFhUkYx9W",  # Old Terra Luna
    "FTT": "AGFEad2et2ZJif9jaGpdMixQqvW5i81aBdvKe7PHNfz3",   # FTX Token
}

# ===== UTILITY FUNCTIONS =====
def lamports_to_sol(lamports: int) -> Decimal:
    """Convert lamports to SOL"""
    return Decimal(lamports) / Decimal(LAMPORTS_PER_SOL)

def sol_to_lamports(sol: Decimal) -> int:
    """Convert SOL to lamports"""
    return int(sol * Decimal(LAMPORTS_PER_SOL))

def format_token_amount(amount: int, decimals: int) -> Decimal:
    """Format token amount with proper decimals"""
    return Decimal(amount) / Decimal(10 ** decimals)

def parse_token_amount(amount: Decimal, decimals: int) -> int:
    """Parse token amount to raw format"""
    return int(amount * Decimal(10 ** decimals))