"""Placeholder functions for Solana transactions."""
from solana.rpc.api import Client


def get_balance(address: str) -> int:
    """Return balance for a given address."""
    client = Client("https://api.mainnet-beta.solana.com")
    return client.get_balance(address)["result"]["value"]
