from __future__ import annotations

from functools import lru_cache

from app import warehouse
from app.tools import delivery, material, mes, quality
from app.tools.common import TODAY, idr
from simulator import plant as P

SCRAP_SQL = """
SELECT machine_id, sku, SUM(scrap_quantity) AS scrap, SUM(total_quantity) AS total,
  SUM(actual_cycle_s * total_quantity) AS actual_s, SUM(ideal_cycle_s * total_quantity) AS ideal_s
FROM fv.fact_production WHERE production_date = :d AND machine_id LIKE 'IMM-%' GROUP BY machine_id, sku
"""


def _losses(day) -> list[dict]:
    losses = []
    for r in mes.downtime_pareto(days=1, top=10)["top"]:
        losses.append({"kind": "downtime", "title": f"Downtime: {r['reason_name']}", "loss_idr": r["loss_idr"], "detail": f"{r['hours']} h, {r['events']} event(s)",
                       "source": "fv.fact_downtime"})
    for r in warehouse.query(SCRAP_SQL, {"d": day}):
        p = P.PRODUCT_BY_SKU[r["sku"]]
        resin_cost = r["scrap"] * p.shot_weight_kg * P.MATERIAL_BY_ID["PP-EPDM-T20"].unit_cost_idr
        if r["scrap"]:
            losses.append({"kind": "scrap", "title": f"Scrap on {r['machine_id']} ({r['sku']})", "loss_idr": idr(resin_cost),
                           "detail": f"{int(r['scrap'])} bumpers, {round(r['scrap'] * p.shot_weight_kg)} kg resin", "source": "fv.fact_production"})
        lost_s = r["actual_s"] - r["ideal_s"]
        if lost_s > 0:
            units = lost_s / p.ideal_cycle_s
            losses.append({"kind": "speed", "title": f"Speed loss on {r['machine_id']} ({r['sku']})", "loss_idr": idr(units * p.price_idr * P.CONTRIBUTION_MARGIN),
                           "detail": f"{round(units)} bumpers not molded; cycle {r['actual_s'] / r['total']:.1f} s vs ideal {p.ideal_cycle_s:.0f} s",
                           "source": "fv.fact_production"})
    losses.sort(key=lambda x: -x["loss_idr"])
    return losses[:3]


def _risks() -> list[dict]:
    risks = []
    h = mes.machine_health("IMM-04")
    if h.get("breakdown_risk") in ("high", "medium"):
        risks.append({"module": "mes", "severity": h["breakdown_risk"], "title": f"IMM-04 breakdown risk in ~{round(h['days_to_alarm'])} days",
                      "detail": f"Cycle {h['current_cycle_s']} s and rising {h['cycle_trend_s_per_day']} s/day; MTBF {h['mtbf_hours_first_6_weeks']} h -> {h['mtbf_hours_last_2_weeks']} h"})
    s = material.stock_projection()
    if s.get("days_to_stockout") is not None:
        risks.append({"module": "material", "severity": "high" if s["days_to_stockout"] <= 10 else "medium",
                      "title": f"Resin PP-EPDM-T20 runs out in {s['days_to_stockout']} days ({s['stockout_date']})",
                      "detail": f"{s['on_hand_qty']:,} kg on hand, using {s['actual_rate_per_workday']:,} kg per working day ({s['overconsumption_vs_bom_pct']}% above BOM)"})
    r = delivery.orders_at_risk()
    if r["count"]:
        risks.append({"module": "delivery", "severity": "high", "title": f"{r['count']} orders at risk of late delivery",
                      "detail": f"All on {', '.join(r['machines_involved'])}; penalty exposure Rp{r['total_penalty_idr']:,}", "order_ids": [o["order_id"] for o in r["orders_at_risk"]]})
    q = quality.defect_correlation()
    if q.get("suspect_lot"):
        f = q["lot_findings"][0]
        risks.append({"module": "quality", "severity": "medium", "title": f"{f['defect_name']} {f['lift']}x on resin lot {q['suspect_lot']}",
                      "detail": f"{f['rate_pct']}% vs {f['overall_rate_pct']}% overall; {q['hold_candidates']['units_in_stock']} bumpers from this lot in stock"})
    return risks


@lru_cache(maxsize=1)
def briefing() -> dict:
    oee = mes.get_oee()
    day = oee["window"][1]
    return {
        "as_of": TODAY.isoformat(), "plant": "Vidad bumper plant", "yesterday": day,
        "oee": {"molding": oee["plant_molding"], "plant": oee["plant_all"], "worst_machine": oee["worst_machine"],
                "worst_machine_oee_pct": oee["by_machine"][oee["worst_machine"]]["oee_pct"] if oee["worst_machine"] else None},
        "top_losses": _losses(day), "risks": _risks(),
        "source": "deterministic tools over fv.* (no Gemini)",
    }
