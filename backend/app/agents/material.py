from __future__ import annotations

from datetime import timedelta
from typing import Any

from app import actions, effects, materials, mrp, plant, readiness
from app.errors import FVError
from app.tools.common import TODAY, idr
from app.tools.impact import impact_calculator

Doc = dict[str, Any]

MRP_MATERIAL_NOT_FOUND = "FV-AGT-010"
NOTHING_TO_ORDER = "FV-AGT-011"
NO_DEFAULT_VENDOR = "FV-AGT-012"

RESIN = "PP-EPDM-T20"
SHORT_KEYS = (
    "material_id",
    "material_name",
    "material_group",
    "color",
    "unit",
    "required_qty",
    "required_date",
    "available_qty",
    "shortage_qty",
    "status",
    "at_risk",
    "note",
)
MRP_KEYS = ("run_id", "horizon_start", "horizon_end", "plan_ids", "created_at", "created_by", "total_materials", "shortage_materials")
ZERO_IMPACT = {"rupiah_idr": 0, "scrap_kg": 0, "kwh": 0, "co2e_kg": 0, "breakdown": {}}


def check_view(check: Doc, *, saved: bool) -> Doc:
    mats = check.get("materials", [])
    return {
        **{k: check.get(k) for k in ("plan_id", "plan_number", "wo_id", "status", "readiness_pct", "required_date", "checked_at")},
        "material_count": len(mats),
        "ready_count": sum(1 for m in mats if m.get("status") == "READY"),
        "shortages": [{k: m.get(k) for k in SHORT_KEYS} for m in check.get("shortages", [])],
        "at_risk": [m.get("material_id") for m in mats if m.get("at_risk")],
        "saved_check": saved,
        "source": f"readiness check of {check.get('plan_number') or check.get('plan_id')} (BOM x plan vs stock and dated incoming)",
    }


def plan_check(sid: str, plan_id: str) -> Doc:
    saved = readiness.latest_check(sid, plan_id)
    if saved.get("status") != "NOT_CHECKED":
        return check_view(saved, saved=True)
    return check_view(readiness.check_plan(sid, plan_id, save=False), saved=False)


def latest_or_new_run(sid: str, user: str) -> tuple[Doc, bool]:
    run = mrp.latest_run(sid)
    if run is not None:
        return run, False
    return mrp.run_mrp(sid, user=user), True


def mrp_view(run: Doc, *, computed_now: bool) -> Doc:
    short = [r for r in run.get("results", []) if (r.get("net_qty") or 0) > 0]
    return {
        **{k: run.get(k) for k in MRP_KEYS},
        "computed_now": computed_now,
        "shortages": short[:10],
        "source": f"MRP run {run.get('run_id')} (gross = BOM x plans, net = gross - available)",
    }


def _n(v: object) -> str:
    return f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)


def po_impact(args: Doc, sid: str) -> Doc:
    if args["material_id"] != RESIN:
        return ZERO_IMPACT
    return impact_calculator("purchase_order", {**args, "demand_change_pct": effects.overrides(sid).demand_change_pct or None})


def pending_po(sid: str, material_id: str) -> Doc | None:
    return next(
        (a for a in actions.list_actions(sid, "pending") if a["kind"] == "purchase_order" and a["command"]["args"].get("material_id") == material_id), None
    )


def propose_po(sid: str, run: Doc, material_id: str, *, plan_id: str | None = None, extra_evidence: list[Doc] | None = None) -> tuple[Doc, bool]:
    row = next((r for r in run.get("results", []) if r.get("material_id") == material_id), None)
    if row is None:
        raise FVError(MRP_MATERIAL_NOT_FOUND, detail={"run_id": run.get("run_id"), "material_id": material_id})
    net = float(row.get("net_qty") or 0)
    if net <= 0:
        raise FVError(NOTHING_TO_ORDER, detail={"run_id": run.get("run_id"), "material_id": material_id, "net_qty": net})
    vendor_id = row.get("default_vendor_id")
    if vendor_id not in plant.VENDOR_BY_ID:
        raise FVError(NO_DEFAULT_VENDOR, detail={"material_id": material_id, "default_vendor_id": vendor_id})
    pending = pending_po(sid, material_id)
    if pending is not None:
        return pending, False
    mat = materials.get_material(material_id)
    vendor = plant.VENDOR_BY_ID[vendor_id]
    lead = int(row.get("lead_time_days") or mat.get("lead_time_days") or vendor["lead_time_days"])
    need = str(row["required_date"])
    expected = max(need, (TODAY + timedelta(days=lead)).isoformat())
    unit, unit_cost = row.get("unit") or mat.get("unit"), float(mat.get("unit_cost_idr") or 0)
    value = idr(net * unit_cost)
    args = {"vendor_id": vendor_id, "material_id": material_id, "qty": net, "expected_date": expected, "unit": unit, "mrp_run_id": run["run_id"]}
    colour = f" ({row['color']})" if row.get("color") else ""
    notes = [f"order-by date {row.get('order_by_date')} has passed"] if str(row.get("order_by_date")) < TODAY.isoformat() else []
    if expected > need:
        notes.append(f"arrives after need date {need}")
    rationale = (
        f"MRP {run['run_id']}: {row.get('material_name') or mat.get('material_name')}{colour} needs {_n(row.get('gross_qty'))} {unit}, "
        f"{_n(row.get('available_qty'))} available, net {_n(net)} {unit} by {need}; order by {row.get('order_by_date')} "
        f"(lead time {lead} days). Default vendor {vendor['vendor_name']}, expected {expected}, PO value Rp{value:,}."
        + (f" Note: {'; '.join(notes)}." if notes else "")
    )
    evidence = [
        {"source": f"MRP run {run['run_id']}", "mrp_result": row},
        {"source": "material master", "unit_cost_idr": unit_cost, "po_value_idr": value, "lead_time_days": lead},
        *(extra_evidence or []),
    ]
    impact = po_impact(args, sid)
    title = f"PO {material_id}{colour} {_n(net)} {unit} from {vendor['vendor_name']}"
    return actions.propose(sid, "purchase_order", title, "purchasing", rationale, args, evidence, impact, plan_id), True
