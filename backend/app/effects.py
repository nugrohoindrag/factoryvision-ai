from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app import actions, plant
from app.store import Doc, store

DEMAND_CHANGE_PCT: dict[str, dict[str, float]] = {
    "disruption_response": {"FB-ARKA": 40},
    "demand_spike": {"FB-ARKA": 40},
}
DEFAULT_PLANNING_UTILIZATION_PCT = 80.0
PLANNING_SETTINGS = "planning_settings"
VENDOR_EXTRA_DELAY: dict[str, dict[str, float]] = {
    "disruption_response": {"VEN-ANDALAS": 3},
    "vendor_late": {"VEN-ANDALAS": 3},
}


@dataclass
class Overrides:
    scenario: str = "baseline"
    demand_change_pct: dict[str, float] = field(default_factory=dict)
    vendor_extra_delay: dict[str, float] = field(default_factory=dict)
    maintenance: list[str] = field(default_factory=list)
    mold_moves: dict[str, str] = field(default_factory=dict)
    extra_shifts: dict[str, int] = field(default_factory=dict)
    extra_receipts: list[dict[str, Any]] = field(default_factory=list)
    holds: list[str] = field(default_factory=list)
    ship_dates: dict[str, str] = field(default_factory=dict)
    notices: list[dict[str, Any]] = field(default_factory=list)
    customer_pos: list[dict[str, Any]] = field(default_factory=list)
    new_orders: list[dict[str, Any]] = field(default_factory=list)
    cancelled_orders: dict[str, str] = field(default_factory=dict)
    applied: list[dict[str, Any]] = field(default_factory=list)
    planning_utilization_pct: float = DEFAULT_PLANNING_UTILIZATION_PCT
    strict_process_sequence: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def session_doc(session_id: str) -> Doc:
    return store().get("sessions", session_id) or {"session_id": session_id, "scenario": "baseline"}


def set_scenario(session_id: str, scenario: str) -> None:
    store().update("sessions", session_id, {"session_id": session_id, "scenario": scenario})


def planning_setting(session_id: str) -> Doc:
    return store().get(PLANNING_SETTINGS, session_id) or {}


def overrides(session_id: str) -> Overrides:
    sc = str(session_doc(session_id).get("scenario", "baseline"))
    setting = planning_setting(session_id)
    util = float(setting.get("planning_utilization_pct", DEFAULT_PLANNING_UTILIZATION_PCT))
    o = Overrides(
        scenario=sc,
        demand_change_pct=dict(DEMAND_CHANGE_PCT.get(sc, {})),
        vendor_extra_delay=dict(VENDOR_EXTRA_DELAY.get(sc, {})),
        planning_utilization_pct=util,
        strict_process_sequence=bool(setting.get("strict_process_sequence", False)),
    )
    for cmd in actions.approved_commands(session_id):
        apply(o, cmd)
    return o


def apply(o: Overrides, cmd: Doc) -> None:
    name, a = cmd["name"], cmd.get("args", {})
    if name == "apply_customer_po":
        for line in a.get("lines", []):
            if line.get("change_pct"):
                o.demand_change_pct[line["sku"]] = float(line["change_pct"])
        o.customer_pos.append(a)
    elif name == "create_customer_order":
        o.new_orders.append(a["order"])
    elif name == "cancel_customer_order":
        o.cancelled_orders[a["order_number"]] = a["reason"]
    elif name == "start_maintenance":
        o.maintenance.append(a["machine_id"])
    elif name == "move_mold":
        o.mold_moves[a["sku"]] = a["to_machine_id"]
    elif name == "add_shift":
        for mid in a.get("machine_ids", []):
            o.extra_shifts[mid] = o.extra_shifts.get(mid, 0) + int(a.get("days", 4))
    elif name == "place_po":
        o.extra_receipts.append(
            {
                "po_id": a.get("po_id", cmd.get("action_id")),
                "expected_date": a["expected_date"],
                "qty": float(a["qty"]),
                "vendor_id": a.get("vendor_id"),
                "material_id": a.get("material_id", "PP-EPDM-T20"),
            }
        )
    elif name == "hold_lot":
        o.holds.append(a["resin_lot_id"])
    elif name == "revise_ship_date":
        o.ship_dates[a["order_id"]] = a["new_date"]
    elif name == "notify_customer":
        o.notices.append(a)
    o.applied.append({"name": name, "action_id": cmd.get("action_id")})


def normal_cycle_factor(rates: dict[str, dict[str, Any]]) -> float:
    others = [float(rates[m]["cycle_factor"]) for m in plant.IMM_IDS if m != "IMM-04" and m in rates]
    return sum(others) / len(others) if others else 1.03
