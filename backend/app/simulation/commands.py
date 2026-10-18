from __future__ import annotations

from typing import TYPE_CHECKING

from app.errors import FVError
from app import plant

if TYPE_CHECKING:
    from app.simulation.engine import State

MAINTENANCE_MIN = 120


def execute(s: "State", command: dict) -> dict:
    name, a = command.get("name"), command.get("args", {}) or {}
    fn = _COMMANDS.get(name)
    if fn is None:
        raise FVError("FV-TWIN-002", detail={"command": name, "valid": sorted(_COMMANDS)})
    return fn(s, a)


def _machine(s: "State", mid: str):
    if mid not in s.machines:
        raise FVError("FV-DATA-001", f"unknown machine {mid}")
    return s.machines[mid]


def start_maintenance(s: "State", a: dict) -> dict:
    m = _machine(s, a.get("machine_id", "IMM-04"))
    s.set_status(m, "Maintenance", "PLANNED_MAINTENANCE", timer=int(a.get("minutes", MAINTENANCE_MIN)))
    return {"machine_id": m.machine_id, "status": "Maintenance", "back_at_minute": s.minute + m.timer}


def move_mold(s: "State", a: dict) -> dict:
    sku, dst = a["sku"], _machine(s, a["to_machine_id"])
    src = next((m for m in s.machines.values() if sku in m.mix), None)
    if src is None or dst.machine_id not in plant.MOLD_COMPAT.get(sku, []):
        raise FVError("FV-DATA-001", f"mold {sku} cannot move to {dst.machine_id}", {"fits": plant.MOLD_COMPAT.get(sku)})
    if src is dst:
        return {"sku": sku, "machine_id": dst.machine_id, "note": "already mounted"}
    back = next(iter(dst.mix), None)
    if back and src.machine_id not in plant.MOLD_COMPAT.get(back, []):
        raise FVError("FV-DATA-001", f"mold {back} does not fit {src.machine_id}")
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


def hold_lot(s: "State", a: dict) -> dict:
    lot = a["resin_lot_id"]
    if lot not in s.holds:
        s.holds.append(lot)
    units = sum(u for racks in s.warehouse.values() for l, u in racks if l == lot)
    return {"resin_lot_id": lot, "units_on_hold": units}


def place_po(s: "State", a: dict) -> dict:
    from app.simulation.engine import RECEIPT_LEAD_MIN

    if a.get("material_id", "PP-EPDM-T20") != "PP-EPDM-T20":
        return {"logged": True}
    lot = f"LOT-PPT20-{s.next_lot:04d}"
    s.next_lot += 1
    at = s.minute + int(a.get("lead_minutes", RECEIPT_LEAD_MIN))
    s.receipts.append({"at": at, "qty": float(a.get("qty", 0)), "lot": lot, "po_id": a.get("po_id")})
    return {"arrives_at_minute": at, "lot": lot, "qty_kg": float(a.get("qty", 0))}


def logged(s: "State", a: dict) -> dict:
    return {"logged": True}


_COMMANDS = {
    "start_maintenance": start_maintenance, "move_mold": move_mold, "hold_lot": hold_lot,
    "place_po": place_po, "expedite_po": place_po,
    "add_shift": logged, "revise_ship_date": logged, "notify_customer": logged,
}
