"""
Jito Bundle Client for Atomic Transaction Execution
"""

import asyncio
import aiohttp
import json
import time
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
import logging

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from solders.instruction import Instruction
from solana.rpc.async_api import AsyncClient
import base58

logger = logging.getLogger(__name__)

@dataclass
class JitoConfig:
    """Jito configuration"""
    block_engine_url: str = "https://mainnet.block-engine.jito.wtf/api/v1"
    tip_accounts: List[str] = None
    auth_keypair: Optional[Keypair] = None
    
    def __post_init__(self):
        if self.tip_accounts is None:
            self.tip_accounts = [
                "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
                "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
                "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
                "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
                "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
                "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
                "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
                "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT"
            ]

class JitoClient:
    """Client for interacting with Jito Labs block engine"""
    
    def __init__(self, config: JitoConfig, solana_client: AsyncClient):
        self.config = config
        self.solana_client = solana_client
        self.session = None
        self._tip_account_index = 0
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    def get_next_tip_account(self) -> Pubkey:
        """Get next tip account in rotation"""
        tip_account = self.config.tip_accounts[self._tip_account_index]
        self._tip_account_index = (self._tip_account_index + 1) % len(self.config.tip_accounts)
        return Pubkey.from_string(tip_account)
    
    def calculate_optimal_tip(self, expected_profit_lamports: int) -> int:
        """Calculate optimal tip amount based on expected profit"""
        # Tip 10-20% of expected profit, minimum 10k lamports
        tip_percentage = 0.15  # 15%
        calculated_tip = int(expected_profit_lamports * tip_percentage)
        
        # Minimum and maximum bounds
        min_tip = 10_000  # 0.00001 SOL
        max_tip = 1_000_000  # 0.001 SOL
        
        return max(min_tip, min(calculated_tip, max_tip))
    
    async def build_bundle_transactions(
        self,
        instructions_list: List[List[Instruction]],
        payer: Keypair,
        tip_lamports: int
    ) -> List[VersionedTransaction]:
        """Build a bundle of transactions with tip in the last one"""
        transactions = []
        
        # Get recent blockhash
        recent_blockhash = (await self.solana_client.get_latest_blockhash()).value.blockhash
        
        # Build each transaction
        for i, instructions in enumerate(instructions_list):
            # Add tip to last transaction
            if i == len(instructions_list) - 1 and tip_lamports > 0:
                tip_account = self.get_next_tip_account()
                tip_ix = transfer(
                    TransferParams(
                        from_pubkey=payer.pubkey(),
                        to_pubkey=tip_account,
                        lamports=tip_lamports
                    )
                )
                instructions = instructions + [tip_ix]
            
            # Create message and transaction
            from solders.message import MessageV0
            
            message = MessageV0.try_compile(
                payer=payer.pubkey(),
                instructions=instructions,
                recent_blockhash=recent_blockhash,
                address_lookup_table_accounts=[]
            )
            
            tx = VersionedTransaction(message, [payer])
            transactions.append(tx)
        
        return transactions
    
    async def send_bundle(
        self,
        transactions: List[VersionedTransaction],
        bundle_only: bool = True
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Send bundle to Jito block engine"""
        try:
            # Serialize transactions
            serialized_txs = []
            for tx in transactions:
                serialized_txs.append(base58.b58encode(bytes(tx)).decode('utf-8'))
            
            # Prepare bundle request
            bundle_data = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendBundle",
                "params": [serialized_txs]
            }
            
            headers = {
                "Content-Type": "application/json"
            }
            
            # Send to Jito
            async with self.session.post(
                f"{self.config.block_engine_url}/bundles",
                json=bundle_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    if "result" in result:
                        bundle_id = result["result"]
                        logger.info(f"Bundle sent successfully: {bundle_id}")
                        return True, bundle_id, None
                    else:
                        error = result.get("error", "Unknown error")
                        logger.error(f"Bundle rejected: {error}")
                        return False, None, str(error)
                else:
                    error = f"HTTP {response.status}: {await response.text()}"
                    logger.error(f"Failed to send bundle: {error}")
                    return False, None, error
                    
        except Exception as e:
            logger.error(f"Exception sending bundle: {e}")
            return False, None, str(e)
    
    async def get_bundle_status(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Get bundle status from Jito"""
        try:
            params = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBundleStatuses",
                "params": [[bundle_id]]
            }
            
            async with self.session.post(
                f"{self.config.block_engine_url}/bundles",
                json=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    if "result" in result and result["result"]:
                        return result["result"][0]
                return None
                
        except Exception as e:
            logger.error(f"Error getting bundle status: {e}")
            return None
    
    async def wait_for_bundle_confirmation(
        self,
        bundle_id: str,
        timeout: int = 30
    ) -> bool:
        """Wait for bundle confirmation"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status = await self.get_bundle_status(bundle_id)
            
            if status:
                bundle_status = status.get("confirmation_status")
                if bundle_status == "confirmed":
                    logger.info(f"Bundle {bundle_id} confirmed!")
                    return True
                elif bundle_status in ["failed", "rejected"]:
                    logger.error(f"Bundle {bundle_id} failed: {status}")
                    return False
            
            await asyncio.sleep(1)
        
        logger.warning(f"Bundle {bundle_id} confirmation timeout")
        return False
    
    async def simulate_bundle(
        self,
        transactions: List[VersionedTransaction]
    ) -> Tuple[bool, Optional[List[Dict]], Optional[str]]:
        """Simulate bundle execution"""
        try:
            results = []
            
            for tx in transactions:
                # Simulate each transaction
                sim_result = await self.solana_client.simulate_transaction(tx)
                
                if sim_result.value.err:
                    return False, None, f"Simulation failed: {sim_result.value.err}"
                
                results.append({
                    "units_consumed": sim_result.value.units_consumed,
                    "logs": sim_result.value.logs
                })
            
            return True, results, None
            
        except Exception as e:
            logger.error(f"Bundle simulation error: {e}")
            return False, None, str(e)