from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app import plant
from app.effects import Overrides, normal_cycle_factor
from app.errors import FVError
from app.tools import delivery, forecast, material, mes, orders, planning, purchasing, quality

UNKNOWN_MODULE = "FV-REQ-002"
UNKNOWN_ENTITY = "FV-DATA-001"

MODULES = ("forecast", "planning", "mes", "material", "purchasing", "quality", "delivery")
# Pages with a summary; Customer Orders (module 0) has no agent of its own.
PAGES = ("orders", *MODULES)


def _maintenance_cycle(o: Overrides) -> dict[str, Any]:
    if not o.maintenance:
        return {}
    rates = planning.machine_rates()
    return {mid: normal_cycle_factor(rates) for mid in o.maintenance if mid in rates}


def delivery_rates(o: Overrides) -> dict[str, Any]:
    molds = planning.current_molds()
    rates = planning.machine_rates()
    normal = normal_cycle_factor(rates)
    factor = {mid: (normal if mid in o.maintenance else r["cycle_factor"]) for mid, r in rates.items()}
    placed = dict(molds)
    for sku, dst in o.mold_moves.items():
        src = placed.get(sku)
        if src is None or src == dst:
            continue
        back = next((k for k, mid in placed.items() if mid == dst), None)
        placed[sku] = dst
        if back:
            placed[back] = src  # the displaced mold swaps onto the source machine, as commands.move_mold does
    out = {}
    for sku, src in molds.items():
        dst = placed[sku]
        if dst in factor and (dst != src or dst in o.maintenance):
            out[sku] = delivery.output_rate(src) * rates[src]["cycle_factor"] / factor[dst]
    return out


@lru_cache(maxsize=64)
def _order_view(key: str) -> str:
    o = Overrides(**json.loads(key))
    view = orders.order_view(
        rates=delivery_rates(o) or None, new_orders=o.new_orders, cancelled=o.cancelled_orders, mold_moves=o.mold_moves, plan_allocations=o.plan_allocations
    )
    return json.dumps(view, default=str)


def order_view(o: Overrides) -> dict[str, Any]:
    """Customer orders with lines, derived status, at-risk and work-order pegging; cached per session state."""
    keep = ("maintenance", "mold_moves", "new_orders", "cancelled_orders", "plan_allocations")
    key = json.dumps({k: v for k, v in o.as_dict().items() if k in keep}, sort_keys=True, default=str)
    result: dict[str, Any] = json.loads(_order_view(key))
    return result


def _orders(o: Overrides) -> dict[str, Any]:
    return {"book": orders.order_book(order_view(o)), "customer_pos": o.customer_pos, "customer_po_change_pct": o.demand_change_pct}


def _forecast(o: Overrides) -> dict[str, Any]:
    main = forecast.bq_forecast("FB-ARKA")
    return {
        "skus": forecast.forecast_all(),
        "focus": main,
        "vs_orders": forecast.forecast_vs_orders("FB-ARKA", o.demand_change_pct or None),
        "customer_po_change_pct": o.demand_change_pct,
    }


@lru_cache(maxsize=64)
def _forecast_detail(sku: str, demand_key: str) -> str:
    demand = json.loads(demand_key) or None
    return json.dumps({"focus": forecast.bq_forecast(sku), "vs_orders": forecast.forecast_vs_orders(sku, demand)}, default=str)


def forecast_detail(sku: str, o: Overrides) -> dict[str, Any]:
    if sku not in plant.PRODUCT_BY_SKU:
        raise FVError(UNKNOWN_ENTITY, f"unknown sku {sku}", {"valid_skus": plant.SKUS})
    result: dict[str, Any] = json.loads(_forecast_detail(sku, json.dumps(o.demand_change_pct, sort_keys=True)))
    return result


def _planning(o: Overrides) -> dict[str, Any]:
    cyc = _maintenance_cycle(o)
    base = planning.get_capacity()
    now = planning.get_capacity(demand_change_pct=o.demand_change_pct or None, cycle_override=cyc or None, extra_days=o.extra_shifts or None)
    return {"baseline": base, "current": now, "mold_moves": o.mold_moves, "what_if_options": list(planning.WHAT_IF)}


def _mes(o: Overrides) -> dict[str, Any]:
    health = mes.machine_health("IMM-04")
    fleet = mes.fleet_health()
    if "IMM-04" in o.maintenance:
        health = {
            **health,
            "breakdown_risk": "low",
            "after_maintenance": True,
            "note": "Maintenance approved: heater band and check ring replaced; cycle time back to normal.",
        }
        fleet = [{**f, "breakdown_risk": "low"} if f["machine_id"] == "IMM-04" else f for f in fleet]
    return {"oee_yesterday": mes.get_oee(), "oee_7d": mes.get_oee(days=7), "pareto": mes.downtime_pareto(), "health": health, "fleet": fleet}


def _material(o: Overrides) -> dict[str, Any]:
    receipts = [r for r in o.extra_receipts if r.get("material_id", "PP-EPDM-T20") == "PP-EPDM-T20"]
    return {
        "requirements": material.material_requirements(demand_change_pct=o.demand_change_pct or None),
        "resin": material.stock_projection(demand_change_pct=o.demand_change_pct or None, extra_receipts=receipts or None),
    }


def _purchasing(o: Overrides) -> dict[str, Any]:
    proj = material.stock_projection(demand_change_pct=o.demand_change_pct or None)
    return {
        "scorecard": purchasing.vendor_performance(),
        "resin_po": purchasing.recommend_po(need_by=proj["stockout_date"], extra_delay=o.vendor_extra_delay or None),
        "placed": o.extra_receipts,
    }


def _quality(o: Overrides) -> dict[str, Any]:
    corr = quality.defect_correlation()
    held = [quality.hold_candidates(lot) | {"status": "OPEN"} for lot in o.holds]
    return {"correlation": corr, "holds": held}


def _delivery(o: Overrides) -> dict[str, Any]:
    rates = delivery_rates(o)
    extra = orders.session_lines(o.new_orders)
    gone = [ln["order_id"] for h in order_view(o)["orders"] if h["order_number"] in o.cancelled_orders for ln in h["lines"]]
    risk = delivery.orders_at_risk(rate_override=rates or None, extra_orders=extra, cancelled=gone)
    base = delivery.orders_at_risk(extra_orders=extra, cancelled=gone) if rates else risk
    for r in risk["orders_at_risk"]:
        r["revised_ship_date"] = o.ship_dates.get(r["order_id"])
    return {
        "risk": risk,
        "baseline_count": base["count"],
        "rate_override": {k: round(v, 1) for k, v in rates.items()},
        "revised_ship_dates": o.ship_dates,
        "notices": o.notices,
    }


@lru_cache(maxsize=16)
def _what_if(demand_key: str) -> str:
    return json.dumps(planning.what_if(demand_change_pct=json.loads(demand_key) or None))


def what_if(o: Overrides) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(_what_if(json.dumps(o.demand_change_pct, sort_keys=True)))
    return result


_BUILDERS = {
    "orders": _orders,
    "forecast": _forecast,
    "planning": _planning,
    "mes": _mes,
    "material": _material,
    "purchasing": _purchasing,
    "quality": _quality,
    "delivery": _delivery,
}


@lru_cache(maxsize=256)
def _cached(module: str, key: str) -> str:
    d = json.loads(key)
    d.pop("applied", None)
    return json.dumps(_BUILDERS[module](Overrides(**d)), default=str)


def summary(module: str, o: Overrides | None = None) -> dict[str, Any]:
    if module not in _BUILDERS:
        raise FVError(UNKNOWN_MODULE, detail={"module": module, "valid": list(PAGES)})
    o = o or Overrides()
    key = json.dumps({**o.as_dict(), "applied": []}, sort_keys=True, default=str)
    return {"module": module, "scenario": o.scenario, "applied_actions": o.applied, **json.loads(_cached(module, key))}
