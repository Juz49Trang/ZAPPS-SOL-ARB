# ===== scripts/create_wallet.py =====
#!/usr/bin/env python3
"""Create a new Solana wallet for the arbitrage bot"""

import json
import base58
from solders.keypair import Keypair
import os
import sys

def create_new_wallet():
    """Create a new Solana wallet"""
    print("🔑 Creating new Solana wallet...")
    
    # Generate new keypair
    keypair = Keypair()
    
    # Get the secret key
    secret_key = base58.b58encode(bytes(keypair)).decode('utf-8')
    public_key = str(keypair.pubkey())
    
    # Create wallet data
    wallet_data = {
        "secret_key": secret_key,
        "public_key": public_key,
        "warning": "NEVER share this file or commit it to git!"
    }
    
    # Check if wallet already exists
    if os.path.exists('wallet.json'):
        response = input("⚠️  wallet.json already exists. Overwrite? (y/N): ")
        if response.lower() != 'y':
            print("Aborted.")
            return
    
    # Save wallet
    with open('wallet.json', 'w') as f:
        json.dump(wallet_data, f, indent=2)
    
    # Set proper permissions (Unix-like systems only)
    try:
        os.chmod('wallet.json', 0o600)
    except:
        pass
    
    print("\n✅ Wallet created successfully!")
    print(f"\n📍 Public Key (Wallet Address):")
    print(f"   {public_key}")
    print("\n⚠️  IMPORTANT:")
    print("   1. Save your wallet.json file securely")
    print("   2. Never share your secret key")
    print("   3. Fund this wallet with SOL and USDC before running the bot")
    print("\n💰 Minimum recommended balances:")
    print("   - SOL: 0.1 SOL (for transaction fees)")
    print("   - USDC: Amount based on your max_position_size")

if __name__ == "__main__":
    create_new_wallet()