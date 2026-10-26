from datetime import UTC, datetime
from typing import Literal

import sqlcipher3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..addresses import NETWORKS, InvalidAddress, validate
from ..vault import Vault
from . import get_vault

router = APIRouter(prefix="/api")

Network = Literal["btc", "ltc", "doge", "evm", "sol", "ton", "tron", "hyperliquid"]


def _strip(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


class WalletIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    network: Network
    address: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=2000)

    strip_fields = field_validator("name", "address", "note", mode="before")(_strip)


class WalletPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    network: Network | None = None
    address: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=2000)
    hidden: bool | None = None

    strip_fields = field_validator("name", "address", "note", mode="before")(_strip)


def _row(r) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "network": r["network"],
        "address": r["address"],
        "note": r["note"],
        "hidden": bool(r["hidden"]),
        "created_at": r["created_at"],
    }


def _check_address(network: str, address: str) -> tuple[str, str]:
    try:
        return validate(network, address)
    except InvalidAddress as e:
        raise HTTPException(422, f"Адрес {NETWORKS[network]}: {e}") from None


def _get(conn, wallet_id: int):
    row = conn.execute("SELECT * FROM wallets WHERE id = ?", (wallet_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Кошелёк не найден")
    return row


DUPLICATE = "Этот адрес в этой сети уже добавлен"


@router.get("/networks")
def networks():
    return [{"id": k, "label": label} for k, label in NETWORKS.items()]


@router.get("/wallets")
def list_wallets(include_hidden: bool = False, vault: Vault = Depends(get_vault)):
    where = "" if include_hidden else "WHERE hidden = 0"
    with vault.db() as conn:
        rows = conn.execute(f"SELECT * FROM wallets {where} ORDER BY created_at, id").fetchall()
    return [_row(r) for r in rows]


@router.post("/wallets", status_code=201)
def create_wallet(body: WalletIn, vault: Vault = Depends(get_vault)):
    address, key = _check_address(body.network, body.address)
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    with vault.db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO wallets (name, network, address, address_key, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (body.name, body.network, address, key, body.note, created_at),
            )
        except sqlcipher3.IntegrityError:
            raise HTTPException(409, DUPLICATE) from None
        return _row(_get(conn, cur.lastrowid))


@router.patch("/wallets/{wallet_id}")
def update_wallet(wallet_id: int, body: WalletPatch, vault: Vault = Depends(get_vault)):
    with vault.db() as conn:
        current = _get(conn, wallet_id)
        fields = body.model_dump(exclude_none=True)
        if "network" in fields or "address" in fields:
            network = fields.get("network", current["network"])
            fields["address"], fields["address_key"] = _check_address(
                network, fields.get("address", current["address"])
            )
        if "hidden" in fields:
            fields["hidden"] = int(fields["hidden"])
        if fields:
            assignments = ", ".join(f"{k} = ?" for k in fields)
            try:
                conn.execute(
                    f"UPDATE wallets SET {assignments} WHERE id = ?", (*fields.values(), wallet_id)
                )
            except sqlcipher3.IntegrityError:
                raise HTTPException(409, DUPLICATE) from None
            if "address_key" in fields:
                # Балансы старого адреса к новому не относятся.
                conn.execute("DELETE FROM wallet_state WHERE wallet_id = ?", (wallet_id,))
        return _row(_get(conn, wallet_id))


@router.delete("/wallets/{wallet_id}", status_code=204)
def delete_wallet(wallet_id: int, vault: Vault = Depends(get_vault)):
    with vault.db() as conn:
        _get(conn, wallet_id)
        conn.execute("DELETE FROM wallets WHERE id = ?", (wallet_id,))
