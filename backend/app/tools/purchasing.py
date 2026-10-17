"""Purchasing tools: vendor scorecard and PO recommendation."""

from __future__ import annotations

from datetime import timedelta

from app import warehouse
from app.tools.common import TODAY, idr, pct, to_date
from simulator import plant as P

PO_SQL = "SELECT po_id, vendor_id, material_id, order_qty, order_date, promised_date, received_date, po_status FROM fv.fact_purchase_order"


def vendor_performance(category: str | None = None) -> dict:
    """On-time rate, average delay and actual lead time per vendor over the last 90 days.

    Args:
        category: optional vendor category filter (Resin, Paint, Component, Packaging).
    """
    rows = warehouse.query(PO_SQL)
    vendors = {v["vendor_id"]: v for v in warehouse.query("SELECT vendor_id, vendor_name, category, lead_time_days, unit_price_idr FROM fv.dim_vendor")}
    out = []
    for vid, v in vendors.items():
        if category and v["category"].lower() != category.lower():
            continue
        pos = [r for r in rows if r["vendor_id"] == vid and r["received_date"] is not None]
        if not pos:
            continue
        delays = [(to_date(r["received_date"]) - to_date(r["promised_date"])).days for r in pos]
        leads = [(to_date(r["received_date"]) - to_date(r["order_date"])).days for r in pos]
        out.append({"vendor_id": vid, "vendor_name": v["vendor_name"], "category": v["category"], "po_count": len(pos),
                    "on_time_pct": pct(sum(1 for d in delays if d <= 0) / len(pos)), "avg_delay_days": round(sum(delays) / len(delays), 1),
                    "max_delay_days": max(delays), "quoted_lead_time_days": v["lead_time_days"],
                    "effective_lead_time_days": round(sum(leads) / len(leads), 1), "unit_price_idr": v["unit_price_idr"]})
    out.sort(key=lambda x: -x["avg_delay_days"])
    return {"vendors": out, "worst_vendor": out[0]["vendor_name"] if out else None, "source": "fv.fact_purchase_order, fv.dim_vendor"}


def recommend_po(material_id: str = "PP-EPDM-T20", need_by: str | None = None, qty: float | None = None) -> dict:
    """Compare vendors for an urgent PO: expected arrival = today + quoted lead time + average delay.

    Args:
        material_id: e.g. PP-EPDM-T20.
        need_by: date the material must arrive (YYYY-MM-DD), e.g. the projected stockout date.
        qty: order quantity in kg; default = 10 working days of use.
    """
    perf = {v["vendor_id"]: v for v in vendor_performance()["vendors"]}
    need = to_date(need_by) if need_by else None
    qty = qty or 160_000.0
    opts = []
    for vid in P.MATERIAL_VENDORS.get(material_id, []):
        v = perf.get(vid) or {**P.VENDOR_BY_ID[vid], "avg_delay_days": 0, "quoted_lead_time_days": P.VENDOR_BY_ID[vid]["lead_time_days"]}
        expected = TODAY + timedelta(days=round(v["quoted_lead_time_days"] + max(0, v["avg_delay_days"])))
        price = P.VENDOR_BY_ID[vid]["unit_price_idr"]
        opts.append({"vendor_id": vid, "vendor_name": P.VENDOR_BY_ID[vid]["vendor_name"], "expected_arrival": expected.isoformat(),
                     "arrives_before_need": (expected < need) if need else None, "unit_price_idr": price, "po_value_idr": idr(price * qty),
                     "avg_delay_days": v["avg_delay_days"]})
    ok = [o for o in opts if o["arrives_before_need"] in (True, None)]
    best = min(ok or opts, key=lambda o: (o["expected_arrival"], o["unit_price_idr"]))
    return {"material_id": material_id, "qty": qty, "need_by": need.isoformat() if need else None, "options": opts, "recommended_vendor": best["vendor_name"],
            "recommended_vendor_id": best["vendor_id"], "premium_vs_cheapest_idr": idr(best["po_value_idr"] - min(o["po_value_idr"] for o in opts)),
            "source": "fv.fact_purchase_order, fv.dim_vendor"}
