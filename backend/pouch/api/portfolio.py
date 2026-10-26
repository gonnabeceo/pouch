from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from .. import history
from ..portfolio import API_KEYS, Portfolio, PortfolioConflict, PortfolioNotFound

router = APIRouter(prefix="/api")


def get_portfolio(request: Request) -> Portfolio:
    return request.app.state.portfolio


class KeyIn(BaseModel):
    value: str | None = Field(default=None, max_length=512)


NOT_FOUND = "Портфолио не найдено"


@router.get("/portfolio")
def portfolio(portfolio_id: int | None = None, p: Portfolio = Depends(get_portfolio)):
    try:
        return p.view(portfolio_id)
    except PortfolioNotFound:
        raise HTTPException(404, NOT_FOUND) from None


@router.post("/refresh")
async def refresh(portfolio_id: int | None = None, p: Portfolio = Depends(get_portfolio)):
    try:
        return await p.refresh(portfolio_id)
    except PortfolioNotFound:
        raise HTTPException(404, NOT_FOUND) from None


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    wallet_ids: list[int] = Field(default_factory=list, max_length=1000)

    strip_name = field_validator("name", mode="before")(lambda v: v.strip() if isinstance(v, str) else v)


class PortfolioPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=48)
    wallet_ids: list[int] | None = Field(default=None, max_length=1000)

    strip_name = field_validator("name", mode="before")(lambda v: v.strip() if isinstance(v, str) else v)


def _save(p: Portfolio, portfolio_id: int | None, name: str | None, wallet_ids: list[int] | None):
    try:
        return p.save_portfolio(portfolio_id, name, wallet_ids)
    except PortfolioNotFound:
        raise HTTPException(404, NOT_FOUND) from None
    except PortfolioConflict as e:
        raise HTTPException(409, str(e)) from None


@router.get("/portfolios")
def portfolios(p: Portfolio = Depends(get_portfolio)):
    return p.portfolios()


@router.post("/portfolios", status_code=201)
def create_portfolio(body: PortfolioIn, p: Portfolio = Depends(get_portfolio)):
    return _save(p, None, body.name, body.wallet_ids)


@router.patch("/portfolios/{portfolio_id}")
def update_portfolio(portfolio_id: int, body: PortfolioPatch, p: Portfolio = Depends(get_portfolio)):
    return _save(p, portfolio_id, body.name, body.wallet_ids)


@router.delete("/portfolios/{portfolio_id}", status_code=204)
def delete_portfolio(portfolio_id: int, p: Portfolio = Depends(get_portfolio)):
    try:
        p.delete_portfolio(portfolio_id)
    except PortfolioNotFound:
        raise HTTPException(404, NOT_FOUND) from None


@router.get("/settings/keys")
def keys(p: Portfolio = Depends(get_portfolio)):
    # Сами ключи во фронт не отдаются: только признак, что ключ задан.
    stored = p.keys()
    return {name: name in stored for name in API_KEYS}


@router.put("/settings/keys/{name}", status_code=204)
def set_key(name: str, body: KeyIn, p: Portfolio = Depends(get_portfolio)):
    if name not in API_KEYS:
        raise HTTPException(404, "Неизвестный провайдер")
    p.set_key(name, (body.value or "").strip() or None)


class SettingsIn(BaseModel):
    dust_threshold_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    refresh_interval_minutes: int | None = Field(default=None, ge=1, le=60)


@router.get("/settings/general")
def general(p: Portfolio = Depends(get_portfolio)):
    return p.settings()


@router.put("/settings/general")
def update_general(body: SettingsIn, p: Portfolio = Depends(get_portfolio)):
    return p.update_settings(**body.model_dump())


HistoryDays = Query(default=30, ge=0, le=365, description="0 — вся история")


@router.get("/history")
def snapshot_history(portfolio_id: int | None = None, days: int = HistoryDays, p: Portfolio = Depends(get_portfolio)):
    try:
        return history.snapshot_history(p, portfolio_id, days)
    except PortfolioNotFound:
        raise HTTPException(404, NOT_FOUND) from None


@router.get("/history/simulated")
async def simulated_history(portfolio_id: int | None = None, days: int = HistoryDays,
                            p: Portfolio = Depends(get_portfolio)):
    try:
        return await history.simulated_history(p, portfolio_id, days)
    except PortfolioNotFound:
        raise HTTPException(404, NOT_FOUND) from None
