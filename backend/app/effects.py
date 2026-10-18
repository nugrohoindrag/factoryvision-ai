from __future__ import annotations

from dataclasses import dataclass, field

from app import actions
from app.store import store
from simulator import plant as P

SCENARIOS = {
    "baseline": {},
    "disruption_response": {"demand_change_pct": {"FB-ARKA": 40}, "vendor_extra_delay": {"VEN-ANDALAS": 3}, "degrade": ["IMM-04"]},
    "demand_spike": {"demand_change_pct": {"FB-ARKA": 40}},
    "vendor_late": {"vendor_extra_delay": {"VEN-ANDALAS": 3}},
    "defect_spike": {"defect_lot": "LOT-PPT20-0918"},
}


@dataclass
class Overrides:
    scenario: str = "baseline"
    demand_change_pct: dict = field(default_factory=dict)
    vendor_extra_delay: dict = field(default_factory=dict)
    maintenance: list = field(default_factory=list)
    mold_moves: dict = field(default_factory=dict)
    extra_shifts: dict = field(default_factory=dict)
    extra_receipts: list = field(default_factory=list)
    holds: list = field(default_factory=list)
    ship_dates: dict = field(default_factory=dict)
    notices: list = field(default_factory=list)
    applied: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def session_doc(session_id: str) -> dict:
    return store().get("sessions", session_id) or {"session_id": session_id, "scenario": "baseline"}


def set_scenario(session_id: str, scenario: str) -> None:
    store().update("sessions", session_id, {"session_id": session_id, "scenario": scenario})


def overrides(session_id: str) -> Overrides:
    sc = session_doc(session_id).get("scenario", "baseline")
    o = Overrides(scenario=sc, **{k: dict(v) if isinstance(v, dict) else v for k, v in SCENARIOS.get(sc, {}).items() if k in ("demand_change_pct", "vendor_extra_delay")})
    for cmd in actions.approved_commands(session_id):
        apply(o, cmd)
    return o


def apply(o: Overrides, cmd: dict) -> None:
    name, a = cmd["name"], cmd.get("args", {})
    if name == "start_maintenance":
        o.maintenance.append(a["machine_id"])
    elif name == "move_mold":
        o.mold_moves[a["sku"]] = a["to_machine_id"]
    elif name == "add_shift":
        for mid in a.get("machine_ids", []):
            o.extra_shifts[mid] = o.extra_shifts.get(mid, 0) + int(a.get("days", 4))
    elif name in ("place_po", "expedite_po"):
        o.extra_receipts.append({"po_id": a.get("po_id", cmd.get("action_id")), "expected_date": a["expected_date"], "qty": float(a["qty"]),
                                 "vendor_id": a.get("vendor_id"), "material_id": a.get("material_id", "PP-EPDM-T20")})
    elif name == "hold_lot":
        o.holds.append(a["resin_lot_id"])
    elif name == "revise_ship_date":
        o.ship_dates[a["order_id"]] = a["new_date"]
    elif name == "notify_customer":
        o.notices.append(a)
    o.applied.append({"name": name, "action_id": cmd.get("action_id")})


def normal_cycle_factor(rates: dict) -> float:
    others = [rates[m]["cycle_factor"] for m in P.IMM_IDS if m != "IMM-04" and m in rates]
    return sum(others) / len(others) if others else 1.03
