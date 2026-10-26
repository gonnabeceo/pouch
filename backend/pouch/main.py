import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import SESSION_COOKIE, auth, export, portfolio, wallets
from .portfolio import Portfolio
from .vault import Locked, Vault

PUBLIC_API = {"/api/status", "/api/setup", "/api/unlock"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

log = logging.getLogger("pouch")

# Никаких внешних скриптов, стилей, шрифтов и CDN: всё отдаётся с этого же сервера.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def create_app(
    vault: Vault | None = None,
    allowed_hosts: set[str] | None = None,
    static_dir: Path = config.STATIC_DIR,
    portfolio_service: Portfolio | None = None,
) -> FastAPI:
    vault = vault or Vault(config.DATA_DIR, config.AUTOLOCK_SECONDS)
    portfolio_service = portfolio_service or Portfolio(vault)
    hosts = allowed_hosts or {f"127.0.0.1:{config.PORT}", f"localhost:{config.PORT}"}
    if config.DEV:
        hosts |= {"127.0.0.1:5173", "localhost:5173"}
    origins = {f"http://{h}" for h in hosts}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def autolock_loop():
            while True:
                await asyncio.sleep(15)
                vault.check_autolock()

        task = asyncio.create_task(autolock_loop())
        try:
            yield
        finally:
            task.cancel()
            vault.lock("остановка сервера")

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.vault = vault
    app.state.portfolio = portfolio_service

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host и Origin защищают от DNS rebinding и запросов с чужих страниц.
        if request.headers.get("host") not in hosts:
            return JSONResponse({"detail": "Недопустимый Host"}, 403)
        origin = request.headers.get("origin")
        if request.method not in SAFE_METHODS and origin is not None and origin not in origins:
            return JSONResponse({"detail": "Недопустимый Origin"}, 403)
        path = request.url.path
        if path.startswith("/api/") and path not in PUBLIC_API:
            if not vault.check_session(request.cookies.get(SESSION_COOKIE)):
                log.debug("401 %s %s cookie=%s unlocked=%s", request.method, path,
                          SESSION_COOKIE in request.cookies, vault.unlocked)
                return JSONResponse({"detail": "locked"}, 401)
            # Автообновление не считается действием пользователя: иначе автоблокировка не сработает.
            if request.headers.get("x-pouch-background") != "1":
                vault.touch()
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Locked)
    async def locked(_request: Request, _exc: Locked):
        return JSONResponse({"detail": "locked"}, 401)

    app.include_router(auth.router)
    app.include_router(wallets.router)
    app.include_router(portfolio.router)
    app.include_router(export.router)

    if (static_dir / "index.html").exists():
        root = static_dir.resolve()
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, 404)
            file = (root / path).resolve()
            if path and file.is_file() and file.is_relative_to(root):
                return FileResponse(file)
            return FileResponse(root / "index.html")

    return app
