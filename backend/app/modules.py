from __future__ import annotations

import json
from functools import lru_cache

from app.effects import Overrides, normal_cycle_factor
from app.errors import FVError
from app.tools import delivery, forecast, material, mes, planning, purchasing, quality
from simulator import plant as P

MODULES = ("forecast", "planning", "mes", "material", "purchasing", "quality", "delivery")


def _maintenance_cycle(o: Overrides) -> dict:
    if not o.maintenance:
        return {}
    rates = planning.machine_rates()
    return {mid: normal_cycle_factor(rates) for mid in o.maintenance if mid in rates}


def delivery_rates(o: Overrides) -> dict:
    molds = planning.current_molds()
    rates = planning.machine_rates()
    normal = normal_cycle_factor(rates)
    factor = {mid: (normal if mid in o.maintenance else r["cycle_factor"]) for mid, r in rates.items()}
    out = {}
    for sku, src in molds.items():
        dst = o.mold_moves.get(sku, src)
        if dst in factor and (dst != src or src in o.maintenance):
            out[sku] = delivery.output_rate(src) * rates[src]["cycle_factor"] / factor[dst]
    return out


def _forecast(o: Overrides) -> dict:
    main = forecast.bq_forecast("FB-ARKA")
    return {"skus": forecast.forecast_all(), "focus": main, "customer_po_change_pct": o.demand_change_pct}


def _planning(o: Overrides) -> dict:
    cyc = _maintenance_cycle(o)
    base = planning.get_capacity()
    now = planning.get_capacity(demand_change_pct=o.demand_change_pct or None, cycle_override=cyc or None, extra_days=o.extra_shifts or None)
    return {"baseline": base, "current": now, "mold_moves": o.mold_moves, "what_if_options": list(planning.WHAT_IF)}


def _mes(o: Overrides) -> dict:
    health = mes.machine_health("IMM-04")
    fleet = mes.fleet_health()
    if "IMM-04" in o.maintenance:
        health = {**health, "breakdown_risk": "low", "after_maintenance": True, "note": "Maintenance approved: heater band and check ring replaced; cycle time back to normal."}
        fleet = [{**f, "breakdown_risk": "low"} if f["machine_id"] == "IMM-04" else f for f in fleet]
    return {"oee_yesterday": mes.get_oee(), "oee_7d": mes.get_oee(days=7), "pareto": mes.downtime_pareto(), "health": health, "fleet": fleet}


def _material(o: Overrides) -> dict:
    receipts = [r for r in o.extra_receipts if r.get("material_id", "PP-EPDM-T20") == "PP-EPDM-T20"]
    return {"requirements": material.material_requirements(demand_change_pct=o.demand_change_pct or None),
            "resin": material.stock_projection(demand_change_pct=o.demand_change_pct or None, extra_receipts=receipts or None)}


def _purchasing(o: Overrides) -> dict:
    proj = material.stock_projection(demand_change_pct=o.demand_change_pct or None)
    return {"scorecard": purchasing.vendor_performance(),
            "resin_po": purchasing.recommend_po(need_by=proj["stockout_date"], extra_delay=o.vendor_extra_delay or None),
            "placed": o.extra_receipts}


def _quality(o: Overrides) -> dict:
    corr = quality.defect_correlation()
    held = [quality.hold_candidates(lot) | {"status": "OPEN"} for lot in o.holds]
    return {"correlation": corr, "holds": held}


def _delivery(o: Overrides) -> dict:
    rates = delivery_rates(o)
    risk = delivery.orders_at_risk(rate_override=rates or None)
    base = delivery.orders_at_risk() if rates else risk
    for r in risk["orders_at_risk"]:
        r["revised_ship_date"] = o.ship_dates.get(r["order_id"])
    return {"risk": risk, "baseline_count": base["count"], "rate_override": {k: round(v, 1) for k, v in rates.items()},
            "revised_ship_dates": o.ship_dates, "notices": o.notices}


@lru_cache(maxsize=16)
def _what_if(demand_key: str) -> str:
    return json.dumps(planning.what_if(demand_change_pct=json.loads(demand_key) or None))


def what_if(o: Overrides) -> dict:
    return json.loads(_what_if(json.dumps(o.demand_change_pct, sort_keys=True)))


_BUILDERS = {"forecast": _forecast, "planning": _planning, "mes": _mes, "material": _material,
             "purchasing": _purchasing, "quality": _quality, "delivery": _delivery}


@lru_cache(maxsize=256)
def _cached(module: str, key: str) -> str:
    d = json.loads(key)
    d.pop("applied", None)
    return json.dumps(_BUILDERS[module](Overrides(**d)), default=str)


def summary(module: str, o: Overrides | None = None) -> dict:
    if module not in _BUILDERS:
        raise FVError("FV-REQ-002", detail={"module": module, "valid": list(MODULES)})
    o = o or Overrides()
    key = json.dumps({**o.as_dict(), "applied": []}, sort_keys=True, default=str)
    return {"module": module, "scenario": o.scenario, "applied_actions": o.applied, **json.loads(_cached(module, key))}
