from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.errors import FVError

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint

log = logging.getLogger("fv")


def create_app() -> FastAPI:
    return FastAPI(title="Factory Vision AI", version="0.1.0")


app = create_app()


@app.middleware("http")
async def request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


@app.exception_handler(FVError)
async def fv_error(_request: Request, exc: FVError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.http_status)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    err = FVError("FV-REQ-001", detail=[{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()])
    return JSONResponse(err.body(), status_code=err.http_status)


@app.exception_handler(StarletteHTTPException)
async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "FV-REQ-004" if exc.status_code == 404 else "FV-REQ-001"
    err = FVError(code, message=str(exc.detail))
    return JSONResponse(err.body(), status_code=exc.status_code)


@app.exception_handler(Exception)
async def unexpected(request: Request, _exc: Exception) -> JSONResponse:
    log.error("unhandled error", exc_info=_exc, extra={"request_id": getattr(request.state, "request_id", None)})
    err = FVError("FV-SYS-001", detail={"request_id": getattr(request.state, "request_id", None)})
    return JSONResponse(err.body(), status_code=500)


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "warehouse": settings.warehouse,
        "models": {"orchestrator": settings.model_orchestrator, "module": settings.model_module, "vision": settings.model_vision},
    }
