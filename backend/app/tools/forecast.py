from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy.exc import SQLAlchemyError

from app import plant, warehouse
from app.errors import FVError
from app.tools.common import TODAY, pct

log = logging.getLogger("fv")

HISTORY_SQL = """
SELECT requested_delivery_date AS d, SUM(order_qty) AS qty
FROM fv.fact_customer_order
WHERE sku = :sku AND order_status != 'Cancelled' AND requested_delivery_date < :today
GROUP BY d ORDER BY d
"""


def demand_history(sku: str) -> list[tuple[date, float]]:
    rows = warehouse.query(HISTORY_SQL, {"sku": sku, "today": TODAY})
    by_day = {r["d"]: float(r["qty"]) for r in rows}
    start = TODAY - timedelta(days=plant.HISTORY_DAYS)
    return [(start + timedelta(days=k), by_day.get(start + timedelta(days=k), 0.0)) for k in range(plant.HISTORY_DAYS)]


def _local_forecast(hist: list[tuple[date, float]], horizon: int, fit_days: int = 42) -> list[tuple[date, float]]:
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
        d = TODAY + timedelta(days=h - 1)
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


def bq_forecast(sku: str = "FB-ARKA", horizon_weeks: int = 6) -> dict[str, Any]:
    if sku not in plant.PRODUCT_BY_SKU:
        return FVError("FV-DATA-001", f"unknown sku {sku}", {"valid_skus": plant.SKUS}).tool_result()
    horizon = horizon_weeks * 7
    hist = demand_history(sku)
    fc, engine = None, "local trend+weekday (dev)"
    if warehouse.is_bigquery():
        fc = _bqml_forecast(sku, horizon)
        engine = "BigQuery ML ARIMA_PLUS (fv.demand_arima)" if fc else engine
    if not fc:
        fc = _local_forecast(hist, horizon)
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
        "calendar_notes": [
            f"Lebaran (Eid al-Fitr) expected {plant.LEBARAN.isoformat()}; MPV call-offs historically ramp in the 6-8 weeks before",
            "Rainy season Nov-Mar",
        ],
        "source": "fv.fact_customer_order (Completed/Shipped history, Cancelled excluded)",
    }


def forecast_all(horizon_weeks: int = 6) -> list[dict[str, Any]]:
    out = []
    for sku in plant.SKUS:
        f = bq_forecast(sku, horizon_weeks)
        out.append({k: f[k] for k in ("sku", "last7_daily_mean", "change_by_final_week_pct", "peak_week", "engine")})
    return out
