from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from .. import asset_export, snapshots
from .. import vault as v
from ..portfolio import Portfolio, PortfolioNotFound
from . import get_vault
from .portfolio import get_portfolio

router = APIRouter(prefix="/api")


class ExportIn(BaseModel):
    password: str = Field(max_length=1024)
    format: Literal["csv", "json"] = "json"


class AssetsExportIn(BaseModel):
    password: str = Field(max_length=1024)
    format: Literal["csv", "json"] = "csv"
    scope: Literal["current", "history"] = "current"
    portfolio_id: int | None = None
    lang: Literal["ru", "en"] = "ru"


class BackupIn(BaseModel):
    password: str = Field(max_length=1024)


def _check(vault: v.Vault, password: str) -> None:
    # Экспорт отдаёт данные из базы в открытом виде, поэтому пароль спрашивается заново.
    try:
        vault.verify_password(password)
    except v.WrongPassword:
        raise HTTPException(403, "Неверный пароль") from None
    except v.TooManyAttempts as e:
        raise HTTPException(429, f"Слишком много попыток, подождите {e.retry_after} с") from None


def _download(content: bytes, filename: str, media_type: str, display_name: str | None = None) -> Response:
    # filename — ASCII для любых программ, filename* — то же с названием портфолио по-русски.
    disposition = f'attachment; filename="{filename}"'
    if display_name:
        disposition += f"; filename*=UTF-8''{quote(display_name)}"
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})


@router.get("/snapshots/summary")
def snapshots_summary(vault: v.Vault = Depends(get_vault)):
    return snapshots.summary(vault)


@router.post("/export/snapshots")
def export_snapshots(body: ExportIn, vault: v.Vault = Depends(get_vault)):
    _check(vault, body.password)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    if body.format == "csv":
        return _download(snapshots.export_csv(vault), f"pouch-snapshots-{stamp}.csv", "text/csv; charset=utf-8")
    return _download(snapshots.export_json(vault), f"pouch-snapshots-{stamp}.json", "application/json")


@router.post("/export/backup")
def export_backup(body: BackupIn, vault: v.Vault = Depends(get_vault)):
    _check(vault, body.password)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return _download(snapshots.backup_zip(vault), f"pouch-backup-{stamp}.zip", "application/zip")


@router.post("/export/assets")
def export_assets(body: AssetsExportIn, vault: v.Vault = Depends(get_vault), p: Portfolio = Depends(get_portfolio)):
    """Статистика по активам без адресов и названий кошельков."""
    _check(vault, body.password)
    try:
        data = (asset_export.current if body.scope == "current" else asset_export.history)(
            p, body.portfolio_id, body.lang
        )
    except PortfolioNotFound:
        raise HTTPException(404, "Портфолио не найдено") from None
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    kind = "assets" if body.scope == "current" else "assets-history"
    filename = f"pouch-{kind}-{stamp}.{body.format}"
    kind_label = {
        ("ru", "current"): "активы", ("ru", "history"): "история активов",
        ("en", "current"): "assets", ("en", "history"): "asset history",
    }[(body.lang, body.scope)]
    display = f"Pouch — {data['portfolio']} — {kind_label} {stamp}.{body.format}"
    if body.format == "json":
        return _download(asset_export.to_json(data), filename, "application/json", display)
    content = asset_export.current_csv(data, body.lang) if body.scope == "current" else asset_export.history_csv(data)
    return _download(content, filename, "text/csv; charset=utf-8", display)
