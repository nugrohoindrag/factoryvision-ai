"""Quality Control tools: defect correlation to resin lot and humidity, hold candidates."""

from __future__ import annotations

from datetime import timedelta

from app import warehouse
from app.tools.common import TODAY, pct
from simulator import plant as P

INSP_SQL = "SELECT resin_lot_id, humidity_pct, SUM(inspected_qty) AS n FROM fv.fact_inspection WHERE inspection_date >= :d0 GROUP BY resin_lot_id, humidity_pct"
DEF_SQL = "SELECT resin_lot_id, humidity_pct, defect_code, SUM(defect_qty) AS n FROM fv.fact_quality WHERE inspection_date >= :d0 GROUP BY resin_lot_id, humidity_pct, defect_code"


def defect_correlation(days: int = 45, humidity_threshold: float = 80.0) -> dict:
    """Defect rate by resin lot and by humidity band, with lift versus the overall rate.

    Args:
        days: look-back window (default 45).
        humidity_threshold: humidity split in % RH (default 80).
    """
    d0 = TODAY - timedelta(days=days)
    insp = warehouse.query(INSP_SQL, {"d0": d0})
    defs = warehouse.query(DEF_SQL, {"d0": d0})
    total = sum(r["n"] for r in insp)
    by_lot_n: dict[str, int] = {}
    hum_n = {"high": 0, "normal": 0}
    for r in insp:
        by_lot_n[r["resin_lot_id"]] = by_lot_n.get(r["resin_lot_id"], 0) + r["n"]
        hum_n["high" if r["humidity_pct"] > humidity_threshold else "normal"] += r["n"]
    overall: dict[str, int] = {}
    lot_def: dict[tuple, int] = {}
    hum_def: dict[tuple, int] = {}
    for r in defs:
        overall[r["defect_code"]] = overall.get(r["defect_code"], 0) + r["n"]
        lot_def[(r["resin_lot_id"], r["defect_code"])] = lot_def.get((r["resin_lot_id"], r["defect_code"]), 0) + r["n"]
        band = "high" if r["humidity_pct"] > humidity_threshold else "normal"
        hum_def[(band, r["defect_code"])] = hum_def.get((band, r["defect_code"]), 0) + r["n"]
    lot_findings = []
    for (lot, code), n in lot_def.items():
        base = overall[code] / total
        rate = n / by_lot_n[lot]
        if by_lot_n[lot] >= 1000 and rate > 2.5 * base and n >= 20:
            lot_findings.append({"resin_lot_id": lot, "defect_code": code, "defect_name": P.DEFECT_BY_CODE[code][2], "ng_group": P.DEFECT_BY_CODE[code][3],
                                 "rate_pct": pct(rate, 2), "overall_rate_pct": pct(base, 2), "lift": round(rate / base, 1), "inspected_qty": by_lot_n[lot], "defect_qty": n})
    lot_findings.sort(key=lambda x: -x["lift"])
    hum_findings = []
    for code in overall:
        hi = hum_def.get(("high", code), 0) / max(1, hum_n["high"])
        lo = hum_def.get(("normal", code), 0) / max(1, hum_n["normal"])
        if lo and hi / lo > 1.8:
            hum_findings.append({"defect_code": code, "defect_name": P.DEFECT_BY_CODE[code][2], "rate_high_humidity_pct": pct(hi, 2),
                                 "rate_normal_pct": pct(lo, 2), "lift": round(hi / lo, 1)})
    pareto = sorted(({"defect_code": c, "defect_name": P.DEFECT_BY_CODE[c][2], "qty": n, "rate_pct": pct(n / total, 2)} for c, n in overall.items()), key=lambda x: -x["qty"])
    suspect = lot_findings[0]["resin_lot_id"] if lot_findings else None
    return {"window_days": days, "inspected_qty": total, "defect_rate_pct": pct(sum(overall.values()) / total, 2),
            "suspect_lot": suspect, "lot_findings": lot_findings, "humidity_threshold_pct": humidity_threshold, "humidity_findings": hum_findings,
            "defect_pareto": pareto[:8], "hold_candidates": hold_candidates(suspect) if suspect else None,
            "source": "fv.fact_inspection, fv.fact_quality"}


def hold_candidates(resin_lot_id: str) -> dict:
    """Finished and WIP bumpers made from one resin lot (candidates for quality hold)."""
    rows = warehouse.query("SELECT sku, color, wip_qty, mezzanine_qty, sgl_qty FROM fv.fact_stock_snapshot WHERE resin_lot_id = :lot", {"lot": resin_lot_id})
    units = sum(r["wip_qty"] + r["mezzanine_qty"] + r["sgl_qty"] for r in rows)
    prod = warehouse.query("SELECT MIN(production_date) AS d0, MAX(production_date) AS d1, SUM(output_quantity) AS q FROM fv.fact_production WHERE resin_lot_id = :lot", {"lot": resin_lot_id})
    return {"resin_lot_id": resin_lot_id, "units_in_stock": units, "by_sku": _sum_by(rows), "molded_from": str(prod[0]["d0"]), "molded_to": str(prod[0]["d1"]),
            "molded_qty": int(prod[0]["q"] or 0)}


def _sum_by(rows):
    out: dict[str, int] = {}
    for r in rows:
        out[r["sku"]] = out.get(r["sku"], 0) + r["wip_qty"] + r["mezzanine_qty"] + r["sgl_qty"]
    return out
