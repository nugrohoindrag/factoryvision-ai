from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy.exc import SQLAlchemyError

from app import plant, warehouse
from app.errors import FVError
from app.tools import orders
from app.tools.common import TODAY, pct, working_days_in_month

log = logging.getLogger("fv")

HISTORY_SQL = """
SELECT requested_delivery_date AS d, SUM(order_qty) AS qty
FROM fv.fact_customer_order
WHERE sku = :sku AND order_status != 'Cancelled' AND requested_delivery_date < :today
GROUP BY d ORDER BY d
"""


HISTORY_BY_CUSTOMER_SQL = """
SELECT requested_delivery_date AS d, customer_id, SUM(order_qty) AS qty
FROM fv.fact_customer_order
WHERE sku = :sku AND order_status != 'Cancelled' AND requested_delivery_date < :today AND requested_delivery_date >= :start
GROUP BY d, customer_id ORDER BY d
"""
FIT_DAYS = 42
LOCAL_ENGINE = "local trend+weekday (dev)"
BQML_ENGINE = "BigQuery ML ARIMA_PLUS (fv.demand_arima)"


def _days(start: date, n: int, by_day: dict[date, float]) -> list[tuple[date, float]]:
    return [(start + timedelta(days=k), by_day.get(start + timedelta(days=k), 0.0)) for k in range(n)]


def demand_history(sku: str) -> list[tuple[date, float]]:
    rows = warehouse.query(HISTORY_SQL, {"sku": sku, "today": TODAY})
    return _days(TODAY - timedelta(days=plant.HISTORY_DAYS), plant.HISTORY_DAYS, {r["d"]: float(r["qty"]) for r in rows})


def demand_history_by_customer(sku: str, days: int = plant.HISTORY_DAYS) -> dict[str, list[tuple[date, float]]]:
    start = TODAY - timedelta(days=days)
    by: dict[str, dict[date, float]] = {}
    for r in warehouse.query(HISTORY_BY_CUSTOMER_SQL, {"sku": sku, "today": TODAY, "start": start}):
        by.setdefault(str(r["customer_id"]), {})[r["d"]] = float(r["qty"])
    return {c: _days(start, days, d) for c, d in sorted(by.items())}


def _local_forecast(hist: list[tuple[date, float]], horizon: int, fit_days: int = FIT_DAYS, origin: date = TODAY) -> list[tuple[date, float]]:
    window = hist[-fit_days:]
    rows, ys = [], []
    for k, (d, q) in enumerate(window):
        if not plant.is_working_day(d):
            continue
        dummies = [1.0 if d.weekday() == w else 0.0 for w in range(1, 6)]
        rows.append([1.0, float(k), *dummies])
        ys.append(q)
    coef, *_ = np.linalg.lstsq(np.asarray(rows), np.asarray(ys), rcond=None)
    out = []
    for h in range(1, horizon + 1):
        d = origin + timedelta(days=h - 1)
        if not plant.is_working_day(d):
            out.append((d, 0.0))
            continue
        x = [1.0, float(len(window) - 1 + h)] + [1.0 if d.weekday() == w else 0.0 for w in range(1, 6)]
        out.append((d, max(0.0, float(np.dot(coef, x)))))
    return out


def _bqml_forecast(sku: str, horizon: int) -> list[tuple[date, float]] | None:
    try:
        rows = warehouse.bqml_forecast(sku, horizon)
    except SQLAlchemyError as e:
        log.warning(
            "BQML forecast unavailable (%s: %s); falling back to the local model. sql: %s", type(e).__name__, str(e)[:300], warehouse.BQML_FORECAST_SQL.strip()
        )
        return None
    return [(r["d"], max(0.0, float(r["q"])) if plant.is_working_day(r["d"]) else 0.0) for r in rows]


def daily_forecast(sku: str, hist: list[tuple[date, float]], horizon: int, fit_days: int = FIT_DAYS) -> tuple[list[tuple[date, float]], str]:
    fc = _bqml_forecast(sku, horizon) if warehouse.is_bigquery() else None
    if fc:
        return fc, BQML_ENGINE
    return _local_forecast(hist, horizon, fit_days), LOCAL_ENGINE


CALENDAR_NOTES = [
    f"Lebaran (Eid al-Fitr) expected {plant.LEBARAN.isoformat()}; MPV call-offs historically ramp in the 6-8 weeks before",
    "Rainy season Nov-Mar",
]


def bq_forecast(sku: str = "FB-ARKA", horizon_weeks: int = 6) -> dict[str, Any]:
    if sku not in plant.PRODUCT_BY_SKU:
        return FVError("FV-DATA-001", f"unknown sku {sku}", {"valid_skus": plant.SKUS}).tool_result()
    horizon = horizon_weeks * 7
    hist = demand_history(sku)
    fc, engine = daily_forecast(sku, hist, horizon)
    last7 = sum(q for _, q in hist[-7:]) / 7
    weeks: list[dict[str, Any]] = []
    for w in range(horizon_weeks):
        chunk = fc[w * 7 : (w + 1) * 7]
        mean = sum(q for _, q in chunk) / len(chunk)
        weeks.append(
            {
                "week": w + 1,
                "week_start": chunk[0][0].isoformat(),
                "total_qty": round(sum(q for _, q in chunk)),
                "daily_mean": round(mean, 1),
                "change_vs_last7_pct": pct(mean / last7 - 1),
            }
        )
    peak = max(weeks, key=lambda x: x["daily_mean"])
    hist_weekly = [
        {"week_start": hist[k][0].isoformat(), "total_qty": round(sum(q for _, q in hist[k : k + 7]))}
        for k in range(plant.HISTORY_DAYS % 7, plant.HISTORY_DAYS, 7)
    ]
    return {
        "sku": sku,
        "engine": engine,
        "as_of": TODAY.isoformat(),
        "horizon_weeks": horizon_weeks,
        "last7_daily_mean": round(last7, 1),
        "change_by_final_week_pct": weeks[-1]["change_vs_last7_pct"],
        "peak_week": peak["week"],
        "peak_change_pct": peak["change_vs_last7_pct"],
        "weekly_forecast": weeks,
        "weekly_history": hist_weekly[-8:],
        "calendar_notes": CALENDAR_NOTES,
        "source": "fv.fact_customer_order (Completed/Shipped history, Cancelled excluded)",
    }


FIRM_SQL = """
SELECT requested_delivery_date AS d, SUM(order_qty) AS qty
FROM fv.fact_customer_order
WHERE sku = :sku AND order_status NOT IN ('Cancelled', 'Completed') AND requested_delivery_date >= :today
GROUP BY d
"""


def forecast_vs_orders(
    sku: str = "FB-ARKA",
    demand_change_pct: dict[str, float] | None = None,
    horizon_weeks: int = 6,
    weekly: list[dict[str, Any]] | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    """Per forecast week: statistical forecast vs firm orders vs the customer's monthly schedule (revised by an approved PO)."""
    # `weekly` ([{week, week_start, total_qty}]) takes the forecast from a stored snapshot instead of computing it again.
    f: dict[str, Any] = {"weekly_forecast": weekly, "engine": engine} if weekly is not None else bq_forecast(sku, horizon_weeks)
    if "error" in f:
        return f
    firm = {r["d"]: float(r["qty"]) for r in warehouse.query(FIRM_SQL, {"sku": sku, "today": TODAY})}
    change = (demand_change_pct or {}).get(sku, 0.0)
    plans: dict[date, int] = {}
    weeks: list[dict[str, Any]] = []
    for w in f["weekly_forecast"]:
        start = date.fromisoformat(w["week_start"])
        days = [start + timedelta(days=k) for k in range(7)]
        sched = 0.0
        for d in days:
            m = d.replace(day=1)
            if m not in plans:
                plans[m] = orders.monthly_schedule(m).get(sku, 0)
            if plant.is_working_day(d) and plans[m]:
                sched += plans[m] / working_days_in_month(m)
        has_sched = all(plans[d.replace(day=1)] for d in days if plant.is_working_day(d))
        firm_qty: int = round(sum(firm.get(d, 0.0) for d in days))
        weeks.append(
            {
                "week": w["week"],
                "week_start": w["week_start"],
                "forecast_qty": w["total_qty"],
                "firm_qty": firm_qty,
                "schedule_qty": round(sched) if has_sched else None,
                "revised_schedule_qty": round(sched * (1 + change / 100)) if has_sched else None,
                "uncovered_qty": max(0, w["total_qty"] - firm_qty),
                "firm_coverage_pct": pct(firm_qty / w["total_qty"]) if w["total_qty"] else None,
            }
        )
    return {
        "sku": sku,
        "engine": f["engine"],
        "customer_po_change_pct": change or None,
        "weeks": weeks,
        "forecast_total": sum(int(w["forecast_qty"]) for w in weeks),
        "firm_total": sum(int(w["firm_qty"]) for w in weeks),
        "note": "Firm = open customer orders (Received to Ready to Ship). Schedule = OEM monthly plan spread over working days; blank where no plan is on file yet.",
        "source": "BQML forecast; fv.fact_customer_order; fv.fact_monthly_plan",
    }


def forecast_all(horizon_weeks: int = 6) -> list[dict[str, Any]]:
    out = []
    for sku in plant.SKUS:
        f = bq_forecast(sku, horizon_weeks)
        out.append({k: f[k] for k in ("sku", "last7_daily_mean", "change_by_final_week_pct", "peak_week", "engine")})
    return out


BACKTEST_WEEKS = 4
UNKNOWN_ENTITY = "FV-DATA-001"


def backtest(sku: str, weeks: int = BACKTEST_WEEKS) -> dict[str, Any]:
    # Fit the local model on history up to `weeks` before today, forecast those weeks, compare with what was ordered.
    if sku not in plant.PRODUCT_BY_SKU:
        raise FVError(UNKNOWN_ENTITY, f"unknown sku {sku}", {"valid_skus": plant.SKUS})
    hist = demand_history(sku)
    days = weeks * 7
    origin = TODAY - timedelta(days=days)
    fc = _local_forecast(hist[:-days], days, FIT_DAYS, origin=origin)
    actual = hist[-days:]
    rows: list[dict[str, Any]] = []
    for w in range(weeks):
        f_qty = round(sum(q for _, q in fc[w * 7 : (w + 1) * 7]))
        a_qty = round(sum(q for _, q in actual[w * 7 : (w + 1) * 7]))
        rows.append(
            {
                "week": w + 1,
                "week_start": actual[w * 7][0].isoformat(),
                "forecast_qty": f_qty,
                "actual_qty": a_qty,
                "error_qty": f_qty - a_qty,
                "abs_pct_error": pct(abs(f_qty - a_qty) / a_qty) if a_qty else None,
            }
        )
    scored = [r["abs_pct_error"] for r in rows if r["abs_pct_error"] is not None]
    f_tot, a_tot = sum(r["forecast_qty"] for r in rows), sum(r["actual_qty"] for r in rows)
    return {
        "sku": sku,
        "cutoff": origin.isoformat(),
        "weeks": rows,
        "mape_pct": round(sum(scored) / len(scored), 1) if scored else None,
        "bias_pct": pct(f_tot / a_tot - 1) if a_tot else None,
        "forecast_total": f_tot,
        "actual_total": a_tot,
    }
