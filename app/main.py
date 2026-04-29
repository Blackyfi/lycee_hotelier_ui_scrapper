from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import get_settings
from .db import init_db
from .routers import api as api_router
from .routers import pages as pages_router
from .scraper.runner import shutdown_scheduler, start_scheduler


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    init_db()
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(
    title="Lycée Hôtelier Watcher",
    description="Watches the Restaurant d'Application reservation calendar and notifies on opening.",
    version="0.1.0",
    lifespan=lifespan,
)

_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

app.include_router(pages_router.router)
app.include_router(api_router.router)
