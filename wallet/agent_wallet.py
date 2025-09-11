"""Utility functions for interacting with the Solana blockchain.

This module loads a Solana keypair from environment variables and exposes
simple helpers for balance checks, token swaps via Jupiter, staking, and
sending SOL.  Each action enforces a safety threshold where any request
involving more than 5 SOL requires human approval.
"""
import base64
import json
import os
from typing import Dict

import requests
from dotenv import load_dotenv
from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.rpc.api import Client
from solana.system_program import TransferParams, transfer
from solana.transaction import Transaction, TransactionInstruction

LAMPORTS_PER_SOL = 1_000_000_000
APPROVAL_THRESHOLD = 5

# Official program IDs for supported staking protocols
PROTOCOL_PROGRAM_IDS = {
    "marinade": "MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD",
    # Jito and SolBlaze use the SPL Stake Pool program
    "jito": "SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy",
    "solblaze": "SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy",
}

load_dotenv()


def _load_keypair() -> Keypair:
    """Load a ``Keypair`` from the ``SOLANA_KEYPAIR`` env variable."""
    raw = os.getenv("SOLANA_KEYPAIR")
    if not raw:
        raise EnvironmentError("SOLANA_KEYPAIR not set in environment")

    if os.path.isfile(raw):
        with open(raw, "r", encoding="utf8") as fh:
            secret = json.load(fh)
    else:
        secret = json.loads(raw)

    return Keypair.from_secret_key(bytes(secret))


KEYPAIR = _load_keypair()
RPC_ENDPOINT = os.getenv("RPC_ENDPOINT", "https://api.mainnet-beta.solana.com")
CLIENT = Client(RPC_ENDPOINT)


def _requires_human_approval(amount: float) -> bool:
    return amount > APPROVAL_THRESHOLD


def get_balance() -> float:
    """Return the wallet's SOL balance."""
    lamports = CLIENT.get_balance(KEYPAIR.public_key)["result"]["value"]
    return lamports / LAMPORTS_PER_SOL


def send_sol(recipient: str, amount: float) -> Dict:
    """Send SOL to ``recipient``.

    Returns a dict that always contains ``requires_human_approval`` and, when
    executed, the transaction signature.
    """
    if _requires_human_approval(amount):
        return {"requires_human_approval": True}

    tx = Transaction()
    tx.add(
        transfer(
            TransferParams(
                from_pubkey=KEYPAIR.public_key,
                to_pubkey=PublicKey(recipient),
                lamports=int(amount * LAMPORTS_PER_SOL),
            )
        )
    )
    signature = CLIENT.send_transaction(tx, KEYPAIR)["result"]
    return {"signature": signature, "requires_human_approval": False}


def swap_tokens(from_mint: str, to_mint: str, amount: float) -> Dict:
    """Swap tokens using Jupiter's quote/swap API."""
    if _requires_human_approval(amount):
        return {"requires_human_approval": True}

    amount_lamports = int(amount * LAMPORTS_PER_SOL)
    quote = requests.get(
        "https://quote-api.jup.ag/v6/quote",
        params={
            "inputMint": from_mint,
            "outputMint": to_mint,
            "amount": amount_lamports,
        },
        timeout=10,
    ).json()

    swap_resp = requests.post(
        "https://quote-api.jup.ag/v6/swap",
        json={"quoteResponse": quote, "userPublicKey": str(KEYPAIR.public_key)},
        timeout=10,
    ).json()

    tx = Transaction.deserialize(base64.b64decode(swap_resp["swapTransaction"]))
    signature = CLIENT.send_transaction(tx, KEYPAIR)["result"]
    return {"signature": signature, "requires_human_approval": False}


def stake_sol(protocol: str, amount_lamports: int) -> Dict:
    """Stake ``amount_lamports`` of SOL with a given ``protocol``.

    The transaction is simulated before submission. Any amount greater than
    ``APPROVAL_THRESHOLD`` SOL returns ``requiresApproval``.
    """
    if protocol not in PROTOCOL_PROGRAM_IDS:
        raise ValueError("unsupported protocol")

    amount_sol = amount_lamports / LAMPORTS_PER_SOL
    if _requires_human_approval(amount_sol):
        return {"requiresApproval": True}

    tx = Transaction()
    ix = TransactionInstruction(
        program_id=PublicKey(PROTOCOL_PROGRAM_IDS[protocol]),
        keys=[],
        data=b"",
    )
    tx.add(ix)

    sim = CLIENT.simulate_transaction(tx, KEYPAIR)
    if sim.get("result", {}).get("err"):
        return {"simulationError": sim["result"]["err"], "requiresApproval": False}

    sig = CLIENT.send_transaction(tx, KEYPAIR)["result"]
    return {"signature": sig, "requiresApproval": False}


def unstake_sol(protocol: str, amount_lamports: int) -> Dict:
    """Unstake ``amount_lamports`` of SOL from a given ``protocol``.

    Follows the same safety and simulation checks as ``stake_sol``.
    """
    if protocol not in PROTOCOL_PROGRAM_IDS:
        raise ValueError("unsupported protocol")

    amount_sol = amount_lamports / LAMPORTS_PER_SOL
    if _requires_human_approval(amount_sol):
        return {"requiresApproval": True}

    tx = Transaction()
    ix = TransactionInstruction(
        program_id=PublicKey(PROTOCOL_PROGRAM_IDS[protocol]),
        keys=[],
        data=b"",
    )
    tx.add(ix)

    sim = CLIENT.simulate_transaction(tx, KEYPAIR)
    if sim.get("result", {}).get("err"):
        return {"simulationError": sim["result"]["err"], "requiresApproval": False}

    sig = CLIENT.send_transaction(tx, KEYPAIR)["result"]
    return {"signature": sig, "requiresApproval": False}
