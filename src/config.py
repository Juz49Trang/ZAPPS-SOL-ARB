"""
Configuration management for the Solana Arbitrage Bot
"""

import json
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

@dataclass
class TradingStrategy:
    """Trading strategy configuration"""
    name: str
    description: str
    min_profit_usd: Decimal
    max_position_size: Decimal
    max_price_impact: Decimal
    min_liquidity_ratio: Decimal
    enabled_dexs: list
    token_categories: list
    max_concurrent_trades: int
    special_rules: Dict[str, Any] = field(default_factory=dict)

@dataclass
class RiskParameters:
    """Risk management parameters"""
    max_daily_trades: int
    max_hourly_trades: int
    cooldown_after_loss_seconds: int
    stop_after_consecutive_losses: int
    max_gas_fee_usd: Decimal
    max_daily_loss: Decimal

@dataclass
class ExecutionParameters:
    """Trade execution parameters"""
    use_jito_bundles: bool
    priority_fee_preset: str
    slippage_tolerance_bps: int
    transaction_timeout_seconds: int
    confirmation_commitment: str

class Config:
    """Centralized configuration management"""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir
        self._main_config: Dict[str, Any] = {}
        self._token_config: Dict[str, Any] = {}
        self._strategy_config: Dict[str, Any] = {}
        self._env_overrides: Dict[str, Any] = {}
        
        # Load all configurations
        self.reload()
    
    def reload(self):
        """Reload all configuration files"""
        try:
            # Load main config
            main_config_path = os.path.join(self.config_dir, "config.json")
            if os.path.exists(main_config_path):
                with open(main_config_path, 'r') as f:
                    self._main_config = json.load(f)
                logger.info(f"Loaded main config from {main_config_path}")
            
            # Load token config
            token_config_path = os.path.join(self.config_dir, "tokens.json")
            if os.path.exists(token_config_path):
                with open(token_config_path, 'r') as f:
                    self._token_config = json.load(f)
                logger.info(f"Loaded token config from {token_config_path}")
            
            # Load strategy config
            strategy_config_path = os.path.join(self.config_dir, "strategies.json")
            if os.path.exists(strategy_config_path):
                with open(strategy_config_path, 'r') as f:
                    self._strategy_config = json.load(f)
                logger.info(f"Loaded strategy config from {strategy_config_path}")
            
            # Load environment overrides
            self._load_env_overrides()
            
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise
    
    def _load_env_overrides(self):
        """Load configuration overrides from environment variables"""
        env_mappings = {
            'MIN_PROFIT_USD': ('min_profit_usd', Decimal),
            'MAX_POSITION_SIZE': ('max_position_size', Decimal),
            'MAX_DAILY_LOSS': ('max_daily_loss', Decimal),
            'CHECK_INTERVAL': ('check_interval', int),
            'RPC_ENDPOINT': ('rpc_endpoint', str),
            'PRIORITY_FEE_MICROLAMPORTS': ('priority_fee_microlamports', int),
        }
        
        for env_var, (config_key, type_func) in env_mappings.items():
            value = os.environ.get(env_var)
            if value:
                try:
                    self._env_overrides[config_key] = type_func(value)
                    logger.info(f"Override {config_key} from environment: {value}")
                except ValueError:
                    logger.warning(f"Invalid value for {env_var}: {value}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with environment override support"""
        # Check environment overrides first
        if key in self._env_overrides:
            return self._env_overrides[key]
        
        # Check main config
        if key in self._main_config:
            return self._main_config[key]
        
        # Check nested configs
        if '.' in key:
            parts = key.split('.')
            config_map = {
                'tokens': self._token_config,
                'strategies': self._strategy_config,
                'main': self._main_config
            }
            
            if parts[0] in config_map:
                value = config_map[parts[0]]
                for part in parts[1:]:
                    if isinstance(value, dict) and part in value:
                        value = value[part]
                    else:
                        return default
                return value
        
        return default
    
    @property
    def rpc_endpoint(self) -> str:
        """Get RPC endpoint with fallback"""
        endpoints = [
            self.get('rpc_endpoint'),
            os.environ.get('RPC_ENDPOINT'),
            os.environ.get('QUICKNODE_ENDPOINT'),
            os.environ.get('HELIUS_ENDPOINT'),
            "https://api.mainnet-beta.solana.com"
        ]
        
        for endpoint in endpoints:
            if endpoint:
                return endpoint
        
        return endpoints[-1]  # Default fallback
    
    @property
    def wallet_path(self) -> str:
        """Get wallet file path"""
        return self.get('wallet_path', 'wallet.json')
    
    @property
    def tokens(self) -> Dict[str, Any]:
        """Get token configurations"""
        return self._token_config.get('tokens', {})
    
    @property
    def active_strategy(self) -> TradingStrategy:
        """Get active trading strategy"""
        strategy_name = self._strategy_config.get('active_strategy', 'balanced')
        strategy_data = self._strategy_config.get('strategies', {}).get(strategy_name, {})
        
        return TradingStrategy(
            name=strategy_name,
            description=strategy_data.get('description', ''),
            min_profit_usd=Decimal(str(strategy_data.get('min_profit_usd', 10.0))),
            max_position_size=Decimal(str(strategy_data.get('max_position_size', 5000.0))),
            max_price_impact=Decimal(str(strategy_data.get('max_price_impact', 0.01))),
            min_liquidity_ratio=Decimal(str(strategy_data.get('min_liquidity_ratio', 0.1))),
            enabled_dexs=strategy_data.get('enabled_dexs', ['jupiter', 'raydium']),
            token_categories=strategy_data.get('token_categories', ['all']),
            max_concurrent_trades=strategy_data.get('max_concurrent_trades', 2),
            special_rules=strategy_data.get('special_rules', {})
        )
    
    @property
    def risk_parameters(self) -> RiskParameters:
        """Get risk management parameters"""
        risk_data = self._strategy_config.get('risk_parameters', {})
        
        return RiskParameters(
            max_daily_trades=risk_data.get('max_daily_trades', 100),
            max_hourly_trades=risk_data.get('max_hourly_trades', 20),
            cooldown_after_loss_seconds=risk_data.get('cooldown_after_loss_seconds', 300),
            stop_after_consecutive_losses=risk_data.get('stop_after_consecutive_losses', 3),
            max_gas_fee_usd=Decimal(str(risk_data.get('max_gas_fee_usd', 5.0))),
            max_daily_loss=Decimal(str(self.get('max_daily_loss', 100.0)))
        )
    
    @property
    def execution_parameters(self) -> ExecutionParameters:
        """Get execution parameters"""
        exec_data = self._strategy_config.get('execution_parameters', {})
        
        return ExecutionParameters(
            use_jito_bundles=exec_data.get('use_jito_bundles', False),
            priority_fee_preset=exec_data.get('priority_fee_preset', 'medium'),
            slippage_tolerance_bps=exec_data.get('slippage_tolerance_bps', 100),
            transaction_timeout_seconds=exec_data.get('transaction_timeout_seconds', 30),
            confirmation_commitment=exec_data.get('confirmation_commitment', 'confirmed')
        )
    
    def get_priority_fee(self) -> int:
        """Get priority fee in microlamports based on preset"""
        presets = {
            'low': 1000,
            'medium': 10000,
            'high': 50000,
            'ultra': 100000
        }
        
        preset = self.execution_parameters.priority_fee_preset
        custom_fee = self.get('priority_fee_microlamports')
        
        if custom_fee:
            return custom_fee
        
        return presets.get(preset, 10000)
    
    def is_token_enabled(self, token_symbol: str) -> bool:
        """Check if a token is enabled for trading"""
        token_data = self.tokens.get(token_symbol, {})
        
        # Check if explicitly disabled
        if token_data.get('skip', False):
            return False
        
        # Check category restrictions
        strategy = self.active_strategy
        if 'all' not in strategy.token_categories:
            token_category = token_data.get('category', 'unknown')
            if token_category not in strategy.token_categories:
                return False
        
        return True
    
    def get_token_max_position(self, token_symbol: str) -> Decimal:
        """Get maximum position size for a specific token"""
        token_data = self.tokens.get(token_symbol, {})
        category = token_data.get('category', 'unknown')
        
        # Get category multiplier
        category_data = self._token_config.get('categories', {}).get(category, {})
        multiplier = Decimal(str(category_data.get('max_position_multiplier', 1.0)))
        
        # Apply multiplier to strategy max position
        return self.active_strategy.max_position_size * multiplier
    
    def validate(self) -> bool:
        """Validate configuration"""
        required_keys = ['rpc_endpoint', 'wallet_path', 'usdc_mint']
        
        for key in required_keys:
            if not self.get(key):
                logger.error(f"Missing required configuration: {key}")
                return False
        
        # Validate strategy
        if not self._strategy_config.get('strategies'):
            logger.error("No strategies defined")
            return False
        
        return True

# Global config instance
config = None

def initialize_config(config_dir: str = "config") -> Config:
    """Initialize global configuration"""
    global config
    config = Config(config_dir)
    
    if not config.validate():
        raise ValueError("Invalid configuration")
    
    return config

def get_config() -> Config:
    """Get global configuration instance"""
    global config
    if config is None:
        config = initialize_config()
    return config