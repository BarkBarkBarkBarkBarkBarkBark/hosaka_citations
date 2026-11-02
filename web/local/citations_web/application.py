from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import hmac
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from citations.api import run
from citations.cli import OperatorContext

STATIC = Path(__file__).parent / "static"
LOGGER = logging.getLogger(__name__)
READ_ACTIONS = {"get", "search", "search-persons", "list-violations", "history", "dashboard", "sync-status", "sync-conflicts"}
WRITE_ACTIONS = {
    "issue-for-attendee", "edit", "add-remedy", "edit-remedy", "complete-remedy",
    "resolve", "reopen", "void", "manage-citation", "sync-resolve", "sync-now",
}


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(max_length=64)
    params: dict = Field(default_factory=dict)


def create_app(root: Path | None = None, token: str | None = None) -> FastAPI:
    root = root or Path(os.environ["HOSAKA_EXTENSION_DATA"])
    token = token or os.environ["HOSAKA_GATEWAY_TOKEN"]
    context = OperatorContext(root, os.environ.get("HOSAKA_WEB_NETWORK") == "1")
    sync_lock = asyncio.Lock()
    refresh = asyncio.Event()

    async def synchronize(force=False):
        async with sync_lock:
            return await asyncio.to_thread(run, "sync-now" if force else "background-tick", {}, context)

    async def worker():
        while True:
            try:
                result = await synchronize()
                if not result["success"]:
                    LOGGER.warning("Citation synchronization failed; inspect synchronization status.")
            except (OSError, ValueError):
                LOGGER.exception("Citation synchronization failed; local operations remain available.")
            try:
                await asyncio.wait_for(refresh.wait(), 60)
                refresh.clear()
            except asyncio.TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        initialized = await asyncio.to_thread(run, "initialize", {}, context)
        if not initialized["success"]:
            raise RuntimeError(initialized["error"])
        task = asyncio.create_task(worker()) if os.environ.get("HOSAKA_WEB_BACKGROUND") == "1" else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def gateway_only(request: Request, call_next):
        if not hmac.compare_digest(request.headers.get("x-hosaka-gateway-token", ""), token):
            return JSONResponse({"detail": "Use Hosaka Gateway."}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/wall")
    async def wall(
        q: str = Query("", max_length=200),
        status: Literal["all", "open", "resolved", "void"] = "all",
        offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    ):
        result = await asyncio.to_thread(run, "list-citations", {"query": q, "status": status, "offset": offset, "limit": limit}, context)
        if not result["success"]:
            raise HTTPException(400, result["error"])
        rows = result["rows"]
        return {"items": rows, **result["record"]}

    @app.post("/api/actions")
    async def action(body: Action, request: Request):
        if body.action not in READ_ACTIONS | WRITE_ACTIONS:
            raise HTTPException(403, "Action is not available on the web.")
        if body.action in WRITE_ACTIONS and request.headers.get("x-hosaka-role") != "organizer":
            raise HTTPException(403, "Organizer authorization required.")
        if body.action == "sync-now":
            result = await synchronize(force=True)
        else:
            result = await asyncio.to_thread(run, body.action, body.params, context)
        if not result["success"]:
            raise HTTPException(409, result["error"])
        if body.action in WRITE_ACTIONS:
            refresh.set()
        return result

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
