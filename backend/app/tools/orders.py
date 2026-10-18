from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app import plant, warehouse
from app.errors import FVError
from app.tools.common import TODAY, pct, to_date, working_days_in_month

UNKNOWN_ENTITY = "FV-DATA-001"
INVALID_REQUEST = "FV-REQ-001"

STATUSES = ("Received", "Planned", "In Production", "Produced", "Ready to Ship", "Shipped", "Completed", "Cancelled")
OPEN_STATUSES = STATUSES[:6]
ON_FILE_TOLERANCE = 0.02

CUSTOMER_BY_ID = {c["customer_id"]: c for c in plant.CUSTOMERS}


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def order_book(sku: str | None = None) -> dict[str, Any]:
    if sku and sku not in plant.PRODUCT_BY_SKU:
        raise FVError(UNKNOWN_ENTITY, f"unknown sku {sku}", {"valid_skus": plant.SKUS})
    since = TODAY - timedelta(days=30)
    rows = warehouse.query(
        "SELECT order_id, customer_id, sku, order_qty, order_date, requested_delivery_date, order_status, shipped_qty "
        "FROM fv.fact_customer_order WHERE requested_delivery_date >= :since ORDER BY requested_delivery_date, order_id",
        {"since": since},
    )
    if sku:
        rows = [r for r in rows if r["sku"] == sku]
    counts = dict.fromkeys(STATUSES, 0)
    for r in rows:
        counts[r["order_status"]] = counts.get(r["order_status"], 0) + 1
    open_rows = [r for r in rows if r["order_status"] in OPEN_STATUSES]
    by_customer: dict[str, int] = {}
    by_week: dict[str, int] = {}
    for r in open_rows:
        by_customer[r["customer_id"]] = by_customer.get(r["customer_id"], 0) + r["order_qty"]
        wk = _week_start(to_date(r["requested_delivery_date"])).isoformat()
        by_week[wk] = by_week.get(wk, 0) + r["order_qty"]
    ships = warehouse.query("SELECT sku, on_time FROM fv.fact_shipment WHERE ship_date >= :since", {"since": since})
    ships = [s for s in ships if not sku or s["sku"] == sku]
    on_time = sum(1 for s in ships if s["on_time"])
    return {
        "as_of": TODAY.isoformat(),
        "sku": sku,
        "statuses": list(STATUSES),
        "status_counts": counts,
        "open_count": len(open_rows),
        "open_qty": sum(r["order_qty"] for r in open_rows),
        "due_this_week": sum(1 for r in open_rows if _week_start(to_date(r["requested_delivery_date"])) == _week_start(TODAY)),
        "on_time_delivery_pct": pct(on_time / len(ships)) if ships else None,
        "shipments_30d": len(ships),
        "open_by_customer": [
            {"customer_id": c, "customer_name": CUSTOMER_BY_ID[c]["customer_name"], "open_qty": q} for c, q in sorted(by_customer.items(), key=lambda x: -x[1])
        ],
        "open_by_week": [{"week_start": w, "open_qty": q} for w, q in sorted(by_week.items())],
        "open_orders": [
            {
                **r,
                "order_date": to_date(r["order_date"]).isoformat(),
                "requested_delivery_date": to_date(r["requested_delivery_date"]).isoformat(),
                "customer_name": CUSTOMER_BY_ID[r["customer_id"]]["customer_name"],
            }
            for r in open_rows
        ],
        "source": "fv.fact_customer_order (requested delivery in the last 30 days and later), fv.fact_shipment",
    }


def monthly_schedule(month: date) -> dict[str, int]:
    rows = warehouse.query("SELECT sku, plan_qty FROM fv.fact_monthly_plan WHERE plan_month = :m", {"m": month.replace(day=1)})
    return {r["sku"]: int(r["plan_qty"] or 0) for r in rows}


def match_customer(name: str) -> dict[str, Any] | None:
    n = name.lower()
    for c in plant.CUSTOMERS:
        full, cid = str(c["customer_name"]).lower(), str(c["customer_id"]).lower()
        tag = full.split("(")[-1].rstrip(")") if "(" in full else None
        if full in n or (tag and tag in n) or cid in n:
            return dict(c)
    return None


def check_po(po: dict[str, Any]) -> dict[str, Any]:
    """Validate an extracted customer PO against master data and the schedule on file; numbers are recomputed here, not taken from the LLM."""
    cust = match_customer(str(po.get("customer_name", "")))
    if cust is None:
        raise FVError(
            UNKNOWN_ENTITY, f"customer {po.get('customer_name')!r} is not in the customer master", {"valid": [c["customer_name"] for c in plant.CUSTOMERS]}
        )
    lines = po.get("lines") or []
    if not lines:
        raise FVError(INVALID_REQUEST, detail="the PO has no lines")
    model_share = {m: dict(mix) for m, mix in plant.CUSTOMER_MIX.items()}
    out, warnings = [], []
    for ln in lines:
        sku = str(ln.get("sku", "")).upper()
        if sku not in plant.PRODUCT_BY_SKU:
            warnings.append(f"{sku or '(blank)'} is not a Vidad SKU; line ignored")
            continue
        qty = int(ln["qty"])
        month = date.fromisoformat(f"{ln.get('month') or TODAY.strftime('%Y-%m')}-01")
        share = model_share[plant.PRODUCT_BY_SKU[sku].model].get(cust["customer_id"], 0.0)
        plan = monthly_schedule(month).get(sku)
        on_file = round(plan * share) if plan and share else None
        prev = ln.get("previous_qty") or on_file
        matches = None if on_file is None or not ln.get("previous_qty") else abs(int(ln["previous_qty"]) - on_file) <= ON_FILE_TOLERANCE * on_file
        if matches is False:
            warnings.append(f"{sku}: previous qty {int(ln['previous_qty']):,} on the PO differs from {on_file:,} on file")
        out.append(
            {
                "sku": sku,
                "month": month.strftime("%Y-%m"),
                "previous_qty": int(prev) if prev else None,
                "qty": qty,
                "change_qty": qty - int(prev) if prev else None,
                "change_pct": round(100 * (qty / int(prev) - 1), 1) if prev else None,
                "on_file_qty": on_file,
                "matches_on_file": matches,
                "per_working_day": round(qty / working_days_in_month(month), 1),
            }
        )
    return {
        "customer_id": cust["customer_id"],
        "customer_name": cust["customer_name"],
        "po_number": po.get("po_number"),
        "issue_date": po.get("issue_date"),
        "notes": po.get("notes"),
        "lines": out,
        "changed": [x for x in out if x["change_pct"]],
        "warnings": warnings,
        "source": "fv.fact_monthly_plan (OEM schedule on file) x customer share; dim_customer",
    }
