# shortcut: thin stand-in for app.mrp (owner: readiness); the integrator keeps the real module.
from __future__ import annotations

from datetime import timedelta
from typing import Any

from app import plant
from app.errors import FVError
from app.store import store

UNKNOWN_ENTITY = "FV-DATA-001"
COLLECTION = "mrp_runs"


def run_mrp(
    sid: str, *, horizon_start: str | None = None, horizon_end: str | None = None, plan_ids: list[str] | None = None, user: str = "You"
) -> dict[str, Any]:
    start = horizon_start or plant.DEMO_TODAY.isoformat()
    end = horizon_end or (plant.DEMO_TODAY + timedelta(days=30)).isoformat()
    n = len(list_runs(sid)) + 1
    run = {
        "run_id": f"MRP-2027-02-{n:03d}",
        "horizon_start": start,
        "horizon_end": end,
        "plan_ids": plan_ids or [],
        "created_at": start,
        "created_by": user,
        "total_materials": 0,
        "shortage_materials": 0,
        "results": [],
    }
    store().set(COLLECTION, f"{sid}:{run['run_id']}", {**run, "session_id": sid})
    return run


def list_runs(sid: str) -> list[dict[str, Any]]:
    return sorted(store().where(COLLECTION, "session_id", sid), key=lambda r: str(r["run_id"]), reverse=True)


def get_run(sid: str, run_id: str) -> dict[str, Any]:
    run = store().get(COLLECTION, f"{sid}:{run_id}")
    if not run:
        raise FVError(UNKNOWN_ENTITY, f"unknown MRP run {run_id}")
    return run


def latest_run(sid: str) -> dict[str, Any] | None:
    runs = list_runs(sid)
    return runs[0] if runs else None
