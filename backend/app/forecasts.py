from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app import effects, modules, plant
from app.errors import FVError
from app.store import Doc, store
from app.tools import forecast, orders
from app.tools.common import TODAY, pct

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("fv.forecasts")

INVALID_PARAMS = "FV-FC-001"
NOT_FOUND = "FV-FC-002"
INVALID_STATUS = "FV-FC-003"
READ_ONLY = "FV-FC-004"
SKU_NOT_IN_SNAPSHOT = "FV-FC-005"
UNKNOWN_ENTITY = "FV-DATA-001"

COLLECTION = "forecast_snapshots"
ACTIVITY = "forecast_activity"
PLANS = "production_plans"
WINDOWS = (30, 60, 90)
HORIZONS = (4, 6, 8)
GENERATED, SUPERSEDED = "Generated", "Superseded"
STATUSES = (GENERATED, SUPERSEDED)
HEADER_ONLY = ("lines", "history", "history_by_customer")
RULES = "Cancelled orders are excluded, days without orders count as zero, and today (2027-02-01) is not part of the history."
SOURCE = "fv.fact_customer_order (requested delivery date, Cancelled excluded)"

_lock = threading.Lock()
_listeners: list[Callable[[str, Doc], None]] = []

Series = dict[str, Any]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _key(sid: str, forecast_id: str) -> str:
    # One store collection holds every session, and every session numbers from FC-…-001, so the doc id carries the session.
    return f"{sid}:{forecast_id}"


def on_generated(fn: Callable[[str, Doc], None]) -> None:
    _listeners.append(fn)


def validate(window_days: object, horizon_weeks: object, skus: object, per_customer: object) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[dict[str, str]] = []
    if isinstance(window_days, bool) or window_days not in WINDOWS:
        errors.append({"field": "window_days", "message": f"The history window must be one of 30, 60 or 90 days; got {window_days!r}."})
    if isinstance(horizon_weeks, bool) or horizon_weeks not in HORIZONS:
        errors.append({"field": "horizon_weeks", "message": f"The horizon must be one of 4, 6 or 8 weeks; got {horizon_weeks!r}."})
    if not isinstance(per_customer, bool):
        errors.append({"field": "per_customer", "message": "per_customer must be true or false."})
    unknown: list[str] = []
    if skus is not None:
        if not isinstance(skus, list) or not all(isinstance(s, str) for s in skus):
            errors.append({"field": "skus", "message": "skus must be a list of SKU codes, or left out for all SKUs."})
        elif not skus:
            errors.append({"field": "skus", "message": "Pick at least one SKU, or leave the filter out for all SKUs."})
        else:
            unknown = [s for s in skus if s not in plant.PRODUCT_BY_SKU]
    return errors, unknown


def weekly_buckets(daily: list[tuple[date, float]], end: date) -> list[dict[str, Any]]:
    # Seven-day buckets counted back from `end` (exclusive); the oldest bucket is partial when the window is not whole weeks.
    out: list[dict[str, Any]] = []
    stop = end
    while daily and stop > daily[0][0]:
        start = max(daily[0][0], stop - timedelta(days=7))
        qty = sum(q for d, q in daily if start <= d < stop)
        out.append({"week_start": start.isoformat(), "days": (stop - start).days, "qty": round(qty)})
        stop = start
    return out[::-1]


def history_stats(buckets: list[dict[str, Any]]) -> tuple[int, int, bool]:
    with_orders = sum(1 for b in buckets if b["qty"] > 0)
    return with_orders, len(buckets), with_orders < len(buckets)


def next_number(existing: list[str], period_start: str) -> str:
    prefix = f"FC-{period_start[:7]}-"
    used = [int(x[len(prefix) :]) for x in existing if x.startswith(prefix) and x[len(prefix) :].isdigit()]
    return f"{prefix}{max(used, default=0) + 1:03d}"


def split_by_share(total: int, weights: list[float]) -> list[int]:
    # Largest remainder, so the customer lines of a week always add up to the SKU's weekly forecast.
    s = sum(weights)
    if not weights:
        return []
    if s <= 0:
        return [total] + [0] * (len(weights) - 1)
    raw = [total * w / s for w in weights]
    out = [int(r) for r in raw]
    for k in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[: total - sum(out)]:
        out[k] += 1
    return out


def _weeks_ahead(daily: list[tuple[date, float]], horizon_weeks: int) -> list[dict[str, Any]]:
    weeks = []
    for w in range(horizon_weeks):
        chunk = daily[w * 7 : (w + 1) * 7]
        weeks.append(
            {
                "week": w + 1,
                "week_start": chunk[0][0].isoformat(),
                "qty": max(0, round(sum(q for _, q in chunk))),
                "mean": sum(q for _, q in chunk) / len(chunk),
            }
        )
    return weeks


def _summary(s: Series, weeks: list[dict[str, Any]], buckets: list[dict[str, Any]]) -> dict[str, Any]:
    last7 = sum(q for _, q in s["history"][-7:]) / 7
    change = [pct(w["mean"] / last7 - 1) if last7 else None for w in weeks]
    peak = max(range(len(weeks)), key=lambda k: weeks[k]["mean"])
    with_orders, n, short = history_stats(buckets)
    return {
        "engine": s["engine"],
        "last7_daily_mean": round(last7, 1),
        "change_by_final_week_pct": change[-1],
        "peak_week": peak + 1,
        "peak_change_pct": change[peak],
        "forecast_total": sum(w["qty"] for w in weeks),
        "history_total": sum(b["qty"] for b in buckets),
        "history_weeks_with_orders": with_orders,
        "history_weeks": n,
        "insufficient_history": short,
    }


def method_text(engine: str, window_days: int) -> str:
    if engine.startswith("BigQuery"):
        return (
            "BigQuery ML ARIMA_PLUS (fv.demand_arima) on daily demand with Indonesian holidays; weekly forecast = sum of the daily forecast, "
            f"non-working days = 0. The {window_days}-day window sets the history kept and checked in this snapshot."
        )
    fit = min(window_days, forecast.FIT_DAYS)
    return f"Linear trend + weekday pattern fitted on the last {fit} days of the {window_days}-day window; weekly forecast = sum of the daily forecast, Sundays = 0, never below zero."


def build_snapshot(forecast_id: str, sid: str, params: dict[str, Any], series: dict[str, Series], user: str, now: str) -> Doc:
    horizon = params["horizon_weeks"]
    lines: list[dict[str, Any]] = []
    history: dict[str, list[dict[str, Any]]] = {}
    by_customer: dict[str, dict[str, list[dict[str, Any]]]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for sku in params["skus"]:
        s = series[sku]
        buckets = weekly_buckets(s["history"], TODAY)
        weeks = _weeks_ahead(s["forecast"], horizon)
        history[sku] = buckets
        summaries[sku] = _summary(s, weeks, buckets)
        groups: list[tuple[str | None, list[dict[str, Any]]]] = [(None, buckets)]
        if params["per_customer"] and s.get("by_customer"):
            cust = {c: weekly_buckets(d, TODAY) for c, d in s["by_customer"].items()}
            by_customer[sku] = cust
            groups = list(cust.items())
        weights = [float(sum(b["qty"] for b in bs)) for _, bs in groups]
        for w in weeks:
            for (cid, bs), qty in zip(groups, split_by_share(w["qty"], weights), strict=True):
                with_orders, n, short = history_stats(bs)
                tail = f"{sku}-{cid}-{w['week_start']}" if cid else f"{sku}-{w['week_start']}"
                lines.append(
                    {
                        "forecast_line_id": f"{forecast_id}-{tail}",
                        "sku": sku,
                        "customer_id": cid,
                        "week": w["week"],
                        "week_start": w["week_start"],
                        "week_end": (date.fromisoformat(w["week_start"]) + timedelta(days=6)).isoformat(),
                        "forecast_qty": qty,
                        "history_weeks_with_orders": with_orders,
                        "history_weeks": n,
                        "insufficient_history": short,
                    }
                )
    engines = {s["engine"] for s in summaries.values()}
    engine = forecast.BQML_ENGINE if engines == {forecast.BQML_ENGINE} else forecast.LOCAL_ENGINE
    start = TODAY.isoformat()
    doc: Doc = {
        "forecast_id": forecast_id,
        "session_id": sid,
        "status": GENERATED,
        "superseded_by": None,
        "superseded_at": None,
        "supersedes": [],
        "window_days": params["window_days"],
        "horizon_weeks": horizon,
        "skus": list(params["skus"]),
        "per_customer": params["per_customer"],
        "engine": engine,
        "method": method_text(engine, params["window_days"]),
        "rules": RULES,
        "generated_at": now,
        "generated_by": user,
        "period_start": start,
        "period_end": (TODAY + timedelta(days=7 * horizon - 1)).isoformat(),
        "history_start": (TODAY - timedelta(days=params["window_days"])).isoformat(),
        "history_end": (TODAY - timedelta(days=1)).isoformat(),
        "line_count": len(lines),
        "insufficient_skus": [k for k, v in summaries.items() if v["insufficient_history"]],
        "insufficient_line_keys": sorted({f"{ln['sku']}|{ln['customer_id'] or ''}" for ln in lines if ln["insufficient_history"]}),
        "sku_summary": summaries,
        "calendar_notes": forecast.CALENDAR_NOTES,
        "source": SOURCE,
        "lines": lines,
        "history": history,
    }
    if by_customer:
        doc["history_by_customer"] = by_customer
    return doc


def _series(sku: str, window_days: int, horizon_weeks: int, *, per_customer: bool) -> Series:
    hist = forecast.demand_history(sku)
    daily, engine = forecast.daily_forecast(sku, hist, horizon_weeks * 7, fit_days=min(window_days, forecast.FIT_DAYS))
    s: Series = {"history": hist[-window_days:], "forecast": daily, "engine": engine}
    if per_customer:
        s["by_customer"] = forecast.demand_history_by_customer(sku, window_days)
    return s


def _same_question(a: Doc, b: Doc) -> bool:
    keys = ("period_start", "window_days", "horizon_weeks", "per_customer")
    return all(a[k] == b[k] for k in keys) and sorted(a["skus"]) == sorted(b["skus"])


def _docs(sid: str) -> list[Doc]:
    return store().where(COLLECTION, "session_id", sid)


def _header(doc: Doc) -> Doc:
    return {k: v for k, v in doc.items() if k not in HEADER_ONLY}


def generate(
    sid: str, *, window_days: int = 90, horizon_weeks: int = 6, skus: list[str] | None = None, per_customer: bool = False, user: str = "You"
) -> dict[str, Any]:
    errors, unknown = validate(window_days, horizon_weeks, skus, per_customer)
    if errors:
        raise FVError(INVALID_PARAMS, "The forecast parameters have fields to fix.", {"fields": errors})
    if unknown:
        msg = f"Unknown SKU {', '.join(unknown)}."
        raise FVError(UNKNOWN_ENTITY, msg, {"fields": [{"field": "skus", "message": msg}], "valid_skus": plant.SKUS})
    chosen = [s for s in plant.SKUS if skus is None or s in skus]
    series = {sku: _series(sku, window_days, horizon_weeks, per_customer=per_customer) for sku in chosen}
    params = {"window_days": window_days, "horizon_weeks": horizon_weeks, "skus": chosen, "per_customer": per_customer}
    with _lock:
        existing = _docs(sid)
        fid = next_number([d["forecast_id"] for d in existing], TODAY.isoformat())
        doc = build_snapshot(fid, sid, params, series, user, _now())
        replaced = [d["forecast_id"] for d in existing if d["status"] == GENERATED and _same_question(d, doc)]
        doc["supersedes"] = replaced
        store().set(COLLECTION, _key(sid, fid), doc)
        for old in replaced:
            # Only the status fields move; lines and history of the old snapshot stay exactly as stored.
            store().update(COLLECTION, _key(sid, old), {"status": SUPERSEDED, "superseded_by": fid, "superseded_at": doc["generated_at"]})
    _record(sid, doc)
    return {**doc, "used_by_plans": [], "used_by_plan_refs": []}


def _record(sid: str, doc: Doc) -> None:
    entry = {
        "session_id": sid,
        "at": doc["generated_at"],
        "event": "Forecast generated",
        "forecast_id": doc["forecast_id"],
        "generated_by": doc["generated_by"],
        "superseded": doc["supersedes"],
        "line_count": doc["line_count"],
        "insufficient_history_count": len(doc["insufficient_line_keys"]),
        "params": {k: doc[k] for k in ("window_days", "horizon_weeks", "skus", "per_customer")},
    }
    store().set(ACTIVITY, _key(sid, doc["forecast_id"]), entry)
    for fn in tuple(_listeners):
        try:
            fn(sid, _header(doc))
        except Exception:
            log.exception("forecast listener failed for %s", doc["forecast_id"])


def activity(sid: str) -> list[dict[str, Any]]:
    return sorted(store().where(ACTIVITY, "session_id", sid), key=lambda e: (e["at"], e["forecast_id"]), reverse=True)


def _status(status: str | None) -> str | None:
    if not status:
        return None
    match = next((s for s in STATUSES if s.lower() == status.strip().lower()), None)
    if match is None:
        raise FVError(INVALID_STATUS, detail={"status": status, "valid": list(STATUSES)})
    return match


def list_snapshots(sid: str, status: str | None = None) -> list[dict[str, Any]]:
    wanted = _status(status)
    docs = _docs(sid)
    if not docs:
        generate(sid, user="seed")
        docs = _docs(sid)
    docs.sort(key=lambda d: (d["generated_at"], d["forecast_id"]), reverse=True)
    return [_header(d) for d in docs if wanted is None or d["status"] == wanted]


def _plans_using(sid: str, forecast_id: str) -> list[dict[str, Any]]:
    plans = [p for p in store().where(PLANS, "session_id", sid) if p.get("forecast_id") == forecast_id]
    return sorted(
        ({"plan_id": p.get("plan_id"), "plan_number": p.get("plan_number"), "status": p.get("status")} for p in plans), key=lambda p: str(p["plan_number"])
    )


def get_snapshot(sid: str, forecast_id: str) -> dict[str, Any]:
    doc = store().get(COLLECTION, _key(sid, forecast_id))
    if not doc or doc.get("session_id") != sid:
        raise FVError(NOT_FOUND, f"Forecast {forecast_id} does not exist in this session.", {"forecast_id": forecast_id})
    refs = _plans_using(sid, forecast_id)
    return {**doc, "used_by_plans": [p["plan_number"] for p in refs], "used_by_plan_refs": refs}


def latest(sid: str) -> dict[str, Any]:
    heads = list_snapshots(sid, GENERATED)
    if not heads:
        return generate(sid, user="seed")
    return get_snapshot(sid, heads[0]["forecast_id"])


def qty_for(snapshot: dict[str, Any], sku: str, start: str, end: str) -> tuple[int, list[str]]:
    total, ids = 0, []
    for ln in snapshot.get("lines", []):
        week_end = ln.get("week_end") or (date.fromisoformat(ln["week_start"]) + timedelta(days=6)).isoformat()
        if ln["sku"] == sku and ln["week_start"] <= end and week_end >= start:
            total += int(ln["forecast_qty"])
            ids.append(ln["forecast_line_id"])
    return total, ids


def clear(sid: str) -> None:
    for d in _docs(sid):
        store().delete(COLLECTION, _key(sid, d["forecast_id"]))
    for e in store().where(ACTIVITY, "session_id", sid):
        store().delete(ACTIVITY, _key(sid, e["forecast_id"]))


def _weekly(snap: dict[str, Any], sku: str) -> list[dict[str, Any]]:
    if sku not in snap["skus"]:
        raise FVError(SKU_NOT_IN_SNAPSHOT, f"{snap['forecast_id']} does not cover {sku}.", {"skus": snap["skus"]})
    weeks: dict[str, dict[str, Any]] = {}
    for ln in snap["lines"]:
        if ln["sku"] == sku:
            w = weeks.setdefault(ln["week_start"], {"week": ln["week"], "week_start": ln["week_start"], "total_qty": 0})
            w["total_qty"] += int(ln["forecast_qty"])
    return [weeks[k] for k in sorted(weeks)]


def vs_orders(sid: str, forecast_id: str, sku: str) -> dict[str, Any]:
    snap = get_snapshot(sid, forecast_id)
    o = effects.overrides(sid)
    out = forecast.forecast_vs_orders(sku, o.demand_change_pct or None, snap["horizon_weeks"], weekly=_weekly(snap, sku), engine=snap["engine"])
    return {**out, "forecast_id": forecast_id}


def _actual_lines(sid: str) -> list[dict[str, Any]]:
    view = modules.order_view(effects.overrides(sid))
    return [
        {"sku": ln["sku"], "customer_id": h["customer_id"], "date": str(ln["requested_delivery_date"])[:10], "qty": int(ln["ordered_qty"])}
        for h in view["orders"]
        if h["status"] != "Cancelled"
        for ln in h["lines"]
        if ln["status"] != "Cancelled"
    ]


def comparison(sid: str, forecast_id: str) -> dict[str, Any]:
    snap = get_snapshot(sid, forecast_id)
    actual = _actual_lines(sid)
    weeks: list[dict[str, Any]] = []
    for ln in snap["lines"]:
        a = sum(
            x["qty"]
            for x in actual
            if x["sku"] == ln["sku"]
            and (ln["customer_id"] is None or x["customer_id"] == ln["customer_id"])
            and ln["week_start"] <= x["date"] <= ln["week_end"]
        )
        weeks.append(
            {
                "forecast_line_id": ln["forecast_line_id"],
                "sku": ln["sku"],
                "customer_id": ln["customer_id"],
                "week": ln["week"],
                "week_start": ln["week_start"],
                "forecast_qty": ln["forecast_qty"],
                "actual_qty": a,
                "variance_qty": a - ln["forecast_qty"],
                "variance_pct": pct(a / ln["forecast_qty"] - 1) if ln["forecast_qty"] else None,
                "elapsed": ln["week_end"] < TODAY.isoformat(),
                "insufficient_history": ln["insufficient_history"],
            }
        )
    rows = []
    for sku in snap["skus"]:
        mine = [w for w in weeks if w["sku"] == sku]
        f, a = sum(w["forecast_qty"] for w in mine), sum(w["actual_qty"] for w in mine)
        rows.append(
            {
                "sku": sku,
                "forecast_qty": f,
                "actual_qty": a,
                "variance_qty": a - f,
                "variance_pct": pct(a / f - 1) if f else None,
                "insufficient_history": snap["sku_summary"][sku]["insufficient_history"],
            }
        )
    return {
        "forecast_id": forecast_id,
        "status": snap["status"],
        "period_start": snap["period_start"],
        "period_end": snap["period_end"],
        "as_of": TODAY.isoformat(),
        "rows": rows,
        "weeks": weeks,
        "note": "Actual = customer orders on the book with a requested delivery in the week (orders entered in this session included, Cancelled excluded). "
        "Weeks that have not ended yet are still filling up, so a negative variance there is not yet a miss.",
        "source": "forecast snapshot lines; fv.fact_customer_order + session orders",
    }


@lru_cache(maxsize=16)
def _backtest(sku: str) -> dict[str, Any]:
    return forecast.backtest(sku)


def accuracy(sid: str, sku: str | None = None) -> dict[str, Any]:
    if sku and sku not in plant.PRODUCT_BY_SKU:
        raise FVError(UNKNOWN_ENTITY, f"unknown sku {sku}", {"valid_skus": plant.SKUS})
    rows = [_backtest(s) for s in ([sku] if sku else plant.SKUS)]
    # History before today is the same for every session, so the backtest is shared; the session only labels the answer.
    return {
        "session_id": sid,
        "method": f"Backtest: the local trend+weekday model is fitted on history up to {rows[0]['cutoff']}, forecasts the next "
        f"{forecast.BACKTEST_WEEKS} weeks, and is compared with the orders that came in. MAPE = mean of |forecast - actual| / actual per week.",
        "engine": forecast.LOCAL_ENGINE,
        "weeks": forecast.BACKTEST_WEEKS,
        "rows": rows,
        "source": SOURCE,
    }


def products() -> list[dict[str, Any]]:
    return orders.products()
