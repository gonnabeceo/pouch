"""Dogecoin: основной источник dogechain.com, запасной BlockCypher (записанные и обезличенные ответы)."""

import asyncio
from decimal import Decimal
from hashlib import sha256

import httpx
import pytest

from conftest import DOGE, fixture
from pouch.addresses import _B58, InvalidAddress, validate
from pouch.chains import default_adapters
from pouch.chains.base import AdapterError, Context, Http, new_client
from pouch.prices import Directory

EXPECTED = Decimal("44992.77496769")


def fetch(handler):
    async def go():
        async with new_client(httpx.MockTransport(handler)) as client:
            return await default_adapters()["doge"].fetch(DOGE, Context(Http(client), {}, Directory.empty(), {}))

    return asyncio.run(go())


def test_dogechain_primary(fake_api):
    balance = fetch(fake_api)
    assert balance.holdings[0].amount == EXPECTED
    assert balance.holdings[0].coingecko_id == "dogecoin"
    assert not any("blockcypher" in c for c in fake_api.calls)


def test_falls_back_to_blockcypher(fake_api):
    fake_api.failing.add("dogechain.com")
    balance = fetch(fake_api)
    # Оба источника на одном адресе дают одинаковый баланс.
    assert balance.holdings[0].amount == EXPECTED
    assert any("blockcypher" in c for c in fake_api.calls)


def test_both_sources_down(fake_api):
    fake_api.failing |= {"dogechain.com", "api.blockcypher.com"}
    with pytest.raises(AdapterError) as e:
        fetch(fake_api)
    assert "dogechain.com" in str(e.value) and "blockcypher" in str(e.value) and DOGE not in str(e.value)


def test_unsuccessful_dogechain_answer_falls_back(fake_api):
    def handler(request):
        if request.url.host == "dogechain.com":
            return httpx.Response(200, json={"success": 0, "error": "Invalid address"})
        return fake_api(request)

    assert fetch(handler).holdings[0].amount == EXPECTED


def b58check(payload: bytes) -> str:
    raw = payload + sha256(sha256(payload).digest()).digest()[:4]
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return out


def test_doge_addresses():
    validate("doge", DOGE)
    p2sh = b58check(bytes([0x16]) + bytes(range(20)))
    assert p2sh[0] in "9A"
    validate("doge", p2sh)
    with pytest.raises(InvalidAddress):
        validate("doge", "1JdXKhsZcys9oxWwUmoF995jHFzmJ2EK2D")  # Bitcoin
    with pytest.raises(InvalidAddress):
        validate("doge", DOGE[:-1] + ("M" if DOGE[-1] != "M" else "N"))
