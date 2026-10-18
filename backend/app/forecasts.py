from __future__ import annotations

# shortcut: thin stand-in for the real module on wt/forecast (same signatures as fv-wt/CONTRACT.md).
# The integrator keeps the wt/forecast version; this one exists so wt/agentic imports and runs on its own.
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app import plant
from app.errors import FVError
from app.store import Doc, store
from app.tools import forecast
from app.tools.common import TODAY

COLLECTION = "forecast_snapshots"
UNKNOWN_ENTITY = "FV-DATA-001"


def _key(sid: str, forecast_id: str) -> str:
    return f"{sid}:{forecast_id}"


def _snapshots(sid: str) -> list[Doc]:
    return sorted(store().where(COLLECTION, "session_id", sid), key=lambda d: str(d["forecast_id"]), reverse=True)


def generate(
    sid: str, *, window_days: int = 90, horizon_weeks: int = 6, skus: list[str] | None = None, per_customer: bool = False, user: str = "You"
) -> dict[str, Any]:
    chosen = skus or list(plant.SKUS)
    lines: list[Doc] = []
    history: dict[str, list[Doc]] = {}
    engine = ""
    prefix = f"FC-{TODAY:%Y-%m}-"
    fid = f"{prefix}{sum(1 for d in _snapshots(sid) if str(d['forecast_id']).startswith(prefix)) + 1:03d}"
    for sku in chosen:
        f = forecast.bq_forecast(sku, horizon_weeks)
        if "error" in f:
            raise FVError(UNKNOWN_ENTITY, f"unknown sku {sku}", {"valid_skus": plant.SKUS})
        engine = f["engine"]
        hist = f["weekly_history"]
        with_orders = sum(1 for h in hist if h["total_qty"] > 0)
        history[sku] = [{"week_start": h["week_start"], "qty": h["total_qty"]} for h in hist]
        lines += [
            {
                "forecast_line_id": f"{fid}-{sku}-{w['week_start']}",
                "sku": sku,
                "customer_id": None,
                "week_start": w["week_start"],
                "forecast_qty": w["total_qty"],
                "history_weeks_with_orders": with_orders,
                "history_weeks": len(hist),
                "insufficient_history": with_orders < len(hist),
            }
            for w in f["weekly_forecast"]
        ]
    for prev in _snapshots(sid):
        if prev["status"] == "Generated" and (prev["window_days"], prev["horizon_weeks"], prev["skus"]) == (window_days, horizon_weeks, chosen):
            store().update(COLLECTION, _key(sid, prev["forecast_id"]), {"status": "Superseded", "superseded_by": fid})
    doc: Doc = {
        "forecast_id": fid,
        "session_id": sid,
        "status": "Generated",
        "superseded_by": None,
        "window_days": window_days,
        "horizon_weeks": horizon_weeks,
        "skus": chosen,
        "per_customer": per_customer,
        "engine": engine,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": user,
        "period_start": TODAY.isoformat(),
        "period_end": (TODAY + timedelta(days=7 * horizon_weeks - 1)).isoformat(),
        "lines": lines,
        "history": history,
    }
    store().set(COLLECTION, _key(sid, fid), doc)
    return doc


def list_snapshots(sid: str, status: str | None = None) -> list[dict[str, Any]]:
    if not _snapshots(sid):
        generate(sid, user="seed")
    return [d for d in _snapshots(sid) if status is None or d["status"] == status]


def get_snapshot(sid: str, forecast_id: str) -> dict[str, Any]:
    doc = store().get(COLLECTION, _key(sid, forecast_id))
    if not doc or doc["session_id"] != sid:
        raise FVError(UNKNOWN_ENTITY, f"unknown forecast snapshot {forecast_id}")
    used = sorted(p["plan_number"] for p in store().where("production_plans", "session_id", sid) if p.get("forecast_id") == forecast_id)
    return {**doc, "used_by_plans": used}


def latest(sid: str) -> dict[str, Any]:
    return list_snapshots(sid, "Generated")[0]


def qty_for(snapshot: dict[str, Any], sku: str, start: str, end: str) -> tuple[int, list[str]]:
    lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    hits = [
        ln
        for ln in snapshot["lines"]
        if ln["sku"] == sku and date.fromisoformat(ln["week_start"]) <= hi and date.fromisoformat(ln["week_start"]) + timedelta(days=6) >= lo
    ]
    return sum(int(ln["forecast_qty"]) for ln in hits), [ln["forecast_line_id"] for ln in hits]


def clear(sid: str) -> None:
    for d in store().where(COLLECTION, "session_id", sid):
        store().delete(COLLECTION, _key(sid, d["forecast_id"]))
