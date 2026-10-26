import pytest

from pouch.addresses import InvalidAddress, validate

VALID = [
    ("btc", "1JdXKhsZcys9oxWwUmoF995jHFzmJ2EK2D"),
    ("btc", "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"),
    ("btc", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"),
    ("btc", "BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4"),  # BIP-173, верхний регистр
    ("btc", "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"),  # BIP-350, taproot
    ("evm", "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"),  # EIP-55
    ("evm", "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc"),  # всё в нижнем регистре
    ("hyperliquid", "0x90f79bf6eb2c4f870365e785982e1f101e93b906"),
    ("sol", "So11111111111111111111111111111111111111112"),
    ("sol", "11111111111111111111111111111111"),
    ("tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"),  # контракт USDT
    ("ton", "EQCWm0pmzUMIbkwXk5IuJXar-cCkd8rOLc-VPnQPNywuYyTF"),
    ("ton", "0:969b4a66cd43086e4c1793922e2576abf9c0a477cace2dcf953e740f372c2e63"),
]

INVALID = [
    ("btc", "1JdXKhsZcys9oxWwUmoF995jHFzmJ2EK2E"),  # испорчена контрольная сумма
    ("btc", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdx"),
    ("btc", "bc1qW508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"),  # смешанный регистр
    ("btc", "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx"),  # тестнет
    ("btc", "LSnGScCYW5JXRGgPvJZEWuX2rwrY5fL3QA"),  # адрес другой сети
    ("ltc", "1JdXKhsZcys9oxWwUmoF995jHFzmJ2EK2D"),
    ("evm", "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD"),  # неверный EIP-55
    ("evm", "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beae"),  # 39 символов
    ("sol", "0OIl11111111111111111111111111111"),
    ("tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u"),
    ("ton", "EQCWm0pmzUMIbkwXk5IuJXar-cCkd8rOLc-VPnQPNywuYyTG"),
    ("btc", ""),
]


@pytest.mark.parametrize(("network", "address"), VALID)
def test_valid(network, address):
    validate(network, address)


@pytest.mark.parametrize(("network", "address"), INVALID)
def test_invalid(network, address):
    with pytest.raises(InvalidAddress):
        validate(network, address)


def test_evm_normalizes_to_checksum():
    shown, key = validate("evm", "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed")
    assert shown == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    assert key == shown.lower()


def test_ton_forms_share_key():
    _, friendly = validate("ton", "EQCWm0pmzUMIbkwXk5IuJXar-cCkd8rOLc-VPnQPNywuYyTF")
    _, raw = validate("ton", "0:969B4A66CD43086E4C1793922E2576ABF9C0A477CACE2DCF953E740F372C2E63")
    assert friendly == raw


def test_ltc_generated():
    # Собираем валидные LTC-адреса тем же кодированием, что использует кошелёк.
    from hashlib import sha256

    from pouch.addresses import _B58

    def b58check(payload: bytes) -> str:
        raw = payload + sha256(sha256(payload).digest()).digest()[:4]
        n, out = int.from_bytes(raw, "big"), ""
        while n:
            n, r = divmod(n, 58)
            out = _B58[r] + out
        return out

    for version, prefix in ((0x30, "L"), (0x32, "M")):
        address = b58check(bytes([version]) + bytes(range(20)))
        assert address.startswith(prefix)
        validate("ltc", address)
