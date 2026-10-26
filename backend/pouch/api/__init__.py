from fastapi import Request

from ..vault import Vault

SESSION_COOKIE = "pouch_session"


def get_vault(request: Request) -> Vault:
    return request.app.state.vault
