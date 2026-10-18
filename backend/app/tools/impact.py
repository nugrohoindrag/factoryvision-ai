from __future__ import annotations

from app.tools import delivery, material, mes, planning
from app.tools.common import idr
from app import plant

HORIZON_WORKDAYS = 24
BREAKDOWN_HOURS = 24
BREAKDOWN_PROB = 0.7
MAINTENANCE_PARTS_IDR = 18_000_000
MAINTENANCE_HOURS = 2
IDLE_KW_SHARE = 0.35
SPOT_BUY_DAYS = 3


def _result(rupiah: float, scrap_kg: float = 0, kwh: float = 0, co2e_kg: float | None = None, breakdown: dict | None = None, assumptions: dict | None = None) -> dict:
    return {"rupiah_idr": idr(rupiah), "scrap_kg": round(scrap_kg), "kwh": round(kwh),
            "co2e_kg": round(co2e_kg if co2e_kg is not None else kwh * plant.GRID_KG_CO2_PER_KWH),
            "breakdown": {k: idr(v) if isinstance(v, (int, float)) else v for k, v in (breakdown or {}).items()}, "assumptions": assumptions or {}}


def maintenance(machine_id: str = "IMM-04") -> dict:
    h = mes.machine_health(machine_id)
    rates = planning.machine_rates()
    normal = sum(rates[m]["cycle_factor"] for m in plant.IMM_IDS if m != machine_id) / (len(plant.IMM_IDS) - 1)
    ideal = h["ideal_cycle_s"]
    cur, new = h["current_cycle_s"], ideal * normal
    sku = planning.current_molds_by_machine().get(machine_id)
    p = plant.PRODUCT_BY_SKU[sku]
    shots_month = HORIZON_WORKDAYS * 3 * 450 * 60 / cur
    gained_units = HORIZON_WORKDAYS * 3 * 450 * 60 * (1 / new - 1 / cur)
    prob = BREAKDOWN_PROB if h["breakdown_risk"] == "high" else 0.3 if h["breakdown_risk"] == "medium" else 0.05
    avoided = prob * BREAKDOWN_HOURS * plant.DOWNTIME_IDR_PER_HOUR_IMM
    speed = gained_units * p.price_idr * plant.CONTRIBUTION_MARGIN
    cost = MAINTENANCE_HOURS * plant.DOWNTIME_IDR_PER_HOUR_IMM + MAINTENANCE_PARTS_IDR
    scrap_drop = max(0.0, h["scrap_rate_last7_pct"] / 100 - 0.01)
    scrap_kg = shots_month * scrap_drop * p.shot_weight_kg
    kw = plant.MACHINE_BY_ID[machine_id].kw_rated * IDLE_KW_SHARE
    kwh = kw * (cur - new) / 3600 * shots_month + scrap_kg * plant.KWH_PER_KG_MOLDED
    return _result(avoided + speed - cost + scrap_kg * plant.MATERIAL_BY_ID["PP-EPDM-T20"].unit_cost_idr, scrap_kg, kwh,
                   breakdown={"avoided_breakdown_idr": avoided, "speed_recovered_idr": speed, "maintenance_cost_idr": -cost},
                   assumptions={"cycle_now_s": round(cur, 1), "cycle_after_s": round(new, 1), "breakdown_probability": prob, "breakdown_hours": BREAKDOWN_HOURS,
                                "downtime_idr_per_hour": plant.DOWNTIME_IDR_PER_HOUR_IMM, "horizon_workdays": HORIZON_WORKDAYS})


def mold_move(sku: str = "FB-ARKA", to_machine_id: str = "IMM-05") -> dict:
    molds = planning.current_molds()
    src = molds[sku]
    rates = planning.machine_rates()
    gain = delivery.output_rate(src) * (rates[src]["cycle_factor"] / rates[to_machine_id]["cycle_factor"] - 1) * HORIZON_WORKDAYS
    p = plant.PRODUCT_BY_SKU[sku]
    cost = 2 * plant.CHANGEOVER_MINUTES / 60 * plant.DOWNTIME_IDR_PER_HOUR_IMM
    return _result(gain * p.price_idr * plant.CONTRIBUTION_MARGIN - cost, 0, 0,
                   breakdown={"extra_units_contribution_idr": gain * p.price_idr * plant.CONTRIBUTION_MARGIN, "changeover_cost_idr": -cost},
                   assumptions={"extra_units": round(gain), "changeovers": 2, "changeover_minutes": plant.CHANGEOVER_MINUTES})


def purchase_order(vendor_id: str, qty: float, expected_date: str, material_id: str = "PP-EPDM-T20", demand_change_pct: dict | None = None) -> dict:
    proj = material.stock_projection(material_id, demand_change_pct=demand_change_pct)
    after = material.stock_projection(material_id, demand_change_pct=demand_change_pct, extra_receipts=[{"expected_date": expected_date, "qty": qty}])
    horizon = 21
    lost_days = max(0, horizon - (proj["days_to_stockout"] or horizon)) - max(0, horizon - (after["days_to_stockout"] or horizon))
    lost_days = min(lost_days, SPOT_BUY_DAYS)
    avoided = lost_days * 6 / 7 * 24 * 6 * plant.DOWNTIME_IDR_PER_HOUR_IMM
    cheapest = min(v["unit_price_idr"] for v in plant.VENDORS if v["vendor_id"] in plant.MATERIAL_VENDORS[material_id])
    premium = (plant.VENDOR_BY_ID[vendor_id]["unit_price_idr"] - cheapest) * qty
    return _result(avoided - premium, 0, 0, breakdown={"avoided_line_stop_idr": avoided, "price_premium_idr": -premium},
                   assumptions={"stockout_days_avoided": lost_days, "days_to_stockout_before": proj["days_to_stockout"],
                                "days_to_stockout_after": after["days_to_stockout"], "imms_stopped": 6})


def quality_hold(resin_lot_id: str) -> dict:
    from app.tools.quality import defect_correlation, hold_candidates

    c = defect_correlation()
    held = hold_candidates(resin_lot_id)
    lift = [f for f in c["lot_findings"] if f["resin_lot_id"] == resin_lot_id]
    excess = sum(f["rate_pct"] - f["overall_rate_pct"] for f in lift) / 100
    escapes = held["units_in_stock"] * excess
    claim = escapes * 2_500_000
    return _result(claim, 0, 0, breakdown={"avoided_customer_claims_idr": claim},
                   assumptions={"units_held": held["units_in_stock"], "expected_defective": round(escapes), "claim_idr_per_unit": 2_500_000})


def ship_date(order_ids: list[str]) -> dict:
    risk = {o["order_id"]: o for o in delivery.orders_at_risk()["orders_at_risk"]}
    pen = sum(risk[o]["penalty_idr"] for o in order_ids if o in risk)
    return _result(pen * 0.5, 0, 0, breakdown={"penalty_reduction_idr": pen * 0.5}, assumptions={"penalty_exposure_idr": pen, "early_notice_discount": 0.5})


def impact_calculator(kind: str, args: dict) -> dict:
    if kind == "maintenance":
        return maintenance(args.get("machine_id", "IMM-04"))
    if kind == "mold_move":
        return mold_move(args.get("sku", "FB-ARKA"), args.get("to_machine_id", "IMM-05"))
    if kind in ("purchase_order", "expedite_po"):
        return purchase_order(args.get("vendor_id", "VEN-MERAPI"), float(args.get("qty", 160_000)), args["expected_date"], args.get("material_id", "PP-EPDM-T20"),
                              args.get("demand_change_pct"))
    if kind == "quality_hold":
        return quality_hold(args["resin_lot_id"])
    if kind == "ship_date":
        return ship_date(args.get("order_ids") or [args["order_id"]])
    if kind == "extra_shift":
        days = int(args.get("days", 4)) * len(args.get("machine_ids", []))
        return _result(-days * 3 * plant.OVERTIME_IDR_PER_SHIFT, 0, -days * 3 * 450 / 60 * 340 * 0.6,
                       assumptions={"extra_machine_days": days, "overtime_idr_per_shift": plant.OVERTIME_IDR_PER_SHIFT})
    return _result(0)


def plan_total(impacts: list[dict]) -> dict:
    keys = ("rupiah_idr", "scrap_kg", "kwh", "co2e_kg")
    return {k: sum(i.get(k) or 0 for i in impacts) for k in keys}
