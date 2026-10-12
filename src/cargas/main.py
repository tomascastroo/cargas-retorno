import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from cargas.api.routes import router
from cargas.config import get_settings
from cargas.db import assert_rls_safe_role, get_engine

STATIC_DIR = Path(__file__).parent / "web" / "static"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if get_settings().require_rls_safe_role:
        assert_rls_safe_role()
    yield


app = FastAPI(title="Cargas de retorno", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    settings = get_settings()
    return {"status": "ok", "llm": settings.llm_provider, "routing": settings.routing_provider}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
