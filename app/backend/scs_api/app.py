"""FastAPI application: every section 5 endpoint under /api/v1, the error envelope, and the built frontend at /.

docs_url and redoc_url are off (their pages load scripts from a CDN); /openapi.json is generated from the models of
this build. Routes are declared in contract order with /cells/at before /cells/{cell_id}; the static frontend is
mounted last, after every /api/v1 route.
"""

from __future__ import annotations

import asyncio
import html
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import APP_VERSION, CONTRACT_VERSION
from .catalog import ResearchPathError
from .config import CAVEAT_SHORT, PRODUCT_CAVEAT, Settings
from .envelope import ApiError, error_response
from .routes import (cells, contacts, events, export, geo, layers, leads, lights, meta, passes, rasters, search,
                     timeline, vessels)
from .store import Store

API = "/api/v1"


def not_built_page(settings: Settings) -> str:
    e = html.escape
    return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
            f"content=\"width=device-width, initial-scale=1\"><title>SCS Vessel Watch</title><style>body{{font:15px/1.5 "
            f"system-ui,sans-serif;margin:16px;max-width:760px;background:#fff;color:#1c2127}}@media (prefers-color-scheme: dark)"
            f"{{body{{background:#1c2127;color:#f6f7f9}}}}.b{{padding:6px 10px;background:#252a31;color:#f6f7f9}}</style></head>"
            f"<body><div class=\"b\">{e(settings.build_label)} {e(CAVEAT_SHORT)}</div><h1>SCS Vessel Watch</h1>"
            f"<p>The frontend is not built. Build it with <code>npm run build</code> in <code>app/frontend/</code>; the API "
            f"answers at <a href=\"{API}/meta\">{API}/meta</a> and its schema at <a href=\"/openapi.json\">/openapi.json</a>.</p>"
            f"<p>{e(settings.caveat())}</p><div class=\"b\">{e(settings.build_label)} {e(CAVEAT_SHORT)}</div></body></html>")


def create_app(settings: Settings, store: Store | None = None) -> FastAPI:
    store = store or Store(settings)

    @asynccontextmanager
    async def lifespan(app):
        """Release the held background work background_delay_s after startup (uvicorn binds the port right after it)."""
        if settings.background_delay_s is not None:
            asyncio.get_running_loop().call_later(settings.background_delay_s, store.release_background)
        yield

    app = FastAPI(title="SCS Vessel Watch API", version=APP_VERSION, docs_url=None, redoc_url=None,
                  openapi_url="/openapi.json", lifespan=lifespan,
                  description=f"Local backend of SCS Vessel Watch, data contract {CONTRACT_VERSION}, {settings.build} build. "
                              + PRODUCT_CAVEAT)
    app.state.store = store
    app.state.settings = settings

    async def fresh():
        """Check for changed files (in a worker thread, so a reload never blocks the event loop), then pin the current
        State for this request: every store call of the request, and a streamed export, reads the same data."""
        if store.check_due():
            await run_in_threadpool(store.ensure_fresh)
        store.pin()

    deps = [Depends(fresh)]
    # Contract section 5 order. cells registers /cells/at before /cells/{cell_id} (contract test h).
    for mod in (meta, layers, contacts, vessels, lights, events, leads, passes, cells, rasters, geo, search, timeline, export):
        app.include_router(mod.router(store), prefix=API, dependencies=deps)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(settings, exc.status_code, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error_response(settings, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        msgs = "; ".join(f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg')}" for e in exc.errors())
        return error_response(settings, 422, "validation_error", msgs)

    @app.exception_handler(ResearchPathError)
    async def _guard(request: Request, exc: ResearchPathError):
        return error_response(settings, 500, "build_guard", str(exc))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        """Any other error still answers with the error envelope, the build and the caveat (contract section 5); the
        server log keeps the traceback."""
        return error_response(settings, 500, "internal_error",
                              f"The backend could not answer this request ({type(exc).__name__}); the server log has the details.")

    dist = settings.frontend_dist
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def index():
            return HTMLResponse(not_built_page(settings))

    return app
