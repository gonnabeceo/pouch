from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import vault as v
from . import SESSION_COOKIE, get_vault

router = APIRouter(prefix="/api")


class PasswordIn(BaseModel):
    password: str = Field(max_length=1024)


def _set_session(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", path="/")


@router.get("/status")
def status(request: Request, vault: v.Vault = Depends(get_vault)):
    return {
        "initialized": vault.initialized,
        "unlocked": vault.check_session(request.cookies.get(SESSION_COOKIE)),
        "autolock_seconds": vault.autolock_seconds,
        "min_password_length": v.MIN_PASSWORD_LENGTH,
    }


@router.post("/setup", status_code=204)
def setup(body: PasswordIn, response: Response, vault: v.Vault = Depends(get_vault)):
    try:
        token = vault.setup(body.password)
    except v.AlreadyInitialized:
        raise HTTPException(409, "Мастер-пароль уже задан") from None
    except v.WeakPassword:
        raise HTTPException(422, f"Пароль короче {v.MIN_PASSWORD_LENGTH} символов") from None
    except v.VaultError as e:
        raise HTTPException(500, str(e)) from None
    _set_session(response, token)


@router.post("/unlock", status_code=204)
def unlock(body: PasswordIn, response: Response, vault: v.Vault = Depends(get_vault)):
    try:
        token = vault.unlock(body.password)
    except v.NotInitialized:
        raise HTTPException(409, "Мастер-пароль ещё не задан") from None
    except v.WrongPassword:
        raise HTTPException(401, "Неверный пароль") from None
    except v.TooManyAttempts as e:
        raise HTTPException(
            429, f"Слишком много попыток, подождите {e.retry_after} с",
            headers={"Retry-After": str(e.retry_after)},
        ) from None
    except v.VaultError as e:
        raise HTTPException(500, str(e)) from None
    _set_session(response, token)


@router.post("/lock", status_code=204)
def lock(response: Response, vault: v.Vault = Depends(get_vault)):
    vault.lock()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/activity", status_code=204)
def activity():
    # Сам факт авторизованного запроса уже продлил сессию в middleware.
    return None


class ChangePasswordIn(BaseModel):
    old: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


@router.post("/password", status_code=204)
def change_password(body: ChangePasswordIn, vault: v.Vault = Depends(get_vault)):
    try:
        vault.change_password(body.old, body.new)
    except v.WrongPassword:
        raise HTTPException(403, "Текущий пароль неверен") from None
    except v.WeakPassword:
        raise HTTPException(422, f"Новый пароль короче {v.MIN_PASSWORD_LENGTH} символов") from None
