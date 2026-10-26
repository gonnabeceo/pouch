"""Реестр адаптеров. Источник каждой сети сменный: достаточно подставить другой класс."""

from .base import Adapter
from .doge import Dogecoin
from .evm import Zerion
from .hyperliquid import Hyperliquid
from .solana import SolanaRpc
from .ton import TonApi
from .tron import TronGrid
from .utxo import Esplora


def default_adapters() -> dict[str, Adapter]:
    return {
        "btc": Esplora("btc", "https://mempool.space/api"),
        "ltc": Esplora("ltc", "https://litecoinspace.org/api"),
        "doge": Dogecoin(),
        "ton": TonApi(),
        "tron": TronGrid(),
        "sol": SolanaRpc(),
        "evm": Zerion(),
        "hyperliquid": Hyperliquid(),
    }

