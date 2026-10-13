"""FastAPI application factory.

Run directly with ``uvicorn --app-dir src backend.main:app`` or through ``python src/run.py``.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.routes import decisions, documents, findings, health, projects, reports, reviews, settings
from .core.exceptions import AppError
from .core.lifecycle import AppContainer, get_container, set_container

log = logging.getLogger(__name__)
API_PREFIX = "/api/v1"


def create_app(container: AppContainer | None = None) -> FastAPI:
    """Build the app.  A pre-built ``container`` is used by tests; otherwise one is created on startup."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        c = container or AppContainer(start_logging=True)
        set_container(c)
        c.startup()
        log.info("Architecture Red Team backend ready (data dir: %s)", c.paths.root)
        try:
            yield
        finally:
            c.shutdown()
            if owned:
                set_container(None)

    app = FastAPI(
        title="Architecture Red Team API", version="1.0.0", lifespan=lifespan,
        description="Adversarial architecture review: six specialist critics, a moderator and a human-in-the-loop workflow. "
                    "Local proof of concept - binds to loopback and has no authentication.",
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": {"code": "request_validation_failed",
                                                                "message": "Request validation failed.",
                                                                "details": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]}})

    for module in (health, projects, documents, reviews, findings, decisions, reports, settings):
        app.include_router(module.router, prefix=API_PREFIX)
    return app


app = create_app()

__all__ = ["app", "create_app", "get_container"]
