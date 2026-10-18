from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app import materials, plans, readiness, warehouse
from app.errors import FVError
from app.store import store
from app.tools.common import TODAY, to_date

INVALID_HORIZON = "FV-MRP-001"
INVALID_DATE = "FV-MRP-002"
PLAN_NOT_FOUND = "FV-MRP-003"
PLAN_CANCELLED = "FV-MRP-004"
RUN_NOT_FOUND = "FV-MRP-005"
RESERVATION_NOT_FOUND = "FV-MR-006"
INVALID_STATUS = "FV-MR-007"

RUNS = "mrp_runs"
RESERVATIONS = readiness.RESERVATIONS
TRANSACTIONS = "material_transactions"
RESERVATION_STATUSES = ("Active", "Released")
RESIN = "PP-EPDM-T20"
TOLERANCE = 0.0001


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


# --- pure rules -----------------------------------------------------------------------------------------------------


def fmt_qty(q: float) -> str:
    return f"{q:,.3f}".rstrip("0").rstrip(".")


def order_by(required_date: str, lead_time_days: int) -> str:
    return (to_date(required_date) - timedelta(days=int(lead_time_days or 0))).isoformat()


def recommendation(net: float, unit: str, order_by_date: str) -> str | None:
    return f"Procure {fmt_qty(net)} {unit} before {order_by_date}" if net > 0 else None


def next_run_id(existing: list[str], horizon_start: date) -> str:
    prefix = f"MRP-{horizon_start:%Y-%m}-"
    taken = [int(x[len(prefix) :]) for x in existing if x.startswith(prefix) and x[len(prefix) :].isdigit()]
    return f"{prefix}{max(taken, default=0) + 1:03d}"


def variance_status(variance: float) -> str:
    if abs(variance) < TOLERANCE:
        return "NORMAL"
    return "OVER_CONSUMPTION" if variance > 0 else "UNDER_CONSUMPTION"


def parse_horizon(horizon_start: str | None, horizon_end: str | None) -> tuple[date, date]:
    fields = []
    parsed: dict[str, date] = {}
    for name, raw in (("horizon_start", horizon_start), ("horizon_end", horizon_end)):
        if not raw:
            continue
        try:
            parsed[name] = date.fromisoformat(raw[:10])
        except ValueError:
            fields.append({"field": name, "message": "Not a date."})
    if fields:
        raise FVError(INVALID_DATE, "The MRP horizon is not a valid date.", {"fields": fields})
    start = parsed.get("horizon_start", TODAY)
    end = parsed.get("horizon_end", start + timedelta(days=readiness.HORIZON_DAYS))
    if end < start:
        raise FVError(
            INVALID_HORIZON,
            "The end of the planning horizon cannot be before its start.",
            {
                "fields": [{"field": "horizon_end", "message": "Must be on or after horizon_start."}],
                "horizon_start": start.isoformat(),
                "horizon_end": end.isoformat(),
            },
        )
    return start, end


def net_results(gross: list[dict[str, Any]], ctx: readiness.Ctx) -> list[dict[str, Any]]:
    out = []
    for g in gross:
        mid = g["material_id"]
        st = ctx.stock.get(mid, {})
        counted = sum(float(r["qty"]) for r in ctx.incoming.get(mid, []) if str(r["expected_date"])[:10] <= g["required_date"])
        on_hand, reserved = float(st.get("on_hand_qty", 0.0)), float(st.get("reserved_qty", 0.0))
        available = round(on_hand - reserved + counted, 4)
        net = round(max(g["gross_qty"] - available, 0.0), 4)
        m = ctx.meta.get(mid, {})
        lead = int(m.get("lead_time_days") or 0)
        obd = order_by(g["required_date"], lead)
        out.append(
            {
                "material_id": mid,
                "material_name": g["material_name"],
                "material_group": g.get("material_group"),
                "color": g["color"],
                "unit": g["unit"],
                "gross_qty": round(g["gross_qty"], 4),
                "on_hand_qty": round(on_hand, 4),
                "reserved_qty": round(reserved, 4),
                "incoming_counted_qty": round(counted, 4),
                "available_qty": available,
                "net_qty": net,
                "status": readiness.status_of(g["gross_qty"], available),
                "required_date": g["required_date"],
                "order_by_date": obd,
                "order_late": net > 0 and obd < TODAY.isoformat(),
                "lead_time_days": lead,
                "default_vendor_id": m.get("default_vendor_id"),
                "unit_cost_idr": m.get("unit_cost_idr"),
                "sources": g["sources"],
                "recommendation": recommendation(net, g["unit"], obd),
            }
        )
    return sorted(out, key=lambda r: (-r["net_qty"], r["material_id"]))


# --- MRP run --------------------------------------------------------------------------------------------------------


def _plans_for(sid: str, start: date, end: date, plan_ids: list[str] | None) -> list[dict[str, Any]]:
    if plan_ids is None:
        return readiness.session_plans(sid, start, end) or readiness.oem_plans(sid, start, end)
    out = []
    for pid in plan_ids:
        try:
            p = readiness.resolve(sid, pid)
        except FVError as e:
            if e.code == readiness.PLAN_NOT_FOUND:
                raise FVError(PLAN_NOT_FOUND, f"Plan {pid} does not exist in this session.", {"plan_id": pid}) from e
            raise
        if p["plan_status"] == "Cancelled":
            raise FVError(PLAN_CANCELLED, f"Production plan {p['plan_number']} is cancelled and cannot feed an MRP run.", {"plan_id": pid})
        out.append(p)
    return out


def _key(sid: str, doc_id: str) -> str:
    return f"{sid}:{doc_id}"


def run_mrp(
    sid: str, *, horizon_start: str | None = None, horizon_end: str | None = None, plan_ids: list[str] | None = None, user: str = "You"
) -> dict[str, Any]:
    start, end = parse_horizon(horizon_start, horizon_end)
    selected = _plans_for(sid, start, end, plan_ids)
    ctx = readiness.context(sid)
    gross: dict[str, dict[str, Any]] = {}
    for p in selected:
        for r, need, _ in readiness.plan_needs(ctx, p):
            g = gross.get(r["material_id"])
            if g is None:
                gross[r["material_id"]] = {**r, "gross_qty": r["required_qty"], "required_date": need, "sources": [p["plan_number"]]}
                continue
            g["gross_qty"] += r["required_qty"]
            g["required_date"] = min(g["required_date"], need)
            if p["plan_number"] not in g["sources"]:
                g["sources"].append(p["plan_number"])
    results = net_results(list(gross.values()), ctx)
    run_id = next_run_id([r["run_id"] for r in store().where(RUNS, "session_id", sid)], start)
    run = {
        "run_id": run_id,
        "session_id": sid,
        "horizon_start": start.isoformat(),
        "horizon_end": end.isoformat(),
        "plan_ids": [p["plan_id"] for p in selected],
        "plan_numbers": [p["plan_number"] for p in selected],
        "created_at": _now(),
        "created_by": user,
        "total_materials": len(results),
        "shortage_materials": sum(1 for r in results if r["net_qty"] > 0),
        "results": results,
    }
    store().set(RUNS, _key(sid, run_id), run)
    return _public(run)


def _public(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "session_id"}


def _runs(sid: str) -> list[dict[str, Any]]:
    return sorted(store().where(RUNS, "session_id", sid), key=lambda r: (r["created_at"], r["run_id"]), reverse=True)


def list_runs(sid: str) -> list[dict[str, Any]]:
    return [{k: v for k, v in _public(r).items() if k != "results"} for r in _runs(sid)]


def get_run(sid: str, run_id: str) -> dict[str, Any]:
    doc = store().get(RUNS, _key(sid, run_id))
    if doc is None:
        raise FVError(RUN_NOT_FOUND, f"MRP run {run_id} does not exist in this session.", {"run_id": run_id})
    return _public(doc)


def latest_run(sid: str) -> dict[str, Any] | None:
    runs = _runs(sid)
    return _public(runs[0]) if runs else None


# --- reservations (MR-R42) ------------------------------------------------------------------------------------------


def _ledger(sid: str, r: dict[str, Any], kind: str, qty: float, free_after: float) -> None:
    n = len(store().where(TRANSACTIONS, "session_id", sid)) + 1
    materials.record_transaction(
        sid,
        {
            "txn_id": f"TXN-S-{n:05d}",
            "date": TODAY.isoformat(),
            "material_id": r["material_id"],
            "warehouse_id": r["warehouse_id"],
            "type": kind,
            "qty": round(qty, 4),
            "balance_after": round(free_after, 4),
            "ref": f"{r['reservation_id']} {r['wo_id']}",
        },
    )


def _free(sid: str, material_id: str, warehouse_id: str) -> float:
    rows = materials.stock(sid, material_id=material_id, warehouse_id=warehouse_id)
    return sum(float(x["on_hand_qty"]) - float(x["reserved_qty"]) for x in rows)


def _reservations(sid: str) -> list[dict[str, Any]]:
    return sorted(store().where(RESERVATIONS, "session_id", sid), key=lambda r: (r["created_at"], r["reservation_id"]), reverse=True)


def reserve_work_order(sid: str, plan_id: str, wo_id: str) -> list[dict[str, Any]]:
    existing = [r for r in _reservations(sid) if r["wo_id"] == wo_id and r["status"] == "Active"]
    if existing:
        return existing
    check = readiness.check_work_order(sid, plan_id, wo_id)
    ctx_stock = readiness.context(sid).stock
    out = []
    n = len(store().where(RESERVATIONS, "session_id", sid))
    for m in check["materials"]:
        qty = round(min(float(m["required_qty"]), max(float(m["on_hand_qty"]), 0.0)), 4)
        if qty <= 0:
            continue
        whs = ctx_stock.get(m["material_id"], {}).get("warehouses") or [("WH-RM", 0.0)]
        wh = max(whs, key=lambda x: x[1])[0]
        n += 1
        r = {
            "reservation_id": f"RSV-{n:05d}",
            "session_id": sid,
            "plan_id": plan_id,
            "plan_number": check["plan_number"],
            "wo_id": wo_id,
            "material_id": m["material_id"],
            "material_name": m["material_name"],
            "color": m["color"],
            "unit": m["unit"],
            "warehouse_id": wh,
            "qty": qty,
            "status": "Active",
            "created_at": _now(),
            "released_at": None,
        }
        store().set(RESERVATIONS, _key(sid, r["reservation_id"]), r)
        _ledger(sid, r, "Reserve", -qty, _free(sid, r["material_id"], wh))
        out.append(r)
    return out


def release_reservation(sid: str, reservation_id: str) -> dict[str, Any]:
    r = store().get(RESERVATIONS, _key(sid, reservation_id))
    if r is None:
        raise FVError(RESERVATION_NOT_FOUND, f"Reservation {reservation_id} does not exist in this session.", {"reservation_id": reservation_id})
    if r["status"] != "Active":
        return r
    r.update(status="Released", released_at=_now())
    store().set(RESERVATIONS, _key(sid, reservation_id), r)
    _ledger(sid, r, "Release", float(r["qty"]), _free(sid, r["material_id"], r["warehouse_id"]))
    return r


def list_reservations(sid: str, status: str | None = None) -> list[dict[str, Any]]:
    if status and status not in RESERVATION_STATUSES:
        raise FVError(INVALID_STATUS, f"Reservation status {status} is not valid.", {"status": status, "valid": list(RESERVATION_STATUSES)})
    return [r for r in _reservations(sid) if not status or r["status"] == status]


# --- consumption variance (MR-R43) ----------------------------------------------------------------------------------


def variance_row(planned: float, actual: float) -> dict[str, Any]:
    variance = round(actual - planned, 4)
    return {
        "planned_qty": round(planned, 4),
        "actual_qty": round(actual, 4),
        "variance_qty": variance,
        "variance_pct": round(variance / planned * 100, 2) if planned > 0 else 0.0,
        "status": variance_status(variance),
    }


def _resin_per_unit(sku: str) -> float:
    item = next((i for i in materials.active_bom(sku)["items"] if i["material_id"] == RESIN and i.get("color") is None), None)
    return readiness.required_for(item["qty_per_unit"], 1, item.get("scrap_pct")) if item else 0.0


def _history() -> tuple[list[dict[str, Any]], str | None, str | None]:
    days = warehouse.query("SELECT DISTINCT production_date AS d FROM fv.fact_production WHERE resin_kg > 0 ORDER BY d DESC LIMIT 14")
    if not days:
        return [], None, None
    lo, hi = to_date(days[-1]["d"]), to_date(days[0]["d"])
    m = materials.get_material(RESIN)
    rows = []
    for r in warehouse.query(
        "SELECT machine_id, sku, SUM(resin_kg) AS kg, SUM(output_quantity) AS good FROM fv.fact_production "
        "WHERE resin_kg > 0 AND production_date >= :d0 GROUP BY machine_id, sku ORDER BY machine_id, sku",
        {"d0": lo},
    ):
        planned = float(r["good"] or 0) * _resin_per_unit(r["sku"])
        rows.append(
            {
                "ref": f"{r['machine_id']} {r['sku']}",
                "wo_id": None,
                "machine_id": r["machine_id"],
                "sku": r["sku"],
                "material_id": RESIN,
                "material_name": m["material_name"],
                "unit": m["unit"],
                "good_qty": int(r["good"] or 0),
                **variance_row(planned, float(r["kg"] or 0)),
            }
        )
    return rows, lo.isoformat(), hi.isoformat()


def _work_order(sid: str, wo_id: str) -> dict[str, Any]:
    w = next((x for x in store().where(plans.WOS, "session_id", sid) if x["wo_id"] == wo_id), None)
    if w is None:
        raise FVError(readiness.WO_NOT_FOUND, f"Work order {wo_id} does not exist in this session.", {"wo_id": wo_id})
    return w


def _wo_rows(sid: str, wo_id: str) -> tuple[list[dict[str, Any]], str | None, str | None]:
    w = _work_order(sid, wo_id)
    p = readiness.resolve(sid, w["plan_id"])
    reqs = [r for r in readiness.wo_requirements(readiness.context_light(), p, w) if r["material_id"] == RESIN]
    lo = str(w["planned_start"])[:10] if w.get("planned_start") else None
    hi = str(w["planned_end"])[:10] if w.get("planned_end") else lo
    actual = 0.0
    if lo and hi and w.get("machine_id"):
        got = warehouse.query(
            "SELECT SUM(resin_kg) AS kg FROM fv.fact_production WHERE machine_id = :m AND sku = :s AND production_date BETWEEN :lo AND :hi",
            {"m": w["machine_id"], "s": w["sku"], "lo": to_date(lo), "hi": to_date(hi)},
        )
        actual = float((got[0]["kg"] if got else 0) or 0)
    rows = [
        {
            "ref": wo_id,
            "wo_id": wo_id,
            "machine_id": w.get("machine_id"),
            "sku": w["sku"],
            "material_id": r["material_id"],
            "material_name": r["material_name"],
            "unit": r["unit"],
            "good_qty": None,
            **variance_row(float(r["required_qty"]), actual),
        }
        for r in reqs
    ]
    return rows, lo, hi


def consumption_variance(sid: str, wo_id: str | None = None) -> dict[str, Any]:
    rows, lo, hi = _wo_rows(sid, wo_id) if wo_id else _history()
    summary = {s: sum(1 for r in rows if r["status"] == s) for s in ("NORMAL", "OVER_CONSUMPTION", "UNDER_CONSUMPTION")}
    note = "Actual use is recorded for resin only, from fv.fact_production.resin_kg; planned use is good output x BOM qty per unit x (1 + scrap %)."
    if wo_id and not rows:
        note = "Actual use is recorded for resin on molding work orders only; this work order has no resin requirement."
    elif wo_id and all(r["actual_qty"] == 0 for r in rows):
        note = "No production is recorded for this work order yet, so its planned use shows as under consumption until it runs."
    return {
        "wo_id": wo_id,
        "from": lo,
        "to": hi,
        "tolerance": TOLERANCE,
        "tolerance_pct": None,
        "rows": rows,
        "summary": summary,
        "note": note,
        "source": "fv.fact_production, active BOM",
    }


# --- reset ----------------------------------------------------------------------------------------------------------


def clear(sid: str) -> None:
    for d in store().where(readiness.CHECKS, "session_id", sid):
        store().delete(readiness.CHECKS, d["check_key"])
    for d in store().where(RUNS, "session_id", sid):
        store().delete(RUNS, _key(sid, d["run_id"]))
    for d in store().where(RESERVATIONS, "session_id", sid):
        store().delete(RESERVATIONS, _key(sid, d["reservation_id"]))
    for d in store().where(TRANSACTIONS, "session_id", sid):
        store().delete(TRANSACTIONS, _key(sid, d["txn_id"]))
