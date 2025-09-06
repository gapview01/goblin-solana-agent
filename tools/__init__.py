"""On-chain search and DeFi opportunities agent."""

from .defi_data import (
    get_liquidity_depths,
    get_prices,
    get_yield_opportunities,
)

__all__ = [
    "get_prices",
    "get_yield_opportunities",
    "get_liquidity_depths",
]
