import base64
import httpx
from typing import Optional, Tuple
import config

# Solana imports
try:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.transaction import VersionedTransaction
    SOLDERS_AVAILABLE = True
except ImportError:
    SOLDERS_AVAILABLE = False

try:
    from solana.rpc.async_api import AsyncClient
    from solana.rpc.types import TxOpts
    SOLANA_AVAILABLE = True
except ImportError:
    SOLANA_AVAILABLE = False


_keypair: Optional[object] = None
_rpc_client: Optional[object] = None


def load_keypair() -> Optional[object]:
    global _keypair
    if not SOLDERS_AVAILABLE:
        raise RuntimeError("solders no instalado. Ejecuta: pip install solders")

    pk = config.PRIVATE_KEY.strip()
    if not pk:
        raise ValueError("PRIVATE_KEY no configurada en .env")

    try:
        _keypair = Keypair.from_base58_string(pk)
    except Exception:
        # Try as byte array format [1,2,3,...]
        import json as _json
        try:
            byte_arr = _json.loads(pk)
            _keypair = Keypair.from_bytes(bytes(byte_arr))
        except Exception as e:
            raise ValueError(f"Formato de clave privada invalido: {e}")

    return _keypair


def set_private_key(pk: str) -> str:
    """Valida y carga una clave privada nueva (desde la UI). Devuelve la pubkey o lanza si es invalida."""
    global _keypair
    pk = (pk or "").strip()
    if not pk:
        raise ValueError("Clave vacia")
    old = config.PRIVATE_KEY
    config.PRIVATE_KEY = pk
    _keypair = None
    try:
        load_keypair()
        return get_public_key_str()
    except Exception:
        config.PRIVATE_KEY = old  # revertir si falla
        _keypair = None
        raise


def get_keypair() -> object:
    global _keypair
    if _keypair is None:
        load_keypair()
    return _keypair


def get_public_key_str() -> str:
    kp = get_keypair()
    return str(kp.pubkey())


def get_rpc_client() -> object:
    global _rpc_client
    if not SOLANA_AVAILABLE:
        raise RuntimeError("solana no instalado. Ejecuta: pip install solana")
    if _rpc_client is None:
        _rpc_client = AsyncClient(config.RPC_URL)
    return _rpc_client


async def get_sol_balance() -> float:
    pubkey_str = get_public_key_str()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            config.RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [pubkey_str]
            }
        )
        data = resp.json()
        lamports = data.get("result", {}).get("value", 0)
        return lamports / 1e9


async def get_token_balance(token_mint: str) -> Tuple[float, int]:
    """Returns (ui_amount, decimals)"""
    pubkey_str = get_public_key_str()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            config.RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    pubkey_str,
                    {"mint": token_mint},
                    {"encoding": "jsonParsed"}
                ]
            }
        )
        data = resp.json()
        accounts = data.get("result", {}).get("value", [])
        if not accounts:
            return 0.0, 6

        info = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]
        return float(info.get("uiAmount") or 0), int(info.get("decimals", 6))


async def sign_and_send_transaction(swap_tx_b64: str) -> str:
    """Sign a Jupiter transaction and send it. Returns signature."""
    if not SOLDERS_AVAILABLE:
        raise RuntimeError("solders no disponible")

    keypair = get_keypair()
    raw = base64.b64decode(swap_tx_b64)

    try:
        tx = VersionedTransaction.from_bytes(raw)
        signed_tx = VersionedTransaction(tx.message, [keypair])
        tx_bytes = bytes(signed_tx)
    except Exception as e:
        raise RuntimeError(f"Error firmando transaccion: {e}")

    encoded = base64.b64encode(tx_bytes).decode("utf-8")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            config.RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    encoded,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "preflightCommitment": "confirmed",
                        "maxRetries": 3,
                    }
                ]
            }
        )
        result = resp.json()
        if "error" in result:
            raise RuntimeError(f"RPC error: {result['error']}")
        return result["result"]


async def confirm_transaction(signature: str, timeout_seconds: int = 60) -> bool:
    import asyncio
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    async with httpx.AsyncClient(timeout=10) as client:
        while asyncio.get_event_loop().time() < deadline:
            resp = await client.post(
                config.RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignatureStatuses",
                    "params": [[signature], {"searchTransactionHistory": True}]
                }
            )
            data = resp.json()
            statuses = data.get("result", {}).get("value", [None])
            if statuses and statuses[0]:
                confirmation = statuses[0].get("confirmationStatus")
                if confirmation in ("confirmed", "finalized"):
                    err = statuses[0].get("err")
                    return err is None
            await asyncio.sleep(2)
    return False
