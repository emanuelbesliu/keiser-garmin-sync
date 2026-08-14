"""FastAPI service frontend (the "hosted" mode).

Runs a continuous background loop that pulls completed Keiser M Series rides,
converts each to TCX and uploads it to Garmin Connect exactly once, and exposes
a small HTTP API for health checks, a status/monitoring widget, and an
on-demand sync trigger.

The web server is an *optional* extra (``pip install keiser-garmin-sync[server]``)
so the CLI stays lightweight. ``create_app`` builds an app around a resolved
``Config`` so both ``uvicorn keiser_garmin_sync.server:app`` and the ``serve``
CLI subcommand share one implementation.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import os
from typing import Any

from .config import Config
from .store import Store
from .syncer import Syncer

logger = logging.getLogger("keiser-garmin-sync.server")


def create_app(cfg: Config | None = None):
    try:
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit(
            "The web server needs the optional 'server' extra:\n"
            "    pip install 'keiser-garmin-sync[server]'"
        ) from exc

    cfg = cfg or Config.load()
    with contextlib.suppress(OSError):
        os.makedirs(cfg.garmin_tokenstore, exist_ok=True)

    store = Store(cfg.db_path)
    syncer = Syncer(cfg, store)

    app = FastAPI(title="Keiser -> Garmin Sync", version="1.1.0")
    state: dict[str, Any] = {
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "last_cycle": None,
        "running": False,
    }

    def _do_cycle() -> dict:
        state["running"] = True
        try:
            result = syncer.run_cycle()
            state["last_cycle"] = result
            return result
        finally:
            state["running"] = False

    @app.on_event("startup")
    async def _startup() -> None:
        async def loop() -> None:
            await asyncio.sleep(15)  # let probes pass before the first slow cycle
            while True:
                try:
                    logger.info("starting sync cycle")
                    result = await asyncio.to_thread(_do_cycle)
                    logger.info(
                        "cycle done: listed=%s uploaded=%s dup=%s skipped=%s errors=%s",
                        result.get("listed"), result.get("uploaded"),
                        result.get("duplicates"), result.get("skipped"),
                        result.get("errors"),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("sync cycle crashed: %s", exc)
                await asyncio.sleep(max(60, cfg.sync_interval_minutes * 60))

        app.state.loop_task = asyncio.create_task(loop())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = getattr(app.state, "loop_task", None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/status")
    async def status() -> JSONResponse:
        counts = store.counts()
        last = state.get("last_cycle") or {}
        return JSONResponse(
            {
                "started_at": state["started_at"],
                "running": state["running"],
                "keiser_configured": cfg.keiser_ready,
                "garmin_configured": cfg.garmin_ready,
                "garmin_status": syncer.garmin.status,
                "sync_interval_minutes": cfg.sync_interval_minutes,
                "lookback_days": cfg.lookback_days,
                "rides_total": counts.get("total", 0),
                "uploaded_total": counts.get("uploaded", 0),
                "duplicate_total": counts.get("duplicate", 0),
                "error_total": counts.get("error", 0),
                "mismatch_total": store.mismatch_count(),
                "last_sync": last.get("finished_at"),
                "totals": counts,
                "last_cycle": {
                    "finished_at": last.get("finished_at"),
                    "listed": last.get("listed"),
                    "uploaded": last.get("uploaded"),
                    "duplicates": last.get("duplicates"),
                    "skipped": last.get("skipped"),
                    "errors": last.get("errors"),
                    "mismatches": last.get("mismatches"),
                    "garmin_status": last.get("garmin_status"),
                },
                "recent": store.recent(10),
            }
        )

    @app.post("/sync")
    async def trigger() -> JSONResponse:
        if state["running"]:
            return JSONResponse({"status": "already_running"}, status_code=409)
        result = await asyncio.to_thread(_do_cycle)
        return JSONResponse({"status": "ok", "result": result})

    @app.post("/retype")
    async def retype(target: str | None = None, dry_run: bool = False) -> JSONResponse:
        """Backfill already-synced rides to the configured (or given) Garmin type."""
        result = await asyncio.to_thread(
            syncer.retype_synced, target, dry_run
        )
        return JSONResponse({"status": "ok", "result": result})

    return app


def run_server(cfg: Config | None = None, host: str = "0.0.0.0", port: int | None = None) -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit(
            "The web server needs the optional 'server' extra:\n"
            "    pip install 'keiser-garmin-sync[server]'"
        ) from exc
    cfg = cfg or Config.load()
    uvicorn.run(create_app(cfg), host=host, port=port or cfg.http_port, workers=1)


# Module-level app for `uvicorn keiser_garmin_sync.server:app`.
def __getattr__(name: str):  # lazy so importing the module doesn't need FastAPI
    if name == "app":
        return create_app()
    raise AttributeError(name)
