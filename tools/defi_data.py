"""Utility functions for fetching DeFi data from Solana ecosystem APIs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

# API endpoints
JUPITER_PRICE_URL = "https://price.jup.ag/v6/price"
JUPITER_DEPTH_URL = "https://price.jup.ag/v6/market-depth"
SOLBLAZE_APR_URL = "https://solblaze.org/api/apr.json"
MARINADE_APR_URL = "https://api.marinade.finance/analytics/apr"
JITO_APR_URL = "https://api.jito.network/apr"


def _fetch_json(url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return JSON data from a given URL or ``None`` if the request fails."""
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def get_prices(tokens: List[str]) -> Dict[str, float]:
    """Query current token prices from the Jupiter Aggregator.

    Args:
        tokens: List of token symbols to query.

    Returns:
        A mapping from token symbol to its price in USD.
    """
    data = _fetch_json(JUPITER_PRICE_URL, {"ids": ",".join(tokens)})
    prices: Dict[str, float] = {}
    if data and "data" in data:
        for token in tokens:
            token_info = data["data"].get(token)
            if token_info and "price" in token_info:
                prices[token] = token_info["price"]
    return prices


def get_yield_opportunities() -> Dict[str, float]:
    """Pull yield APRs from SolBlaze, Marinade, and Jito APIs."""
    yields: Dict[str, float] = {}

    solblaze = _fetch_json(SOLBLAZE_APR_URL)
    if solblaze and "apr" in solblaze:
        yields["SolBlaze"] = solblaze["apr"]

    marinade = _fetch_json(MARINADE_APR_URL)
    if marinade and "apr" in marinade:
        yields["Marinade"] = marinade["apr"]

    jito = _fetch_json(JITO_APR_URL)
    if jito and "apr" in jito:
        yields["Jito"] = jito["apr"]

    return yields


def get_liquidity_depths(pairs: List[str]) -> Dict[str, Any]:
    """Return liquidity depth data for given token pairs from Jupiter.

    Args:
        pairs: Token pair identifiers in the form ``"BASE-QUOTE"`` (e.g. ``"SOL-USDC"``).

    Returns:
        Mapping from pair identifier to depth information.
    """
    data = _fetch_json(JUPITER_DEPTH_URL, {"ids": ",".join(pairs)})
    return data.get("data", {}) if data else {}

