from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from app import warehouse
from app.tools.common import TODAY, to_date, working_days_in_month
from app.tools.planning import monthly_plan
from simulator import plant as P

USE_SQL = """
SELECT production_date AS d, SUM(resin_kg) AS kg, SUM(output_quantity) AS good
FROM fv.fact_production WHERE resin_kg > 0 GROUP BY d ORDER BY d
"""


def ratio(numerator: float, denominator: float | Sequence[Any]) -> float | None:
    if not denominator:
        return None
    return numerator / denominator if isinstance(denominator, (int, float)) else numerator / len(denominator)


def _on_hand(material_id: str) -> float:
    r = warehouse.query("SELECT on_hand_qty FROM fv.fact_material_stock WHERE material_id = :m ORDER BY snapshot_date DESC LIMIT 1", {"m": material_id})
    return float(r[0]["on_hand_qty"]) if r else 0.0


def material_requirements(month: str | None = None, demand_change_pct: dict | None = None) -> dict:
    m = to_date(month).replace(day=1) if month else TODAY.replace(day=1)
    plan = monthly_plan(m, demand_change_pct)
    bom = warehouse.query("SELECT sku, material_id, qty_per_unit, unit FROM fv.bom")
    req: dict[str, float] = {}
    for b in bom:
        req[b["material_id"]] = req.get(b["material_id"], 0.0) + plan.get(b["sku"], 0) * b["qty_per_unit"]
    stocks = {r["material_id"]: float(r["on_hand_qty"]) for r in warehouse.query(
        "SELECT material_id, on_hand_qty FROM fv.fact_material_stock WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM fv.fact_material_stock)")}
    rows = []
    for mid, q in sorted(req.items()):
        mat = P.MATERIAL_BY_ID[mid]
        on = stocks.get(mid)
        rows.append({"material_id": mid, "material_name": mat.material_name, "unit": mat.unit, "required_qty": round(q, 1),
                     "on_hand_qty": on, "coverage_pct": round(100 * on / q, 1) if on is not None and q else None,
                     "safety_stock": mat.safety_stock})
    return {"month": m.isoformat(), "working_days": working_days_in_month(m), "plan_qty": plan, "requirements": rows, "source": "fv.fact_monthly_plan x fv.bom; fv.fact_material_stock"}


def stock_projection(material_id: str = "PP-EPDM-T20", demand_change_pct: dict | None = None, horizon_days: int = 21,
                     extra_receipts: list | None = None) -> dict:
    if material_id != "PP-EPDM-T20":
        return {"material_id": material_id, "note": "consumption history is tracked for resin only in this prototype"}
    use = warehouse.query(USE_SQL)
    last14 = use[-14:]
    actual_rate = ratio(sum(r["kg"] for r in last14), last14)
    plan = monthly_plan(TODAY.replace(day=1), demand_change_pct)
    base_plan = monthly_plan(TODAY.replace(day=1))
    bom = {r["sku"]: r["qty_per_unit"] for r in warehouse.query("SELECT sku, qty_per_unit FROM fv.bom WHERE material_id = :m", {"m": material_id})}
    wd = working_days_in_month(TODAY.replace(day=1))
    bom_rate = sum(plan[s] * bom[s] for s in plan) / wd
    base_bom_rate = sum(base_plan[s] * bom[s] for s in base_plan) / wd
    per_unit = warehouse.query(
        "SELECT sku, SUM(resin_kg) AS kg, SUM(output_quantity) AS good FROM fv.fact_production WHERE resin_kg > 0 AND production_date >= :d0 GROUP BY sku",
        {"d0": to_date(last14[0]["d"])}) if last14 else []
    actual_kg = sum(r["kg"] for r in per_unit)
    bom_kg = sum(r["good"] * bom[r["sku"]] for r in per_unit)
    overconsumption = ratio(actual_kg, bom_kg)
    scaled = ratio(actual_rate * bom_rate, base_bom_rate) if actual_rate is not None else None
    actual_rate_adj = scaled if scaled is not None else bom_rate
    on_hand = _on_hand(material_id)
    delays = {r["vendor_id"]: r["delay"] for r in warehouse.query(
        "SELECT vendor_id, AVG(DATE_DIFF(received_date, promised_date, DAY)) AS delay FROM fv.fact_purchase_order WHERE received_date IS NOT NULL GROUP BY vendor_id")} if warehouse.is_bigquery() else _delays_local()
    receipts = []
    for po in warehouse.query("SELECT po_id, vendor_id, order_qty, promised_date FROM fv.fact_purchase_order WHERE material_id = :m AND po_status = 'Open'", {"m": material_id}):
        exp = to_date(po["promised_date"]) + timedelta(days=round(delays.get(po["vendor_id"], 0)))
        receipts.append({"po_id": po["po_id"], "expected_date": exp, "qty": float(po["order_qty"])})
    for r in extra_receipts or []:
        receipts.append({"po_id": r.get("po_id", "proposed"), "expected_date": to_date(r["expected_date"]), "qty": float(r["qty"])})

    def project(rate: float):
        stock, rows, stockout, below_safety = on_hand, [], None, None
        for k in range(horizon_days):
            d = TODAY + timedelta(days=k)
            stock += sum(r["qty"] for r in receipts if r["expected_date"] == d)
            if P.is_working_day(d):
                stock -= rate
            rows.append({"date": d.isoformat(), "projected_qty": round(stock)})
            if below_safety is None and stock < P.MATERIAL_BY_ID[material_id].safety_stock:
                below_safety = d
            if stockout is None and stock < 0:
                stockout = d
        return rows, stockout, below_safety

    rows, stockout, below = project(actual_rate_adj)
    _, stockout_bom, _ = project(bom_rate)
    return {
        "material_id": material_id, "as_of": TODAY.isoformat(), "on_hand_qty": round(on_hand), "unit": "kg",
        "actual_rate_per_workday": round(actual_rate_adj), "bom_rate_per_workday": round(bom_rate),
        "overconsumption_vs_bom_pct": round(100 * (overconsumption - 1), 1) if overconsumption is not None else None,
        "stockout_date": stockout.isoformat() if stockout else None,
        "days_to_stockout": (stockout - TODAY).days if stockout else None,
        "days_to_stockout_bom_basis": (stockout_bom - TODAY).days if stockout_bom else None,
        "below_safety_stock_date": below.isoformat() if below else None,
        "open_receipts": [{**r, "expected_date": r["expected_date"].isoformat()} for r in receipts],
        "projection": rows, "demand_change_pct": demand_change_pct or {},
        "source": "fv.fact_material_stock, fv.fact_production (actual use), fv.fact_monthly_plan x fv.bom, fv.fact_purchase_order",
    }


def _delays_local() -> dict:
    rows = warehouse.query("SELECT vendor_id, promised_date, received_date FROM fv.fact_purchase_order WHERE received_date IS NOT NULL")
    acc: dict[str, list] = {}
    for r in rows:
        acc.setdefault(r["vendor_id"], []).append((to_date(r["received_date"]) - to_date(r["promised_date"])).days)
    return {k: sum(v) / len(v) for k, v in acc.items()}
