"""fv-app: FastAPI entry point. Run (from backend/): uvicorn app.main:app --reload --port 8080"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.errors import FVError

log = logging.getLogger("fv")

app = FastAPI(title="Factory Vision AI", version="0.1.0")


@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


@app.exception_handler(FVError)
async def fv_error(request: Request, exc: FVError):
    return JSONResponse(exc.body(), status_code=exc.http_status)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    err = FVError("FV-REQ-001", detail=[{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()])
    return JSONResponse(err.body(), status_code=err.http_status)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    code = "FV-REQ-002" if exc.status_code == 404 else "FV-REQ-001"
    err = FVError(code, message=str(exc.detail))
    return JSONResponse(err.body(), status_code=exc.status_code)


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    log.exception("unhandled error", extra={"request_id": getattr(request.state, "request_id", None)})
    err = FVError("FV-SYS-001", detail={"request_id": getattr(request.state, "request_id", None)})
    return JSONResponse(err.body(), status_code=500)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "warehouse": settings.warehouse, "models": {
        "orchestrator": settings.model_orchestrator, "module": settings.model_module, "vision": settings.model_vision}}
