"""Проверка формата публичных адресов под каждую сеть.

validate() возвращает адрес для показа и ключ уникальности. Ключ совпадает у
разных записей одного и того же адреса (регистр EVM, bounceable/non-bounceable в TON).
"""

import base64
import re
from hashlib import sha256

from Crypto.Hash import keccak

NETWORKS = {
    "btc": "Bitcoin",
    "ltc": "Litecoin",
    "doge": "Dogecoin",
    "evm": "EVM",
    "sol": "Solana",
    "ton": "TON",
    "tron": "Tron",
    "hyperliquid": "Hyperliquid",
}


class InvalidAddress(ValueError):
    pass


def validate(network: str, address: str) -> tuple[str, str]:
    address = address.strip()
    try:
        check = _VALIDATORS[network]
    except KeyError:
        raise InvalidAddress(f"Неизвестная сеть: {network}") from None
    if not address:
        raise InvalidAddress("Адрес пустой")
    return check(address)


# --- base58 ---

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            raise InvalidAddress(f"Недопустимый символ «{ch}»")
        n = n * 58 + i
    pad = len(s) - len(s.lstrip("1"))
    return b"\0" * pad + n.to_bytes((n.bit_length() + 7) // 8, "big")


def _b58check(s: str) -> bytes:
    raw = _b58decode(s)
    payload, checksum = raw[:-4], raw[-4:]
    if len(raw) < 5 or sha256(sha256(payload).digest()).digest()[:4] != checksum:
        raise InvalidAddress("Неверная контрольная сумма")
    return payload


# --- bech32 / bech32m (BIP-173, BIP-350) ---

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32, _BECH32M = 1, 0x2BC830A3


def _polymod(values) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= gen[i]
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data, frombits: int, tobits: int) -> bytes:
    acc = bits = 0
    out = []
    maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append((acc >> bits) & maxv)
    if bits >= frombits or (acc << (tobits - bits)) & maxv:
        raise InvalidAddress("Неверная длина данных")
    return bytes(out)


def _segwit(hrp: str, address: str) -> tuple[str, str]:
    if address.lower() != address and address.upper() != address:
        raise InvalidAddress("Смешанный регистр в bech32-адресе")
    addr = address.lower()
    pos = addr.rfind("1")
    if addr[:pos] != hrp or pos + 7 > len(addr) or len(addr) > 90:
        raise InvalidAddress("Неверный формат")
    data = [_CHARSET.find(c) for c in addr[pos + 1 :]]
    if -1 in data:
        raise InvalidAddress("Недопустимый символ")
    const = _polymod(_hrp_expand(hrp) + data)
    if const not in (_BECH32, _BECH32M):
        raise InvalidAddress("Неверная контрольная сумма")
    words = data[:-6]
    if not words or words[0] > 16:
        raise InvalidAddress("Неверная версия witness")
    version, program = words[0], _convertbits(words[1:], 5, 8)
    if not 2 <= len(program) <= 40 or (version == 0 and len(program) not in (20, 32)):
        raise InvalidAddress("Неверная длина witness-программы")
    if (version == 0) != (const == _BECH32):
        raise InvalidAddress("Неверная кодировка для этой версии witness")
    return addr, addr


def _base58_versions(address: str, versions: set[int]) -> tuple[str, str]:
    payload = _b58check(address)
    if len(payload) != 21 or payload[0] not in versions:
        raise InvalidAddress("Адрес другой сети или неверной длины")
    return address, address


# --- сети ---


def _btc(address: str):
    if address.lower().startswith("bc1"):
        return _segwit("bc", address)
    return _base58_versions(address, {0x00, 0x05})  # 1… P2PKH, 3… P2SH


def _ltc(address: str):
    if address.lower().startswith("ltc1"):
        return _segwit("ltc", address)
    return _base58_versions(address, {0x30, 0x32, 0x05})  # L…, M…, устаревший 3…


def _doge(address: str):
    return _base58_versions(address, {0x1E, 0x16})  # D… P2PKH, 9…/A… P2SH


def _evm_checksum(hex40: str) -> str:
    digest = keccak.new(digest_bits=256, data=hex40.lower().encode()).hexdigest()
    return "0x" + "".join(c.upper() if int(d, 16) >= 8 else c for c, d in zip(hex40.lower(), digest))


def _evm(address: str):
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        raise InvalidAddress("Ожидается 0x и 40 шестнадцатеричных символов")
    body = address[2:]
    checksummed = _evm_checksum(body)
    if body != body.lower() and body != body.upper() and address != checksummed:
        raise InvalidAddress("Неверная контрольная сумма EIP-55")
    return checksummed, checksummed.lower()


def _sol(address: str):
    if not 32 <= len(address) <= 44 or len(_b58decode(address)) != 32:
        raise InvalidAddress("Ожидается 32-байтный ключ в base58")
    return address, address


def _tron(address: str):
    payload = _b58check(address)
    if len(payload) != 21 or payload[0] != 0x41:
        raise InvalidAddress("Ожидается адрес, начинающийся с T")
    return address, address


def _crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


def _ton(address: str):
    raw = re.fullmatch(r"(0|-1):([0-9a-fA-F]{64})", address)
    if raw:
        key = f"{raw[1]}:{raw[2].lower()}"
        return key, key
    if len(address) != 48:
        raise InvalidAddress("Ожидается 48 символов или формат 0:<hex>")
    try:
        data = base64.b64decode(address.replace("-", "+").replace("_", "/"), validate=True)
    except ValueError:
        raise InvalidAddress("Неверная кодировка base64") from None
    if len(data) != 36 or _crc16(data[:34]) != int.from_bytes(data[34:], "big"):
        raise InvalidAddress("Неверная контрольная сумма")
    tag, workchain = data[0], int.from_bytes(data[1:2], "big", signed=True)
    if tag & 0x80:
        raise InvalidAddress("Это адрес тестовой сети")
    if tag not in (0x11, 0x51) or workchain not in (0, -1):
        raise InvalidAddress("Неверный формат")
    return address, f"{workchain}:{data[2:34].hex()}"


_VALIDATORS = {
    "btc": _btc,
    "ltc": _ltc,
    "doge": _doge,
    "evm": _evm,
    "sol": _sol,
    "ton": _ton,
    "tron": _tron,
    "hyperliquid": _evm,
}
