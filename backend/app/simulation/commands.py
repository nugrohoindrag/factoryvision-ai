from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app import plant
from app.errors import FVError

if TYPE_CHECKING:
    from app.simulation.engine import Machine, State

UNKNOWN_ENTITY = "FV-DATA-001"
UNKNOWN_COMMAND = "FV-TWIN-002"

MAINTENANCE_MIN = 120


def execute(s: State, command: dict[str, Any]) -> dict[str, Any]:
    name, a = str(command.get("name", "")), command.get("args", {}) or {}
    fn = _COMMANDS.get(name)
    if fn is None:
        raise FVError(UNKNOWN_COMMAND, detail={"command": name, "valid": sorted(_COMMANDS)})
    if name in _NEEDS_APPROVAL_META:
        a = {**a, "_action_id": command.get("action_id"), "_decided_by": command.get("decided_by")}
    return fn(s, a)


def _machine(s: State, mid: str) -> Machine:
    if mid not in s.machines:
        raise FVError(UNKNOWN_ENTITY, f"unknown machine {mid}")
    return s.machines[mid]


def start_maintenance(s: State, a: dict[str, Any]) -> dict[str, Any]:
    m = _machine(s, str(a.get("machine_id", "IMM-04")))
    s.set_status(m, "Maintenance", "PLANNED_MAINTENANCE", timer=int(a.get("minutes", MAINTENANCE_MIN)))
    return {"machine_id": m.machine_id, "status": "Maintenance", "back_at_minute": s.minute + m.timer}


def move_mold(s: State, a: dict[str, Any]) -> dict[str, Any]:
    sku, dst = a["sku"], _machine(s, a["to_machine_id"])
    src = next((m for m in s.machines.values() if sku in m.mix), None)
    if src is None or dst.machine_id not in plant.MOLD_COMPAT.get(sku, []):
        raise FVError(UNKNOWN_ENTITY, f"mold {sku} cannot move to {dst.machine_id}", {"fits": plant.MOLD_COMPAT.get(sku)})
    if src is dst:
        return {"sku": sku, "machine_id": dst.machine_id, "note": "already mounted"}
    back = next(iter(dst.mix), None)
    if back and src.machine_id not in plant.MOLD_COMPAT.get(back, []):
        raise FVError(UNKNOWN_ENTITY, f"mold {back} does not fit {src.machine_id}")
    for m, new_sku in ((dst, sku), (src, back)):
        if new_sku is None:
            m.pending_mix = {}
            continue
        m.pending_mix = {new_sku: 1.0}
        m.ideal_cycle_s = plant.PRODUCT_BY_SKU[new_sku].ideal_cycle_s
        ratio = (m.cycle_s / plant.PRODUCT_BY_SKU[next(iter(m.mix))].ideal_cycle_s) if m.cycle_s and m.mix else 1.058
        m.cycle_s = round(m.ideal_cycle_s * ratio, 1)
        if m.status == "Maintenance":
            m.timer = max(m.timer, plant.CHANGEOVER_MINUTES)
        else:
            s.set_status(m, "Setup", "CHANGEOVER", timer=plant.CHANGEOVER_MINUTES)
    return {"sku": sku, "from": src.machine_id, "to": dst.machine_id, "swapped_back": back, "running_at_minute": s.minute + plant.CHANGEOVER_MINUTES}


def hold_lot(s: State, a: dict[str, Any]) -> dict[str, Any]:
    lot = a["resin_lot_id"]
    if lot not in s.holds:
        s.holds.append(lot)
    units = sum(u for racks in s.warehouse.values() for rack_lot, u in racks if rack_lot == lot)
    return {"resin_lot_id": lot, "units_on_hold": units}


def place_po(s: State, a: dict[str, Any]) -> dict[str, Any]:
    from app.simulation.engine import RECEIPT_LEAD_MIN

    if a.get("material_id", "PP-EPDM-T20") != "PP-EPDM-T20":
        return {"logged": True}
    lot = f"LOT-PPT20-{s.next_lot:04d}"
    s.next_lot += 1
    at = s.minute + int(a.get("lead_minutes", RECEIPT_LEAD_MIN))
    s.receipts.append({"at": at, "qty": float(a.get("qty", 0)), "lot": lot, "po_id": a.get("po_id")})
    return {"arrives_at_minute": at, "lot": lot, "qty_kg": float(a.get("qty", 0))}


def create_replan(s: State, a: dict[str, Any]) -> dict[str, Any]:
    from app import forecasts, plans
    from app.store import store

    aid = a.get("_action_id")
    done = store().get("actions", str(aid)) if aid else None
    if done and done.get("status") == "approved" and (done.get("execution") or {}).get("ok"):
        prior: dict[str, Any] = done["execution"]["result"]
        return prior
    forecasts.list_snapshots(s.session_id)
    p = plans.replan(
        s.session_id,
        reason=str(a.get("reason", "")),
        forecast_id=a.get("forecast_id"),
        demand_change_pct=a.get("demand_change_pct") or None,
        user=str(a.get("_decided_by") or "agent"),
    )
    keys = ("plan_id", "plan_number", "status", "forecast_id", "capacity_delta")
    return {k: p.get(k) for k in keys}


def create_draft_plan(s: State, a: dict[str, Any]) -> dict[str, Any]:
    from app import plans
    from app.store import store

    aid = a.get("_action_id")
    done = store().get("actions", str(aid)) if aid else None
    if done and done.get("status") == "approved" and (done.get("execution") or {}).get("ok"):
        prior: dict[str, Any] = done["execution"]["result"]
        return prior
    args = {k: v for k, v in a.items() if not k.startswith("_")}
    return plans.materialize_draft(s.session_id, args, str(aid) if aid else None, str(a.get("_decided_by") or "") or None)


def logged(_s: State, _a: dict[str, Any]) -> dict[str, Any]:
    return {"logged": True}


_NEEDS_APPROVAL_META = {"create_replan", "create_draft_plan"}

_COMMANDS = {
    "start_maintenance": start_maintenance,
    "move_mold": move_mold,
    "hold_lot": hold_lot,
    "place_po": place_po,
    "expedite_po": place_po,
    "create_replan": create_replan,
    "create_draft_plan": create_draft_plan,
    "add_shift": logged,
    "apply_customer_po": logged,
    "create_customer_order": logged,
    "cancel_customer_order": logged,
    "revise_ship_date": logged,
    "notify_customer": logged,
}
