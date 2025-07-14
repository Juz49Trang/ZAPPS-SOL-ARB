"""
Transaction building and execution module
"""

import asyncio
import base64
from typing import Optional, List, Dict, Any, Tuple
from decimal import Decimal
import logging
import time

from solana.rpc.async_api import AsyncClient
from solana.transaction import Transaction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction
from solders.instruction import Instruction
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solders.hash import Hash
from solders.message import MessageV0

from ..constants import (
    DEFAULT_COMPUTE_UNITS,
    MAX_COMPUTE_UNITS,
    PRIORITY_FEE_LEVELS,
    MAX_TRANSACTION_SIZE,
    JITO_TIP_ACCOUNTS
)

logger = logging.getLogger(__name__)

class TransactionBuilder:
    """Build optimized Solana transactions"""
    
    @staticmethod
    def add_priority_fee_instructions(
        instructions: List[Instruction],
        compute_units: int = DEFAULT_COMPUTE_UNITS,
        priority_level: str = "medium"
    ) -> List[Instruction]:
        """Add compute budget instructions for priority fees"""
        # Get priority fee
        micro_lamports_per_cu = PRIORITY_FEE_LEVELS.get(priority_level, 10000)
        
        # Create compute budget instructions
        compute_limit_ix = set_compute_unit_limit(compute_units)
        compute_price_ix = set_compute_unit_price(micro_lamports_per_cu)
        
        # Add at the beginning
        return [compute_limit_ix, compute_price_ix] + instructions
    
    @staticmethod
    def add_jito_tip_instruction(
        instructions: List[Instruction],
        tip_amount: int,
        tip_account_index: int = 0
    ) -> List[Instruction]:
        """Add Jito tip instruction for MEV protection"""
        # Select tip account
        tip_account = Pubkey.from_string(JITO_TIP_ACCOUNTS[tip_account_index])
        
        # Create tip transfer
        tip_ix = transfer(
            TransferParams(
                from_pubkey=Pubkey.default(),  # Will be replaced with actual payer
                to_pubkey=tip_account,
                lamports=tip_amount
            )
        )
        
        # Add at the end
        return instructions + [tip_ix]
    
    @staticmethod
    async def build_versioned_transaction(
        client: AsyncClient,
        instructions: List[Instruction],
        payer: Pubkey,
        signers: List[Keypair],
        recent_blockhash: Optional[Hash] = None
    ) -> VersionedTransaction:
        """Build a versioned transaction"""
        if not recent_blockhash:
            response = await client.get_latest_blockhash()
            recent_blockhash = response.value.blockhash
        
        # Create message
        message = MessageV0.try_compile(
            payer=payer,
            instructions=instructions,
            recent_blockhash=recent_blockhash,
            address_lookup_table_accounts=[]
        )
        
        # Create transaction
        tx = VersionedTransaction(message, signers)
        
        # Check size
        serialized = bytes(tx)
        if len(serialized) > MAX_TRANSACTION_SIZE:
            raise ValueError(f"Transaction too large: {len(serialized)} bytes")
        
        return tx
    
    @staticmethod
    def estimate_transaction_fee(
        num_signatures: int,
        compute_units: int,
        priority_level: str = "medium"
    ) -> int:
        """Estimate transaction fee in lamports"""
        # Base fee (5000 lamports per signature)
        base_fee = 5000 * num_signatures
        
        # Priority fee
        micro_lamports_per_cu = PRIORITY_FEE_LEVELS.get(priority_level, 10000)
        priority_fee = (compute_units * micro_lamports_per_cu) // 1_000_000
        
        return base_fee + priority_fee

class TransactionExecutor:
    """Execute and monitor transactions"""
    
    def __init__(self, client: AsyncClient, rate_limiter=None):
        self.client = client
        self.rate_limiter = rate_limiter
    
    async def send_transaction(
        self,
        transaction: VersionedTransaction,
        max_retries: int = 3
    ) -> str:
        """Send transaction with retries"""
        if self.rate_limiter:
            await self.rate_limiter.acquire()
        
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Send transaction
                response = await self.client.send_transaction(
                    transaction,
                    opts={
                        "skip_preflight": False,
                        "preflight_commitment": "processed",
                        "max_retries": 0  # We handle retries ourselves
                    }
                )
                
                if response.value:
                    return str(response.value)
                
            except Exception as e:
                last_error = e
                logger.warning(f"Transaction send attempt {attempt + 1} failed: {e}")
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        raise Exception(f"Failed to send transaction after {max_retries} attempts: {last_error}")
    
    async def confirm_transaction(
        self,
        signature: str,
        timeout: int = 30
    ) -> bool:
        """Wait for transaction confirmation"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = await self.client.get_signature_statuses([signature])
                
                if response.value and response.value[0]:
                    status = response.value[0]
                    if status.confirmations >= 1 or status.confirmation_status == "confirmed":
                        return True
                    if status.err:
                        logger.error(f"Transaction failed: {status.err}")
                        return False
                
            except Exception as e:
                logger.warning(f"Error checking transaction status: {e}")
            
            await asyncio.sleep(0.5)
        
        logger.warning(f"Transaction confirmation timeout: {signature}")
        return False
    
    async def simulate_transaction(
        self,
        transaction: VersionedTransaction
    ) -> Tuple[bool, Optional[List[str]]]:
        """Simulate transaction to check if it would succeed"""
        try:
            response = await self.client.simulate_transaction(transaction)
            
            if response.value:
                if response.value.err:
                    return False, [str(response.value.err)]
                
                # Check logs for errors
                logs = response.value.logs or []
                error_logs = [log for log in logs if "failed" in log.lower() or "error" in log.lower()]
                
                if error_logs:
                    return False, error_logs
                
                return True, logs
            
            return False, ["Simulation failed: no response"]
            
        except Exception as e:
            return False, [f"Simulation error: {str(e)}"]
    
    async def get_transaction_details(self, signature: str) -> Optional[Dict]:
        """Get transaction details after confirmation"""
        try:
            response = await self.client.get_transaction(
                signature,
                encoding="json",
                max_supported_transaction_version=0
            )
            
            if response.value:
                return response.value
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting transaction details: {e}")
            return None

class TransactionMonitor:
    """Monitor multiple transactions"""
    
    def __init__(self, client: AsyncClient):
        self.client = client
        self.pending_transactions: Dict[str, Dict] = {}
    
    async def add_transaction(
        self,
        signature: str,
        metadata: Dict[str, Any]
    ):
        """Add transaction to monitor"""
        self.pending_transactions[signature] = {
            'signature': signature,
            'metadata': metadata,
            'added_at': time.time(),
            'status': 'pending'
        }
    
    async def monitor_all(self, timeout: int = 60) -> Dict[str, Dict]:
        """Monitor all pending transactions"""
        results = {}
        
        while self.pending_transactions and timeout > 0:
            signatures = list(self.pending_transactions.keys())
            
            # Check statuses in batches
            for i in range(0, len(signatures), 100):
                batch = signatures[i:i+100]
                
                try:
                    response = await self.client.get_signature_statuses(batch)
                    
                    if response.value:
                        for sig, status in zip(batch, response.value):
                            if status:
                                if status.confirmations >= 1 or status.confirmation_status == "confirmed":
                                    tx_data = self.pending_transactions.pop(sig)
                                    tx_data['status'] = 'confirmed'
                                    tx_data['error'] = None
                                    results[sig] = tx_data
                                elif status.err:
                                    tx_data = self.pending_transactions.pop(sig)
                                    tx_data['status'] = 'failed'
                                    tx_data['error'] = str(status.err)
                                    results[sig] = tx_data
                
                except Exception as e:
                    logger.error(f"Error monitoring transactions: {e}")
            
            await asyncio.sleep(1)
            timeout -= 1
        
        # Mark remaining as timeout
        for sig, tx_data in self.pending_transactions.items():
            tx_data['status'] = 'timeout'
            tx_data['error'] = 'Confirmation timeout'
            results[sig] = tx_data
        
        self.pending_transactions.clear()
        return results

class TransactionOptimizer:
    """Optimize transactions for better performance"""
    
    @staticmethod
    def calculate_optimal_compute_units(
        instructions: List[Instruction],
        safety_margin: float = 1.2
    ) -> int:
        """Calculate optimal compute units for transaction"""
        # Base estimates for common operations
        base_units = {
            'transfer': 2_000,
            'token_transfer': 10_000,
            'swap': 200_000,
            'create_account': 15_000,
            'close_account': 5_000
        }
        
        # Estimate based on instruction count
        # This is simplified - in production you'd analyze actual instructions
        estimated = len(instructions) * 50_000
        
        # Add safety margin
        with_margin = int(estimated * safety_margin)
        
        # Cap at maximum
        return min(with_margin, MAX_COMPUTE_UNITS)
    
    @staticmethod
    def should_use_jito(
        expected_profit: Decimal,
        network_congestion: float = 0.5
    ) -> Tuple[bool, int]:
        """Determine if Jito should be used and tip amount"""
        # Use Jito for high-value trades or high congestion
        if expected_profit > 50 or network_congestion > 0.7:
            # Calculate tip as percentage of profit
            tip_percentage = Decimal('0.01')  # 1% of profit
            tip_amount = int(expected_profit * tip_percentage * 1_000_000)  # Convert to lamports
            
            # Minimum tip of 10,000 lamports
            tip_amount = max(tip_amount, 10_000)
            
            return True, tip_amount
        
        return False, 0
    
    @staticmethod
    def batch_instructions(
        instructions: List[Instruction],
        max_per_transaction: int = 20
    ) -> List[List[Instruction]]:
        """Batch instructions into multiple transactions if needed"""
        batches = []
        
        for i in range(0, len(instructions), max_per_transaction):
            batch = instructions[i:i + max_per_transaction]
            batches.append(batch)
        
        return batches

# Utility functions
async def get_token_account_address(
    client: AsyncClient,
    owner: Pubkey,
    mint: Pubkey
) -> Optional[Pubkey]:
    """Get associated token account address"""
    from spl.token.constants import TOKEN_PROGRAM_ID
    from spl.token.instructions import get_associated_token_address
    
    try:
        ata = get_associated_token_address(owner, mint)
        
        # Check if account exists
        response = await client.get_account_info(ata)
        if response.value:
            return ata
        
        return None
        
    except Exception as e:
        logger.error(f"Error getting token account: {e}")
        return None

async def create_token_account_if_needed(
    client: AsyncClient,
    payer: Keypair,
    owner: Pubkey,
    mint: Pubkey
) -> Optional[Tuple[Pubkey, Optional[Instruction]]]:
    """Create token account if it doesn't exist"""
    from spl.token.constants import TOKEN_PROGRAM_ID
    from spl.token.instructions import (
        get_associated_token_address,
        create_associated_token_account
    )
    
    try:
        ata = get_associated_token_address(owner, mint)
        
        # Check if account exists
        response = await client.get_account_info(ata)
        if response.value:
            return ata, None  # Account already exists
        
        # Create instruction
        create_ix = create_associated_token_account(
            payer=payer.pubkey(),
            owner=owner,
            mint=mint
        )
        
        return ata, create_ix
        
    except Exception as e:
        logger.error(f"Error creating token account: {e}")
        return None, None