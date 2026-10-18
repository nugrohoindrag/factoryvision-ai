from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from app import plant, warehouse
from app.tools.common import TODAY, to_date, working_days_between

if TYPE_CHECKING:
    from collections.abc import Collection

ATP_RULE_SUMMARY = "Ship what is needed, and delay what is not: any exception must be confirmed by the planner before the cutoff."

ATP_RULE_FORMULA = (
    "Supply available on date d = stock on hand (WIP + Mezzanine + SGL) "
    "+ output rate x working days from today up to and including d, where the "
    "output rate is the average daily output of the machine that holds the "
    "SKU's mold over its last 7 production days. An order is at risk when "
    "cumulative open demand up to and including it exceeds that supply."
)

PRIORITY = {"CUS-OEMA": 0, "CUS-OEMB": 0, "CUS-TIER1": 1, "CUS-EXP": 2, "CUS-AFTM": 3}


def output_rate(machine_id: str) -> float:
    rows = warehouse.query(
        "SELECT production_date AS d, SUM(output_quantity) AS q FROM fv.fact_production WHERE machine_id = :m GROUP BY d ORDER BY d DESC LIMIT 7",
        {"m": machine_id},
    )
    return sum(r["q"] for r in rows) / len(rows) if rows else 0.0


def _stock() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in warehouse.query("SELECT sku, color, wip_qty, mezzanine_qty, sgl_qty, hourly_use, target_hours FROM fv.fact_stock_snapshot"):
        s = out.setdefault(r["sku"], {"units": 0, "colors": []})
        units = r["wip_qty"] + r["mezzanine_qty"] + r["sgl_qty"]
        s["units"] += units
        hours = units / r["hourly_use"] if r["hourly_use"] else None
        s["colors"].append(
            {
                "color": r["color"],
                "wip_qty": r["wip_qty"],
                "mezzanine_qty": r["mezzanine_qty"],
                "sgl_qty": r["sgl_qty"],
                "stock_hours": round(hours, 1) if hours is not None else None,
                "target_hours": r["target_hours"],
                "gap_hours": round(hours - r["target_hours"], 1) if hours is not None else None,
            }
        )
    return out


def atp_check(
    sku: str | None = None,
    rate_override: dict[str, float] | None = None,
    extra_stock: dict[str, float] | None = None,
    extra_orders: list[dict[str, Any]] | None = None,
    cancelled: Collection[str] = (),
) -> dict[str, Any]:
    """extra_orders are order lines entered in this session; cancelled lists order line IDs to leave out."""
    molds = {r["sku"]: r["current_machine_id"] for r in warehouse.query("SELECT sku, current_machine_id FROM fv.dim_mold")}
    orders = warehouse.query(
        "SELECT order_id, customer_id, sku, order_qty, requested_delivery_date FROM fv.fact_customer_order WHERE order_status IN ('Received', 'Planned')"
    )
    orders = [o for o in orders + list(extra_orders or []) if o["order_id"] not in cancelled]
    stock = _stock()
    result: list[dict[str, Any]] = []
    for s in [sku] if sku else plant.SKUS:
        mid = molds[s]
        rate = (rate_override or {}).get(s, output_rate(mid))
        on_hand = stock.get(s, {"units": 0})["units"] + (extra_stock or {}).get(s, 0)
        mine = sorted((o for o in orders if o["sku"] == s), key=lambda o: (to_date(o["requested_delivery_date"]), PRIORITY[o["customer_id"]], o["order_id"]))
        cum = 0
        rows: list[dict[str, Any]] = []
        for o in mine:
            cum += o["order_qty"]
            req = to_date(o["requested_delivery_date"])
            supply = on_hand + rate * working_days_between(TODAY, req + timedelta(days=1))
            short = cum - supply
            est = req
            if short > 0:
                d, k = TODAY, 0
                while on_hand + rate * working_days_between(TODAY, d + timedelta(days=1)) < cum and k < 60:
                    d += timedelta(days=1)
                    k += 1
                est = max(d, req + timedelta(days=1))
            rows.append(
                {
                    "order_id": o["order_id"],
                    "customer_id": o["customer_id"],
                    "order_qty": o["order_qty"],
                    "requested_delivery_date": req.isoformat(),
                    "estimated_completion": est.isoformat(),
                    "at_risk": short > 0,
                    "shortfall_qty": max(0, round(short)),
                    "days_late": (est - req).days,
                }
            )
        result.append(
            {
                "sku": s,
                "machine_id": mid,
                "output_rate_per_day": round(rate, 1),
                "stock_on_hand": on_hand,
                "orders": rows,
                "stock_by_color": stock.get(s, {}).get("colors", []),
            }
        )
    return {
        "as_of": TODAY.isoformat(),
        "skus": result,
        "rule": ATP_RULE_SUMMARY,
        "source": "fv.fact_customer_order, fv.fact_stock_snapshot, fv.fact_production, fv.dim_mold",
    }


def orders_at_risk(
    rate_override: dict[str, float] | None = None,
    extra_stock: dict[str, float] | None = None,
    extra_orders: list[dict[str, Any]] | None = None,
    cancelled: Collection[str] = (),
) -> dict[str, Any]:
    atp = atp_check(rate_override=rate_override, extra_stock=extra_stock, extra_orders=extra_orders, cancelled=cancelled)
    customers = {c["customer_id"]: c for c in plant.CUSTOMERS}
    risk: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []
    for s in atp["skus"]:
        for o in s["orders"]:
            if o["at_risk"]:
                c = customers[o["customer_id"]]
                risk.append(
                    {
                        **o,
                        "sku": s["sku"],
                        "machine_id": s["machine_id"],
                        "customer_name": c["customer_name"],
                        "penalty_idr": c["penalty_idr_per_day"] * o["days_late"],
                    }
                )
        low.extend({"sku": s["sku"], **c} for c in s["stock_by_color"] if c["gap_hours"] is not None and c["gap_hours"] < 0)
    return {
        "as_of": atp["as_of"],
        "orders_at_risk": risk,
        "count": len(risk),
        "machines_involved": sorted({r["machine_id"] for r in risk}),
        "total_penalty_idr": sum(r["penalty_idr"] for r in risk),
        "below_stock_target": low,
        "source": atp["source"],
    }
