"""ExamOS API entrypoint (FastAPI).

Run:  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import BACKEND_DIR, settings
from app.db import init_db
from app.routers import agent as agent_router
from app.routers import content as content_router
from app.routers import learning as learning_router
from app.routers import studio as studio_router
from app.routers import system as system_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="ExamOS API",
    description="Personalized exam super-agent: exam data, knowledge graph, ROI planning, transformation and periodic testing.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_router.router)
app.include_router(content_router.router)
app.include_router(learning_router.router)
app.include_router(agent_router.router)
app.include_router(studio_router.router)

# serve the exported Next.js dashboard when present (single-port demo)
WEB_OUT = Path(
    os.environ.get("EXAMOS_WEB_OUT")
    or Path(__file__).resolve().parents[2] / "web" / "out"
)


class UiStaticFiles(StaticFiles):
    """Static UI with SPA fallback.

    Unknown *page* paths return the dashboard instead of Next's exported
    404.html, while API / docs paths keep normal JSON 404 semantics.
    """

    API_PREFIXES = ("/api/", "/docs", "/openapi", "/redoc", "/health", "/favicon")

    async def get_response(self, path, scope):
        request_path = scope.get("path", "") or ""
        is_api = request_path.startswith(self.API_PREFIXES) or str(path).startswith(
            ("api/", "docs", "openapi", "redoc", "health", "favicon")
        )
        response = await super().get_response(path, scope)
        if response.status_code == 404:
            if is_api:
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            index = Path(self.directory) / "index.html"
            if index.exists():
                return await super().get_response("index.html", scope)
        return response


if WEB_OUT.exists():
    icon_file = WEB_OUT / "icon.svg"
    if not icon_file.exists():
        icon_file = WEB_OUT / "favicon.ico"

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        if icon_file.exists():
            media_type = "image/svg+xml" if icon_file.suffix == ".svg" else "image/x-icon"
            return FileResponse(str(icon_file), media_type=media_type)
        return JSONResponse(status_code=204, content=None)

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    async def legacy_ui_root():
        return RedirectResponse("/", status_code=307)

    @app.get("/ui/{rest:path}", include_in_schema=False)
    async def legacy_ui(rest: str):
        return RedirectResponse(f"/{rest}", status_code=307)

    # mounted last: API routes above take precedence, everything else serves the static UI
    app.mount("/", UiStaticFiles(directory=str(WEB_OUT), html=True), name="ui")


@app.exception_handler(Exception)
async def unhandled_exception(_request, exc: Exception) -> JSONResponse:  # pragma: no cover
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def dev() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":  # pragma: no cover
    dev()
