"""FastAPI app serving the StockLens dashboard.

Read-only in Phase 1. Bind to 127.0.0.1 - once Phase 2 wires the kill switch
and order cancellation into this app, it becomes a control surface for a live
brokerage account, and it has no authentication.

    uvicorn src.dashboard.api:app --host 127.0.0.1 --port 8000
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import load_config
from src.dashboard.service import DashboardService

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

_service = None


@asynccontextmanager
async def lifespan(_app):
    yield
    if _service is not None:
        _service.close()


app = FastAPI(title="StockLens AI", docs_url="/api/docs", lifespan=lifespan)


def get_service():
    global _service
    if _service is None:
        _service = DashboardService(load_config())
    return _service


@app.get("/api/overview")
def overview():
    return get_service().overview()


@app.get("/api/holdings")
def holdings():
    return get_service().holdings()


@app.get("/api/history")
def history(range: str = Query("3M", pattern="^(1W|1M|3M|1Y|ALL)$")):
    return get_service().history(range)


@app.get("/api/allocation")
def allocation(by: str = Query("market", pattern="^(market|currency)$")):
    return get_service().allocation(by)


@app.get("/api/reports")
def reports(limit: int = Query(20, ge=1, le=100)):
    return get_service().reports(limit)


@app.get("/api/health")
def health():
    return get_service().health()


@app.get("/api/settings")
def settings():
    return get_service().settings()


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
