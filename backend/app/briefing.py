from __future__ import annotations

from functools import lru_cache
from typing import Any

from app import plant, warehouse
from app.errors import FVError
from app.tools import delivery, material, mes, quality
from app.tools.common import TODAY, idr

SCRAP_SQL = """
SELECT machine_id, sku, SUM(scrap_quantity) AS scrap, SUM(total_quantity) AS total,
  SUM(actual_cycle_s * total_quantity) AS actual_s, SUM(ideal_cycle_s * total_quantity) AS ideal_s
FROM fv.fact_production WHERE production_date = :d AND machine_id LIKE 'IMM-%' GROUP BY machine_id, sku
"""


UNKNOWN_ENTITY = "FV-DATA-001"
PRODUCTION_ROWS_SQL = """
SELECT production_date, shift_id, machine_id, sku, total_quantity, output_quantity, scrap_quantity, ideal_cycle_s, actual_cycle_s
FROM fv.fact_production WHERE production_date = :d AND machine_id = :mid AND sku = :sku ORDER BY shift_id
"""
DOWNTIME_ROWS_SQL = """
SELECT d.downtime_id, d.shift_id, d.machine_id, m.area, d.duration_minutes
FROM fv.fact_downtime d JOIN fv.dim_machine m ON m.machine_id = d.machine_id
WHERE d.production_date = :d AND d.reason_code = :reason ORDER BY d.start_ts
"""


def _n(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}"


def _step(label: str, expr: str, value: str) -> dict[str, str]:
    return {"label": label, "expr": expr, "value": value}


def _term(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def _const(name: str, value: str, source: str, link: str | None = None) -> dict[str, str | None]:
    """A constant with where a person finds it in the app, never where it lives in code or the warehouse."""
    return {"name": name, "value": value, "source": source, "link": link}


def _trace(loss: dict[str, Any], day: str) -> dict[str, Any]:
    """How one loss figure is computed: the steps with their numbers, the last multiplication as an equation,
    the constants used and where to find them in the app, and the records the figure is summed from."""
    key = loss["_key"]
    total_idr = f"Rp{_n(loss['loss_idr'])}"
    if loss["kind"] == "downtime":
        rows = warehouse.query(DOWNTIME_ROWS_SQL, {"d": day, "reason": key["reason_code"]})
        rates = {r["area"]: mes.LINE_STOP_IDR_PER_HOUR[r["area"]] for r in rows}
        steps = [
            _step(
                f"{r['machine_id']} ({r['area']})",
                f"{_n(r['duration_minutes'])} min / 60 x Rp{_n(rates[r['area']])} per hour",
                f"Rp{_n(r['duration_minutes'] / 60 * rates[r['area']])}",
            )
            for r in rows
        ]
        hours = sum(r["duration_minutes"] for r in rows) / 60
        one_rate = next(iter(rates.values())) if len(rates) == 1 else None
        return {
            "formula": "Loss = sum of (downtime minutes / 60 x line-stop cost per hour of the machine's area)",
            "steps": [*steps, _step("Loss", "sum of the rows above", total_idr)],
            "equation": [_term("hours stopped", _n(hours, 2)), _term("line-stop cost / h", f"Rp{_n(one_rate)}")] if one_rate else None,
            "constants": [_const(f"Line-stop cost, {a}", f"Rp{_n(v)} / hour", "Plant cost assumption per area") for a, v in rates.items()],
            "scope": [_term("Production day", day), _term("Downtime reason", key["reason_code"].replace("_", " ").lower())],
            "dataset": "Downtime events",
            "rows": rows,
        }
    rows = warehouse.query(PRODUCTION_ROWS_SQL, {"d": day, "mid": key["machine_id"], "sku": key["sku"]})
    p = plant.PRODUCT_BY_SKU[key["sku"]]
    scope = [_term("Production day", day), _term("Machine", key["machine_id"]), _term("Product", key["sku"])]
    if loss["kind"] == "scrap":
        resin = plant.MATERIAL_BY_ID["PP-EPDM-T20"]
        scrap = sum(r["scrap_quantity"] for r in rows)
        kg = scrap * p.shot_weight_kg
        return {
            "formula": "Loss = scrapped bumpers x shot weight x resin cost per kg",
            "steps": [
                _step("Scrapped bumpers", "scrapped bumpers, summed over the shifts", _n(scrap)),
                _step("Resin lost", f"{_n(scrap)} x {p.shot_weight_kg} kg", f"{_n(kg, 1)} kg"),
                _step("Loss", f"{_n(kg, 1)} kg x Rp{_n(resin.unit_cost_idr)}", total_idr),
            ],
            "equation": [
                _term("bumpers scrapped", _n(scrap)),
                _term("kg per shot", f"{p.shot_weight_kg}"),
                _term("resin / kg", f"Rp{_n(resin.unit_cost_idr)}"),
            ],
            "constants": [
                _const(f"Shot weight, {p.sku}", f"{p.shot_weight_kg} kg", f"Product master · {p.sku}", "/master/products"),
                _const("Resin PP-EPDM-T20 cost", f"Rp{_n(resin.unit_cost_idr)} / kg", "Material master · PP-EPDM-T20", "/master/materials"),
            ],
            "scope": scope,
            "dataset": "Production records per shift",
            "rows": rows,
        }
    actual_s = sum(r["actual_cycle_s"] * r["total_quantity"] for r in rows)
    ideal_s = sum(r["ideal_cycle_s"] * r["total_quantity"] for r in rows)
    lost_s = actual_s - ideal_s
    units = lost_s / p.ideal_cycle_s
    return {
        "formula": "Loss = (actual machine time - ideal machine time) / ideal cycle x price x contribution margin",
        "steps": [
            _step("Actual machine time", "actual cycle x shots, summed over the shifts", f"{_n(actual_s)} s"),
            _step("Ideal machine time", "ideal cycle x shots, summed over the shifts", f"{_n(ideal_s)} s"),
            _step("Time lost", f"{_n(actual_s)} - {_n(ideal_s)} s ({_n(lost_s / 3600, 1)} h)", f"{_n(lost_s)} s"),
            _step("Bumpers not molded", f"{_n(lost_s)} s / {p.ideal_cycle_s:g} s ideal cycle", _n(units, 1)),
            _step("Loss", f"{_n(units, 1)} x Rp{_n(p.price_idr)} x {plant.CONTRIBUTION_MARGIN:.0%}", total_idr),
        ],
        "equation": [
            _term("bumpers not molded", _n(units, 1)),
            _term("selling price", f"Rp{_n(p.price_idr)}"),
            _term("contribution margin", f"{plant.CONTRIBUTION_MARGIN:.0%}"),
        ],
        "constants": [
            _const(f"Ideal cycle, {p.sku}", f"{p.ideal_cycle_s:g} s", f"Product master · {p.sku}", "/master/products"),
            _const(f"Selling price, {p.sku}", f"Rp{_n(p.price_idr)}", f"Product master · {p.sku}", "/master/products"),
            _const("Contribution margin", f"{plant.CONTRIBUTION_MARGIN:.0%}", "Finance assumption, share of the selling price"),
        ],
        "scope": scope,
        "dataset": "Production records per shift",
        "rows": rows,
    }


def _all_losses(day: str) -> list[dict[str, Any]]:
    """Every loss of the day, largest first; `_key` names the fact rows each one is summed from."""
    losses: list[dict[str, Any]] = [
        {
            "kind": "downtime",
            "title": f"Downtime: {r['reason_name']}",
            "loss_idr": r["loss_idr"],
            "detail": f"{r['hours']} h, {r['events']} event(s)",
            "source": "fv.fact_downtime",
            "_key": {"reason_code": r["reason_code"]},
        }
        for r in mes.downtime_pareto(days=1, top=10)["top"]
    ]
    for r in warehouse.query(SCRAP_SQL, {"d": day}):
        p = plant.PRODUCT_BY_SKU[r["sku"]]
        resin_cost = r["scrap"] * p.shot_weight_kg * plant.MATERIAL_BY_ID["PP-EPDM-T20"].unit_cost_idr
        if r["scrap"]:
            losses.append(
                {
                    "kind": "scrap",
                    "title": f"Scrap on {r['machine_id']} ({r['sku']})",
                    "loss_idr": idr(resin_cost),
                    "detail": f"{int(r['scrap'])} bumpers, {round(r['scrap'] * p.shot_weight_kg)} kg resin",
                    "source": "fv.fact_production",
                    "_key": {"machine_id": r["machine_id"], "sku": r["sku"]},
                }
            )
        lost_s = r["actual_s"] - r["ideal_s"]
        if lost_s > 0:
            units = lost_s / p.ideal_cycle_s
            losses.append(
                {
                    "kind": "speed",
                    "title": f"Speed loss on {r['machine_id']} ({r['sku']})",
                    "loss_idr": idr(units * p.price_idr * plant.CONTRIBUTION_MARGIN),
                    "detail": f"{round(units)} bumpers not molded; cycle {r['actual_s'] / r['total']:.1f} s vs ideal {p.ideal_cycle_s:.0f} s",
                    "source": "fv.fact_production",
                    "_key": {"machine_id": r["machine_id"], "sku": r["sku"]},
                }
            )
    for x in losses:
        x["id"] = "-".join([x["kind"], *x["_key"].values()])
    losses.sort(key=lambda x: -x["loss_idr"])
    return losses


def _losses(day: str) -> list[dict[str, Any]]:
    return [{k: v for k, v in x.items() if k != "_key"} for x in _all_losses(day)[:3]]


def loss_detail(loss_id: str) -> dict[str, Any]:
    """One loss of the last production day with its full calculation trace, for the loss detail page."""
    day = mes.get_oee()["window"][1]
    losses = _all_losses(day)
    found = next((i for i, x in enumerate(losses) if x["id"] == loss_id), None)
    if found is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown loss {loss_id}", {"valid": [x["id"] for x in losses[:10]]})
    loss = losses[found]
    top3 = sum(x["loss_idr"] for x in losses[:3]) or 1
    out = {k: v for k, v in loss.items() if k != "_key"}
    return {
        **out,
        "day": day,
        "rank": found + 1,
        "share_of_top3_pct": round(100 * loss["loss_idr"] / top3, 1) if found < 3 else None,
        "machine_id": loss["_key"].get("machine_id"),
        "sku": loss["_key"].get("sku"),
        "reason_code": loss["_key"].get("reason_code"),
        "trace": _trace(loss, day),
    }


def _risks() -> list[dict[str, Any]]:
    risks = []
    h = mes.machine_health("IMM-04")
    if h.get("breakdown_risk") in ("high", "medium"):
        risks.append(
            {
                "module": "mes",
                "severity": h["breakdown_risk"],
                "title": f"IMM-04 breakdown risk in ~{round(h['days_to_alarm'])} days",
                "detail": f"Cycle {h['current_cycle_s']} s and rising {h['cycle_trend_s_per_day']} s/day; MTBF {h['mtbf_hours_first_6_weeks']} h -> {h['mtbf_hours_last_2_weeks']} h",
            }
        )
    s = material.stock_projection()
    if s.get("days_to_stockout") is not None:
        risks.append(
            {
                "module": "material",
                "severity": "high" if s["days_to_stockout"] <= 10 else "medium",
                "title": f"Resin PP-EPDM-T20 runs out in {s['days_to_stockout']} days ({s['stockout_date']})",
                "detail": f"{s['on_hand_qty']:,} kg on hand, using {s['actual_rate_per_workday']:,} kg per working day ({s['overconsumption_vs_bom_pct']}% above BOM)",
            }
        )
    r = delivery.orders_at_risk()
    if r["count"]:
        risks.append(
            {
                "module": "delivery",
                "severity": "high",
                "title": f"{r['count']} orders at risk of late delivery",
                "detail": f"All on {', '.join(r['machines_involved'])}; penalty exposure Rp{r['total_penalty_idr'] / 1e6:.1f} M",
                "order_ids": [o["order_id"] for o in r["orders_at_risk"]],
            }
        )
    q = quality.defect_correlation()
    if q.get("suspect_lot"):
        f = q["lot_findings"][0]
        risks.append(
            {
                "module": "quality",
                "severity": "medium",
                "title": f"{f['defect_name']} {f['lift']}x on resin lot {q['suspect_lot']}",
                "detail": f"{f['rate_pct']}% vs {f['overall_rate_pct']}% overall; {q['hold_candidates']['units_in_stock']} bumpers from this lot in stock",
            }
        )
    return risks


@lru_cache(maxsize=1)
def briefing() -> dict[str, Any]:
    oee = mes.get_oee()
    day = oee["window"][1]
    return {
        "as_of": TODAY.isoformat(),
        "plant": "Vidad bumper plant",
        "yesterday": day,
        "oee": {
            "molding": oee["plant_molding"],
            "plant": oee["plant_all"],
            "worst_machine": oee["worst_machine"],
            "worst_machine_oee_pct": oee["by_machine"][oee["worst_machine"]]["oee_pct"] if oee["worst_machine"] else None,
        },
        "top_losses": _losses(day),
        "risks": _risks(),
        "source": "deterministic tools over fv.* (no Gemini)",
    }
