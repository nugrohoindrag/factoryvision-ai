"""Waste and energy: scrap, kWh and CO2e of the last four weeks against the four before, and what the approved
agent decisions save. All numbers come from fv.fact_production and fv.fact_energy plus the impact_calculator figures
already attached to each proposal; nothing here is estimated by Gemini."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app import actions, plant, warehouse
from app.tools.common import TODAY, idr

WINDOW_DAYS = 28
RESIN = "PP-EPDM-T20"

SCRAP_SQL = """
SELECT sku, SUM(scrap_quantity) AS scrap, SUM(total_quantity) AS total, SUM(output_quantity) AS good
FROM fv.fact_production WHERE production_date BETWEEN :a AND :b AND machine_id LIKE 'IMM-%' GROUP BY sku
"""
ENERGY_SQL = """
SELECT SUM(kwh) AS kwh, SUM(co2e_kg) AS co2e_kg, SUM(gas_m3) AS gas_m3
FROM fv.fact_energy WHERE energy_date BETWEEN :a AND :b
"""


def _period(start: str, end: str) -> dict[str, Any]:
    resin_idr = plant.MATERIAL_BY_ID[RESIN].unit_cost_idr
    scrap_kg = scrap_units = total = good = 0.0
    for r in warehouse.query(SCRAP_SQL, {"a": start, "b": end}):
        p = plant.PRODUCT_BY_SKU.get(r["sku"])
        if p is None:
            continue
        scrap_units += float(r["scrap"] or 0)
        scrap_kg += float(r["scrap"] or 0) * p.shot_weight_kg
        total += float(r["total"] or 0)
        good += float(r["good"] or 0)
    e = (warehouse.query(ENERGY_SQL, {"a": start, "b": end}) or [{}])[0]
    kwh = float(e.get("kwh") or 0)
    co2 = float(e.get("co2e_kg") or 0) or kwh * plant.GRID_KG_CO2_PER_KWH
    return {
        "from": start,
        "to": end,
        "scrap_units": round(scrap_units),
        "scrap_kg": round(scrap_kg),
        "scrap_rate_pct": round(100 * scrap_units / total, 2) if total else None,
        "scrap_idr": idr(scrap_kg * resin_idr),
        "kwh": round(kwh),
        "energy_idr": idr(kwh * plant.ELECTRICITY_IDR_PER_KWH),
        "co2e_kg": round(co2),
        "kwh_per_good_unit": round(kwh / good, 3) if good else None,
        "good_units": round(good),
    }


def _change(cur: float | None, prev: float | None) -> float | None:
    return round(100 * (cur - prev) / prev, 1) if cur is not None and prev else None


def report(session_id: str) -> dict[str, Any]:
    end = TODAY - timedelta(days=1)
    cur = _period((end - timedelta(days=WINDOW_DAYS - 1)).isoformat(), end.isoformat())
    prev_end = end - timedelta(days=WINDOW_DAYS)
    prev = _period((prev_end - timedelta(days=WINDOW_DAYS - 1)).isoformat(), prev_end.isoformat())
    keys = ("scrap_kg", "scrap_rate_pct", "kwh", "co2e_kg", "kwh_per_good_unit", "scrap_idr", "energy_idr")
    acts = actions.list_actions(session_id)

    def total(status: str) -> dict[str, int]:
        out = dict.fromkeys(("rupiah_idr", "scrap_kg", "kwh", "co2e_kg"), 0)
        for a in acts:
            if a["status"] == status:
                for k in out:
                    out[k] += int((a.get("impact") or {}).get(k) or 0)
        return out

    approved, pending = total("approved"), total("pending")
    return {
        "window_days": WINDOW_DAYS,
        "current": cur,
        "previous": prev,
        "change_pct": {k: _change(cur[k], prev[k]) for k in keys},
        "decisions": {
            "approved": {**approved, "count": sum(1 for a in acts if a["status"] == "approved")},
            "pending": {**pending, "count": sum(1 for a in acts if a["status"] == "pending")},
            "note": "Monthly effect of each decision from impact_calculator; positive scrap, kWh and CO2e are reductions.",
        },
        "factors": {
            "electricity_idr_per_kwh": plant.ELECTRICITY_IDR_PER_KWH,
            "grid_kg_co2_per_kwh": plant.GRID_KG_CO2_PER_KWH,
            "resin_idr_per_kg": plant.MATERIAL_BY_ID[RESIN].unit_cost_idr,
        },
        "source": "fv.fact_production (molding scrap x shot weight), fv.fact_energy, approved proposals (impact_calculator)",
    }
