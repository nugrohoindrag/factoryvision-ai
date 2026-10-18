# Demand Forecast snapshots (module 1): generate, list, open, compare with orders, and check accuracy.
# A person generates a forecast here, so the snapshot is stored straight away; agents only read snapshots via app.forecasts.

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import forecasts
from app.errors import FVError
from app.session import SessionId
from app.tools import orders

router = APIRouter(prefix="/api/forecasts")


class GenerateIn(BaseModel):
    # Loose types on purpose: app.forecasts.validate reports every bad field with a catalog code, not pydantic's generic one.
    window_days: Any = 90
    horizon_weeks: Any = 6
    skus: Any = None
    per_customer: Any = False


def _user(request: Request) -> str:
    user: dict[str, Any] = getattr(request.state, "user", None) or {}
    return str(user.get("email") or "You")


@router.post("")
def generate(body: GenerateIn, request: Request, sid: SessionId) -> dict[str, Any]:
    return forecasts.generate(
        sid, window_days=body.window_days, horizon_weeks=body.horizon_weeks, skus=body.skus, per_customer=body.per_customer, user=_user(request)
    )


@router.get("")
def list_snapshots(sid: SessionId, status: str | None = None) -> dict[str, Any]:
    return {
        "snapshots": forecasts.list_snapshots(sid, status),
        "statuses": list(forecasts.STATUSES),
        "windows": list(forecasts.WINDOWS),
        "horizons": list(forecasts.HORIZONS),
        "products": forecasts.products(),
        "customers": [{"customer_id": c["customer_id"], "customer_name": c["customer_name"]} for c in orders.customers()],
        "rules": forecasts.RULES,
    }


@router.get("/accuracy")
def accuracy(sid: SessionId, sku: str | None = None) -> dict[str, Any]:
    return forecasts.accuracy(sid, sku)


@router.get("/activity")
def activity(sid: SessionId) -> dict[str, Any]:
    return {"activity": forecasts.activity(sid)}


@router.get("/{forecast_id}")
def get_snapshot(forecast_id: str, sid: SessionId) -> dict[str, Any]:
    return forecasts.get_snapshot(sid, forecast_id)


@router.get("/{forecast_id}/comparison")
def comparison(forecast_id: str, sid: SessionId) -> dict[str, Any]:
    return forecasts.comparison(sid, forecast_id)


@router.get("/{forecast_id}/vs-orders")
def vs_orders(forecast_id: str, sid: SessionId, sku: str = "FB-ARKA") -> dict[str, Any]:
    return forecasts.vs_orders(sid, forecast_id, sku)


@router.api_route("/{forecast_id}", methods=["PUT", "PATCH", "DELETE"])
def read_only(forecast_id: str, sid: SessionId) -> dict[str, Any]:
    forecasts.get_snapshot(sid, forecast_id)
    raise FVError(forecasts.READ_ONLY, f"{forecast_id} is a stored snapshot and never changes.", {"forecast_id": forecast_id})
