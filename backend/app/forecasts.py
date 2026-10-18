from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app import plant
from app.errors import FVError
from app.store import store
from app.tools import forecast
from app.tools.common import TODAY, to_date

# Stand-in for the forecast branch's module: same signatures, just enough for Production Plan to run.
COLLECTION = "forecast_snapshots"
UNKNOWN_ENTITY = "FV-DATA-001"


def _key(sid: str, forecast_id: str) -> str:
    return f"{sid}/{forecast_id}"


def _all(sid: str) -> list[dict[str, Any]]:
    return sorted(store().where(COLLECTION, "session_id", sid), key=lambda d: (str(d["generated_at"]), d["forecast_id"]), reverse=True)


def generate(
    sid: str, *, window_days: int = 90, horizon_weeks: int = 6, skus: list[str] | None = None, per_customer: bool = False, user: str = "You"
) -> dict[str, Any]:
    prefix = f"FC-{TODAY:%Y-%m}"
    n = sum(1 for d in _all(sid) if d["forecast_id"].startswith(prefix)) + 1
    fid = f"{prefix}-{n:03d}"
    chosen = skus or plant.SKUS
    lines: list[dict[str, Any]] = [
        {
            "forecast_line_id": f"{fid}-{sku}-{w['week_start']}",
            "sku": sku,
            "customer_id": None,
            "week_start": w["week_start"],
            "forecast_qty": int(w["total_qty"]),
            "history_weeks_with_orders": None,
            "history_weeks": None,
            "insufficient_history": False,
        }
        for sku in chosen
        for w in forecast.bq_forecast(sku, horizon_weeks)["weekly_forecast"]
    ]
    for old in _all(sid):
        if old["status"] == "Generated":
            store().update(COLLECTION, _key(sid, old["forecast_id"]), {"status": "Superseded", "superseded_by": fid})
    doc: dict[str, Any] = {
        "forecast_id": fid,
        "session_id": sid,
        "status": "Generated",
        "superseded_by": None,
        "window_days": window_days,
        "horizon_weeks": horizon_weeks,
        "skus": chosen,
        "per_customer": per_customer,
        "engine": "local trend+weekday (dev)",
        "generated_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "generated_by": user,
        "period_start": TODAY.isoformat(),
        "period_end": (TODAY + timedelta(days=7 * horizon_weeks - 1)).isoformat(),
        "lines": lines,
        "history": {},
    }
    store().set(COLLECTION, _key(sid, fid), doc)
    return doc


def list_snapshots(sid: str, status: str | None = None) -> list[dict[str, Any]]:
    docs = _all(sid) or [generate(sid, user="seed")]
    return [d for d in docs if status is None or d["status"] == status]


def get_snapshot(sid: str, forecast_id: str) -> dict[str, Any]:
    doc = store().get(COLLECTION, _key(sid, forecast_id))
    if doc is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown forecast snapshot {forecast_id}")
    used = sorted(p["plan_number"] for p in store().where("production_plans", "session_id", sid) if p.get("forecast_id") == forecast_id)
    return {**doc, "used_by_plans": used}


def latest(sid: str) -> dict[str, Any]:
    return list_snapshots(sid, "Generated")[0]


def qty_for(snapshot: dict[str, Any], sku: str, start: str, end: str) -> tuple[int, list[str]]:
    lo, hi = to_date(start), to_date(end)
    hit = [ln for ln in snapshot["lines"] if ln["sku"] == sku and _overlaps(to_date(ln["week_start"]), lo, hi)]
    return sum(int(ln["forecast_qty"]) for ln in hit), [ln["forecast_line_id"] for ln in hit]


def _overlaps(week_start: date, lo: date, hi: date) -> bool:
    return week_start <= hi and week_start + timedelta(days=6) >= lo


def clear(sid: str) -> None:
    for d in store().where(COLLECTION, "session_id", sid):
        store().delete(COLLECTION, _key(sid, d["forecast_id"]))
