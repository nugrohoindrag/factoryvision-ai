from __future__ import annotations

# shortcut: thin stand-in for the real module on wt/plan-api (same signatures as fv-wt/CONTRACT.md).
# The integrator keeps the wt/plan-api version; this one exists so wt/agentic imports and runs on its own.
import uuid
from datetime import UTC, datetime
from typing import Any

from app import forecasts, plant
from app.errors import FVError
from app.store import Doc, store
from app.tools import planning
from app.tools.common import TODAY

COLLECTION = "production_plans"
UNKNOWN_ENTITY = "FV-DATA-001"
PERIOD_END = "2027-02-28"


def _gap_by_sku(cap: dict[str, Any]) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {}
    for g in cap["groups"]:
        for sku in plant.SKUS:
            if set(plant.MOLD_COMPAT.get(sku, [])) <= set(g["machines"]):
                out[sku] = (float(g["capacity_gap_pct"]), str(g["status"]))
    return out


def _capacity(sku: str, demand: int, gap_pct: float, status: str) -> Doc:
    total = round(demand / (1 + gap_pct / 100)) if demand else 0
    machines = plant.MOLD_COMPAT.get(sku, [])
    return {
        "total": total,
        "planning": round(total * 0.8),
        "buffer": total - round(total * 0.8),
        "gap": max(demand - total, 0),
        "utilization_pct": round(demand / total * 100, 1) if total else None,
        "status": status,
        "contributions": [{"machine_id": m, "available_minutes": None, "cycle_s": None, "capacity": total // len(machines)} for m in machines],
        "uncomputed": [],
    }


def _gap_pct(cap: Doc) -> float | None:
    return round((cap["utilization_pct"] - 100), 1) if cap.get("utilization_pct") is not None else None


def list_plans(sid: str, status: str | None = None) -> list[dict[str, Any]]:
    docs = sorted(store().where(COLLECTION, "session_id", sid), key=lambda d: str(d["created_at"]), reverse=True)
    return [d for d in docs if status is None or d["status"] == status]


def get_plan(sid: str, plan_id: str) -> dict[str, Any]:
    doc = store().get(COLLECTION, plan_id)
    if not doc or doc["session_id"] != sid:
        raise FVError(UNKNOWN_ENTITY, f"unknown production plan {plan_id}")
    return doc


def replan(sid: str, *, reason: str, forecast_id: str | None = None, demand_change_pct: dict[str, float] | None = None, user: str = "agent") -> dict[str, Any]:
    snap = forecasts.get_snapshot(sid, forecast_id) if forecast_id else forecasts.latest(sid)
    prev = next((p for p in list_plans(sid) if p["status"] != "Cancelled"), None)
    before = {ln["sku"]: _gap_pct(ln["capacity"]) for ln in prev["lines"]} if prev else {k: v[0] for k, v in _gap_by_sku(planning.get_capacity()).items()}
    cap = planning.get_capacity(demand_change_pct=demand_change_pct or None)
    after = _gap_by_sku(cap)
    lines: list[Doc] = []
    for sku, qty in cap["plan_qty"].items():
        fq, fids = forecasts.qty_for(snap, sku, TODAY.isoformat(), PERIOD_END)
        gap, status = after.get(sku, (0.0, "Within Plan"))
        lines.append(
            {
                "line_id": uuid.uuid4().hex[:12],
                "sku": sku,
                "product_name": f"{plant.PRODUCT_BY_SKU[sku].position} bumper {plant.PRODUCT_BY_SKU[sku].model}",
                "demand_qty": qty,
                "forecast_qty": fq,
                "forecast_line_ids": fids,
                "planned_qty": qty,
                "priority": 3,
                "required_date": PERIOD_END,
                "status": "Open",
                "capacity": _capacity(sku, qty, gap, status),
            }
        )
    month = f"PP-{TODAY:%Y-%m}"
    n = sum(1 for p in store().where(COLLECTION, "session_id", sid) if str(p["plan_number"]).startswith(month))
    doc: Doc = {
        "plan_id": str(uuid.uuid4()),
        "plan_number": month if n == 0 else f"{month}-{n + 1:03d}",
        "session_id": sid,
        "period_start": TODAY.isoformat(),
        "period_end": PERIOD_END,
        "status": "Draft",
        "utilization_pct": 80,
        "forecast_id": snap["forecast_id"],
        "demand_change_pct": demand_change_pct or {},
        "created_at": datetime.now(UTC).isoformat(),
        "created_by": user,
        "origin": "agent",
        "reason": reason,
        "lines": lines,
        "demand": [],
    }
    store().set(COLLECTION, doc["plan_id"], doc)
    delta = {ln["sku"]: {"before_gap_pct": before.get(ln["sku"]), "after_gap_pct": _gap_pct(ln["capacity"])} for ln in lines}
    return {**doc, "capacity_delta": delta}


def clear(sid: str) -> None:
    for d in store().where(COLLECTION, "session_id", sid):
        store().delete(COLLECTION, d["plan_id"])
