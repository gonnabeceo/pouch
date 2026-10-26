"""Hyperliquid на записанных и обезличенных ответах трёх реальных аккаунтов.

Итог каждого аккаунта сверяется с `portfolio` — суммой в интерфейсе Hyperliquid.
"""

import asyncio
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import HL
from pouch.chains.base import Context, Http, new_client
from pouch.chains.hyperliquid import Hyperliquid
from pouch.main import create_app
from pouch.portfolio import Portfolio
from pouch.prices import Directory
from pouch.vault import Vault


def fetch(fake_api, scenario, meta=None):
    fake_api.hl_scenario = scenario

    async def go():
        async with new_client(httpx.MockTransport(fake_api)) as client:
            ctx = Context(Http(client), {}, Directory.empty(), {} if meta is None else meta)
            return await Hyperliquid().fetch(HL[scenario], ctx)

    return asyncio.run(go())


def account_total(balance, label):
    return sum(h.amount * h.price_usd for h in balance.holdings if h.protocol == label and h.price_usd is not None)


def test_every_account_matches_hyperliquid_ui(fake_api):
    for scenario in HL:
        balance = fetch(fake_api, scenario)
        assert balance.warning is None, scenario
        for account in balance.details["accounts"]:
            ui = Decimal(account["ui_total"])
            assert account_total(balance, account["label"]) == pytest.approx(ui, rel=Decimal("0.005")), account["label"]


def test_sub_accounts_nested_under_master(fake_api):
    balance = fetch(fake_api, "standard_subs")
    labels = [a["label"] for a in balance.details["accounts"]]
    assert labels[0] == "Мастер" and len(labels) == 5
    assert all(label.startswith("Суб-аккаунт ") for label in labels[1:])
    # Добавлен только мастер, суб-аккаунты подтянулись сами и опрошены под своими адресами.
    subs = {a["address"] for a in balance.details["accounts"][1:]}
    assert len(subs) == 4 and HL["standard_subs"] not in subs


def test_standard_mode_counts_perps_unified_does_not(fake_api):
    balance = fetch(fake_api, "standard_subs")
    modes = {a["label"]: a["mode"] for a in balance.details["accounts"]}
    perps = {h.protocol for h in balance.holdings if h.symbol == "Перпы"}
    assert modes["Мастер"] == "disabled" and perps == {"Мастер"}
    assert all(modes[label] == "unifiedAccount" for label in modes if label != "Мастер")

    unified = fetch(fake_api, "unified_positions")
    account = unified.details["accounts"][0]
    # Account value перпов ненулевой, но в unified-режиме он — часть спота и не прибавляется.
    assert account["perp_value"] is None
    assert not any(h.symbol == "Перпы" for h in unified.holdings)


def test_staking_separate_from_spot(fake_api):
    balance = fetch(fake_api, "unified_staking")
    hype = {h.kind: h.amount for h in balance.holdings if h.symbol == "HYPE"}
    assert hype["free"] == Decimal("144620.00220207")
    assert hype["staked"] == Decimal("79630.33402975")
    # Сумма сходится с интерфейсом только если сложить и спот, и стейк — значит, не дубль.
    without_stake = account_total(balance, "Мастер") - hype["staked"] * next(
        h.price_usd for h in balance.holdings if h.kind == "staked"
    )
    ui = Decimal(balance.details["accounts"][0]["ui_total"])
    assert abs(without_stake - ui) / ui > Decimal("0.1")


def test_vaults_with_name_and_lockup(fake_api):
    balance = fetch(fake_api, "unified_staking")
    vault = balance.details["accounts"][0]["vaults"][0]
    assert vault["name"] and vault["locked_until"]
    assert any(h.symbol == "Vault" and h.name == vault["name"] for h in balance.holdings)


def test_vault_name_cached(fake_api):
    meta = {}
    fetch(fake_api, "unified_staking", meta)
    fetch(fake_api, "unified_staking", meta)
    assert sum(c.endswith("vaultDetails") for c in fake_api.calls) == 1


def test_open_positions_block(fake_api):
    balance = fetch(fake_api, "unified_positions")
    positions = balance.details["accounts"][0]["positions"]
    assert len(positions) == 12
    btc = next(p for p in positions if p["coin"] == "BTC")
    assert btc["side"] == "short" and Decimal(btc["size"]) > 0
    assert btc["leverage_type"] == "cross" and btc["leverage"] == 4
    for field in ("entry_px", "unrealized_pnl", "liquidation_px", "position_value"):
        assert field in btc
    # Номинал позиций в сумму не входит.
    total = account_total(balance, "Мастер")
    notional = sum(Decimal(p["position_value"]) for p in positions)
    assert total < notional


def test_mismatch_with_ui_is_flagged(fake_api):
    fake_api.hl_overrides["portfolio"] = [["day", {"accountValueHistory": [[0, "1.0"]], "pnlHistory": [], "vlm": "0"}]]
    balance = fetch(fake_api, "unified_staking")
    assert balance.warning and "расходится с интерфейсом Hyperliquid" in balance.warning


def test_wallet_view_has_details(tmp_path, fake_api):
    vault = Vault(tmp_path, 900)
    app = create_app(vault, allowed_hosts={"testserver"}, static_dir=tmp_path / "none",
                     portfolio_service=Portfolio(vault, transport=httpx.MockTransport(fake_api)))
    fake_api.hl_scenario = "standard_subs"
    with TestClient(app) as c:
        c.post("/api/setup", json={"password": "correct horse battery"})
        c.post("/api/wallets", json={"name": "HL", "network": "hyperliquid", "address": HL["standard_subs"]})
        view = c.post("/api/refresh").json()
        w = view["wallets"][0]
        assert w["status"] == "fresh" and len(w["details"]["accounts"]) == 5
        ui = sum(float(a["ui_total"]) for a in w["details"]["accounts"])
        assert w["value_usd"] == pytest.approx(ui, rel=0.005)
        assert {h["protocol"] for h in w["holdings"]} >= {"Мастер", "Суб-аккаунт gamma"}
