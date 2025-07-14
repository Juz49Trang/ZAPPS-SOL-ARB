#!/usr/bin/env python3
"""
Standalone balance checker - no import issues
"""

import asyncio
import json
import os
from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
import base58

async def check_balances():
    """Check SOL and token balances"""
    # Load wallet
    wallet_path = 'wallet.json'
    if not os.path.exists(wallet_path):
        print("❌ wallet.json not found!")
        print("Run: python scripts/create_wallet.py")
        return
    
    try:
        with open(wallet_path, 'r') as f:
            wallet_data = json.load(f)
            keypair = Keypair.from_bytes(base58.b58decode(wallet_data['secret_key']))
    except Exception as e:
        print(f"❌ Error loading wallet: {e}")
        return
    
    # Load config for RPC endpoint
    config_path = 'config/config.json'
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
            rpc_endpoint = config.get('rpc_endpoint', 'https://api.mainnet-beta.solana.com')
    else:
        rpc_endpoint = 'https://api.mainnet-beta.solana.com'
    
    # Try environment variable override
    rpc_endpoint = os.environ.get('RPC_ENDPOINT', rpc_endpoint)
    
    print(f"🌐 Connecting to {rpc_endpoint}...")
    
    # Connect to Solana
    client = AsyncClient(rpc_endpoint)
    
    try:
        # Test connection
        print("Testing connection...")
        is_connected = await client.is_connected()
        if not is_connected:
            print("❌ Cannot connect to Solana RPC")
            return
            
        # Get SOL balance
        print(f"\n💳 Wallet: {keypair.pubkey()}")
        
        balance_response = await client.get_balance(keypair.pubkey())
        sol_balance = balance_response.value / 1e9
        
        print(f"💰 SOL Balance: {sol_balance:.4f} SOL")
        
        # Estimate USD value
        sol_price = 150  # You could fetch this from an API
        usd_value = sol_balance * sol_price
        print(f"💵 Estimated Value: ${usd_value:.2f} (at ${sol_price}/SOL)")
        
        # Check if balance is sufficient
        print("\n📊 Status:")
        if sol_balance < 0.05:
            print("⚠️  WARNING: Very low SOL balance! You need at least 0.1 SOL for fees.")
        elif sol_balance < 0.1:
            print("⚠️  WARNING: Low SOL balance! Add more SOL for safe operation.")
        else:
            print("✅ SOL balance sufficient for trading")
        
        # Get recent blockhash to verify connection
        blockhash_resp = await client.get_latest_blockhash()
        print(f"\n🔗 Connected to Solana (slot: {blockhash_resp.context.slot})")
        
        print("\n💡 Next steps:")
        print("1. Fund your wallet with USDC for trading")
        print("2. Run the monitor: python -m src.simple_monitor")
        print("3. Start the bot: python -m src.arbitrage_bot")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nTroubleshooting:")
        print("1. Check your internet connection")
        print("2. Try a different RPC endpoint")
        print("3. Make sure wallet.json is valid")
    finally:
        await client.close()

if __name__ == "__main__":
    print("🔍 Checking Wallet Balances")
    print("=" * 40)
    
    # Check if running on Windows and handle event loop
    if os.name == 'nt':
        # Windows specific event loop policy
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(check_balances())