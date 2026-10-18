from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app import effects, materials, plans
from app.errors import FVError
from app.store import store
from app.tools import planning
from app.tools.common import TODAY

PLAN_NOT_FOUND = "FV-MR-001"
WO_NOT_FOUND = "FV-MR-002"
INVALID_RANGE = "FV-MR-003"
PLAN_CANCELLED = "FV-MR-004"
UNKNOWN_BOM_MATERIAL = "FV-MR-005"

CHECKS = "material_checks"
RESERVATIONS = "material_reservations"
HORIZON_DAYS = 30
FORMULA = "Available = On Hand - Reserved + Incoming"
LATE_NOTE = "arrives after need date"
OEM_PREFIX = "OEM-"
WO_PROCESS = {"Molding": "Molding", "Painting": "Painting", "Assembly": "Assembly", "Packing": "Assembly"}
LATE_KEY = "T99"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


# --- pure rules -----------------------------------------------------------------------------------------------------


def required_for(qty_per_unit: float, qty: float, scrap_pct: float | None) -> float:
    return float(qty_per_unit) * float(qty) * (1 + float(scrap_pct or 0) / 100)


def status_of(required: float, available: float) -> str:
    if required - available <= 0:
        return "READY"
    return "PARTIAL" if available > 0 else "SHORTAGE"


def summarise(rows: list[dict[str, Any]]) -> tuple[str, float]:
    if not rows:
        return "NOT_CHECKED", 0.0
    ready = sum(1 for r in rows if r["status"] == "READY")
    if ready == len(rows):
        status = "READY"
    elif ready:
        status = "PARTIAL"
    else:
        status = "SHORTAGE"
    return status, round(ready / len(rows) * 100, 1)


def collapse(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:
        m = merged.get(r["material_id"])
        if m is None:
            merged[r["material_id"]] = {**r, "sources": [dict(s) for s in r["sources"]]}
            continue
        m["required_qty"] += r["required_qty"]
        for s in r["sources"]:
            hit = next((x for x in m["sources"] if x["sku"] == s["sku"] and x["color"] == s["color"]), None)
            if hit:
                hit["qty"] += s["qty"]
            else:
                m["sources"].append(dict(s))
    return [{**m, "required_qty": round(m["required_qty"], 4)} for m in merged.values()]


def scale(parts: dict[str | None, int], total: int) -> dict[str | None, int]:
    have = sum(parts.values())
    if have <= 0 or total <= 0:
        return {}
    raw = {k: total * v / have for k, v in parts.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: total - sum(out.values())]:
        out[k] += 1
    return {k: v for k, v in out.items() if v > 0}


def line_parts(line: dict[str, Any], demand: list[dict[str, Any]]) -> list[dict[str, Any]]:
    planned = int(line["planned_qty"])
    if planned <= 0:
        return []
    colored: dict[str | None, int] = {}
    for d in demand:
        if d.get("line_id") == line["line_id"] and d.get("color"):
            colored[d["color"]] = colored.get(d["color"], 0) + int(d["qty"])
    total = sum(colored.values())
    if total >= planned:
        colored = scale(colored, planned)
    else:
        colored[None] = planned - total
    return [{"sku": line["sku"], "color": c, "qty": q} for c, q in colored.items() if q > 0]


def material_row(
    req: dict[str, Any],
    required_date: str,
    stock: dict[str, Any],
    incoming: list[dict[str, Any]],
    *,
    own_reserved: float = 0.0,
    used_before: float = 0.0,
) -> dict[str, Any]:
    required = float(req["required_qty"])
    on_hand = float(stock.get("on_hand_qty", 0.0))
    reserved = max(float(stock.get("reserved_qty", 0.0)) - own_reserved, 0.0)
    inc = [
        {"po_id": r["po_id"], "qty": float(r["qty"]), "expected_date": str(r["expected_date"])[:10], "counted": str(r["expected_date"])[:10] <= required_date}
        for r in sorted(incoming, key=lambda r: (str(r["expected_date"]), str(r["po_id"])))
    ]
    counted = sum(r["qty"] for r in inc if r["counted"])
    available = on_hand - reserved + counted
    if used_before > 0:
        available = max(available - used_before, 0.0)
    available = round(available, 4)
    shortage = round(max(required - available, 0.0), 4)
    status = status_of(required, available)
    safety = float(stock.get("safety_stock") or 0.0)
    notes = [f"{r['po_id']} ({r['qty']:g} {req['unit']}, expected {r['expected_date']}) {LATE_NOTE}" for r in inc if not r["counted"]]
    if used_before > 0:
        notes.append(f"{round(used_before, 4):g} {req['unit']} is taken by earlier work orders")
    return {
        **{k: req.get(k) for k in ("material_id", "material_name", "material_group", "color", "unit", "process")},
        "required_qty": round(required, 4),
        "required_date": required_date,
        "on_hand_qty": round(on_hand, 4),
        "reserved_qty": round(reserved, 4),
        "incoming_qty": round(sum(r["qty"] for r in inc), 4),
        "incoming_counted_qty": round(counted, 4),
        "incoming": inc,
        "used_before_qty": round(used_before, 4),
        "available_qty": available,
        "shortage_qty": shortage,
        "status": status,
        "at_risk": status == "READY" and available - required < safety,
        "safety_stock": safety,
        "note": "; ".join(notes) or None,
        "sources": req.get("sources", []),
    }


def parse_range(date_from: str | None, date_to: str | None, code: str = INVALID_RANGE, days: int = HORIZON_DAYS) -> tuple[date, date]:
    fields: list[dict[str, str]] = []
    parsed: dict[str, date] = {}
    for name, raw in (("from", date_from), ("to", date_to)):
        if not raw:
            continue
        try:
            parsed[name] = date.fromisoformat(raw[:10])
        except ValueError:
            fields.append({"field": name, "message": "Not a date."})
    if fields:
        raise FVError(code, "The date range is not valid.", {"fields": fields})
    start = parsed.get("from", TODAY)
    end = parsed.get("to", start + timedelta(days=days))
    if end < start:
        raise FVError(code, "The end of the range is before its start.", {"fields": [{"field": "to", "message": "Must be on or after the start."}]})
    return start, end


# --- context --------------------------------------------------------------------------------------------------------


@dataclass
class Ctx:
    sid: str
    meta: dict[str, dict[str, Any]]
    stock: dict[str, dict[str, Any]]
    incoming: dict[str, list[dict[str, Any]]]
    reservations: list[dict[str, Any]]
    items: dict[tuple[str, str | None], list[dict[str, Any]]] = field(default_factory=dict)
    schedule: list[dict[str, Any]] | None = None


def context(sid: str, extra_receipts: list[dict[str, Any]] | None = None) -> Ctx:
    agg: dict[str, dict[str, Any]] = {}
    for r in materials.stock(sid):
        a = agg.setdefault(r["material_id"], {"on_hand_qty": 0.0, "reserved_qty": 0.0, "safety_stock": 0.0, "warehouses": []})
        a["on_hand_qty"] += float(r["on_hand_qty"] or 0)
        a["reserved_qty"] += float(r["reserved_qty"] or 0)
        a["safety_stock"] = max(a["safety_stock"], float(r.get("safety_stock") or 0))
        a["warehouses"].append((r["warehouse_id"], float(r["on_hand_qty"] or 0)))
    inc: dict[str, list[dict[str, Any]]] = {}
    rows = list(materials.incoming(sid))
    rows += [{**r, "po_id": r.get("po_id") or "proposed", "material_id": r.get("material_id") or "PP-EPDM-T20"} for r in extra_receipts or []]
    for r in rows:
        inc.setdefault(r["material_id"], []).append(r)
    res = [r for r in store().where(RESERVATIONS, "session_id", sid) if r["status"] == "Active"] if sid else []
    return Ctx(sid=sid, meta={m["material_id"]: m for m in materials.list_materials()}, stock=agg, incoming=inc, reservations=res)


def _items(ctx: Ctx, sku: str, color: str | None) -> list[dict[str, Any]]:
    k = (sku, color)
    if k not in ctx.items:
        ctx.items[k] = [i for i in materials.bom_items_for(sku, color) if i.get("material_id")]
    return ctx.items[k]


def _expand(lines: list[dict[str, Any]]) -> list[tuple[str, str | None, float]]:
    out: list[tuple[str, str | None, float]] = []
    for ln in lines:
        q = float(ln["qty"])
        if q <= 0:
            continue
        if ln.get("color") is None:
            split = materials.color_split(ln["sku"], q)
            if split:
                out.extend((ln["sku"], c, float(n)) for c, n in split.items() if n > 0)
                continue
        out.append((ln["sku"], ln.get("color"), q))
    return out


def _explode(ctx: Ctx, lines: list[dict[str, Any]], processes: set[str] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sku, color, qty in _expand(lines):
        for it in _items(ctx, sku, color):
            proc = it.get("process")
            if processes is not None and WO_PROCESS.get(str(proc), str(proc)) not in processes:
                continue
            mid = it["material_id"]
            m = ctx.meta.get(mid)
            if m is None:
                raise FVError(
                    UNKNOWN_BOM_MATERIAL,
                    f"The active BOM of {sku} uses material {mid}, which is not in the material master.",
                    {"sku": sku, "material_id": mid},
                )
            rows.append(
                {
                    "material_id": mid,
                    "material_name": m["material_name"],
                    "material_group": m.get("material_group"),
                    "color": m.get("color"),
                    "unit": it.get("unit") or m["unit"],
                    "process": proc,
                    "required_qty": required_for(it["qty_per_unit"], qty, it.get("scrap_pct")),
                    "sources": [{"sku": sku, "color": color, "qty": qty}],
                }
            )
    return collapse(rows)


def explode(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _explode(context_light(), lines)


def context_light() -> Ctx:
    return Ctx(sid="", meta={m["material_id"]: m for m in materials.list_materials()}, stock={}, incoming={}, reservations=[])


# --- demand: session plans and the OEM monthly plan -----------------------------------------------------------------


def _month_end(m: date) -> date:
    return m.replace(day=calendar.monthrange(m.year, m.month)[1])


def oem_plan(month: date, demand_change_pct: dict[str, float] | None = None) -> dict[str, Any] | None:
    m = month.replace(day=1)
    qty = planning.monthly_plan(m, demand_change_pct or None)
    if not any(q > 0 for q in qty.values()):
        return None
    lines = [
        {"line_id": f"OEM-{sku}", "sku": sku, "planned_qty": q, "required_date": None, "parts": [{"sku": sku, "color": None, "qty": q}]}
        for sku, q in qty.items()
        if q > 0
    ]
    return {
        "plan_id": f"{OEM_PREFIX}{m:%Y-%m}",
        "plan_number": f"OEM monthly plan {m:%Y-%m}",
        "period_start": m.isoformat(),
        "period_end": _month_end(m).isoformat(),
        "plan_status": "OEM",
        "origin": "oem",
        "lines": lines,
        "work_orders": [],
    }


def _normalize(p: dict[str, Any]) -> dict[str, Any]:
    lines = [{**ln, "parts": line_parts(ln, p.get("demand", []))} for ln in p["lines"]]
    return {
        "plan_id": p["plan_id"],
        "plan_number": p["plan_number"],
        "period_start": p["period_start"],
        "period_end": p["period_end"],
        "plan_status": p["status"],
        "origin": p.get("origin", "user"),
        "lines": lines,
        "work_orders": p.get("work_orders", []),
    }


def resolve(sid: str, plan_id: str) -> dict[str, Any]:
    if plan_id.startswith(OEM_PREFIX):
        try:
            month = date.fromisoformat(f"{plan_id[len(OEM_PREFIX) :]}-01")
        except ValueError:
            month = None
        p = oem_plan(month, effects.overrides(sid).demand_change_pct) if month else None
        if p is None:
            raise FVError(PLAN_NOT_FOUND, f"There is no OEM monthly plan {plan_id}.", {"plan_id": plan_id})
        return p
    try:
        return _normalize(plans.get_plan(sid, plan_id))
    except FVError as e:
        if e.code == plans.PLAN_NOT_FOUND:
            raise FVError(PLAN_NOT_FOUND, f"Production plan {plan_id} does not exist in this session.", {"plan_id": plan_id}) from e
        raise


def session_plans(sid: str, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    out = []
    for h in plans.list_plans(sid, period_from=start.isoformat() if start else None, period_to=end.isoformat() if end else None):
        if h["status"] == "Cancelled" or not h["total_planned"]:
            continue
        out.append(_normalize(plans.get_plan(sid, h["plan_id"])))
    return sorted(out, key=lambda p: (p["period_start"], p["plan_number"]))


def oem_plans(sid: str, start: date, end: date, demand_change_pct: dict[str, float] | None = None) -> list[dict[str, Any]]:
    dcp = effects.overrides(sid).demand_change_pct if demand_change_pct is None else demand_change_pct
    out = []
    m = start.replace(day=1)
    while m <= end:
        p = oem_plan(m, dcp)
        if p:
            out.append(p)
        m = _month_end(m) + timedelta(days=1)
    return out


# --- sequential consumption -----------------------------------------------------------------------------------------


def _fallback_date(p: dict[str, Any]) -> str:
    dates = [ln["required_date"] for ln in p["lines"] if ln.get("required_date")]
    return str(min(dates))[:10] if dates else p["period_start"]


def wo_key(w: dict[str, Any], p: dict[str, Any]) -> str:
    if w.get("planned_start"):
        return str(w["planned_start"])
    line = next((ln for ln in p["lines"] if ln["line_id"] == w.get("line_id")), None)
    rd = (line or {}).get("required_date") or w.get("required_date")
    return f"{str(rd)[:10] if rd else _fallback_date(p)}{LATE_KEY}"


def _wo_parts(p: dict[str, Any], w: dict[str, Any]) -> list[dict[str, Any]]:
    line = next((ln for ln in p["lines"] if ln["line_id"] == w["line_id"]), None)
    if line is None:
        return []
    parts = {pt["color"]: int(pt["qty"]) for pt in line["parts"]}
    return [{"sku": w["sku"], "color": c, "qty": q} for c, q in scale(parts, int(w["qty"])).items()]


def wo_requirements(ctx: Ctx, p: dict[str, Any], w: dict[str, Any]) -> list[dict[str, Any]]:
    return _explode(ctx, _wo_parts(p, w), {w["process"]})


def _reserved_by(ctx: Ctx, key: str, value: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in ctx.reservations:
        if r.get(key) == value:
            out[r["material_id"]] = out.get(r["material_id"], 0.0) + float(r["qty"])
    return out


def schedule(ctx: Ctx) -> list[dict[str, Any]]:
    if ctx.schedule is None:
        rows = []
        for p in session_plans(ctx.sid) if ctx.sid else []:
            for w in p["work_orders"]:
                own = _reserved_by(ctx, "wo_id", w["wo_id"])
                uses = {r["material_id"]: max(r["required_qty"] - own.get(r["material_id"], 0.0), 0.0) for r in wo_requirements(ctx, p, w)}
                rows.append({"wo_id": w["wo_id"], "plan_id": p["plan_id"], "key": wo_key(w, p), "uses": uses})
        ctx.schedule = sorted(rows, key=lambda s: (s["key"], s["wo_id"]))
    return ctx.schedule


def used_before(sched: list[dict[str, Any]], material_id: str, key: tuple[str, str], *, skip_plan: str | None = None, skip_wo: str | None = None) -> float:
    return sum(float(s["uses"].get(material_id, 0.0)) for s in sched if (s["key"], s["wo_id"]) < key and s["plan_id"] != skip_plan and s["wo_id"] != skip_wo)


# --- checks ---------------------------------------------------------------------------------------------------------


def plan_needs(ctx: Ctx, p: dict[str, Any]) -> list[tuple[dict[str, Any], str, str]]:
    reqs = _explode(ctx, [pt for ln in p["lines"] for pt in ln["parts"]])
    starts: dict[str, str] = {}
    for w in p["work_orders"]:
        if w.get("planned_start"):
            s = str(w["planned_start"])
            starts[w["process"]] = min(starts.get(w["process"], s), s)
    fallback = _fallback_date(p)
    out = []
    for r in reqs:
        start = starts.get(WO_PROCESS.get(str(r["process"]), str(r["process"])))
        out.append((r, start[:10] if start else fallback, start or f"{fallback}{LATE_KEY}"))
    return out


def _stock(ctx: Ctx, material_id: str) -> dict[str, Any]:
    return ctx.stock.get(material_id, {"on_hand_qty": 0.0, "reserved_qty": 0.0, "safety_stock": 0.0, "warehouses": []})


def _result(p: dict[str, Any], wo_id: str | None, rows: list[dict[str, Any]], fallback: str) -> dict[str, Any]:
    status, pct = summarise(rows)
    return {
        "plan_id": p["plan_id"],
        "plan_number": p["plan_number"],
        "wo_id": wo_id,
        "status": status,
        "readiness_pct": pct,
        "required_date": min((r["required_date"] for r in rows), default=fallback),
        "checked_at": _now(),
        "period_start": p["period_start"],
        "period_end": p["period_end"],
        "origin": p["origin"],
        "formula": FORMULA,
        "total_materials": len(rows),
        "ready_materials": sum(1 for r in rows if r["status"] == "READY"),
        "short_count": sum(1 for r in rows if r["shortage_qty"] > 0),
        "materials": rows,
        "shortages": [r for r in rows if r["shortage_qty"] > 0],
    }


def evaluate_plan(ctx: Ctx, p: dict[str, Any]) -> dict[str, Any]:
    sched = schedule(ctx) if p["origin"] != "oem" else []
    own = _reserved_by(ctx, "plan_id", p["plan_id"])
    rows = []
    for r, need, key in plan_needs(ctx, p):
        mid = r["material_id"]
        used = used_before(sched, mid, (key, ""), skip_plan=p["plan_id"])
        rows.append(material_row(r, need, _stock(ctx, mid), ctx.incoming.get(mid, []), own_reserved=own.get(mid, 0.0), used_before=used))
    return _result(p, None, rows, _fallback_date(p))


def evaluate_work_order(ctx: Ctx, p: dict[str, Any], wo_id: str) -> dict[str, Any]:
    w = next((x for x in p["work_orders"] if x["wo_id"] == wo_id), None)
    if w is None:
        raise FVError(WO_NOT_FOUND, f"Work order {wo_id} is not part of production plan {p['plan_number']}.", {"plan_id": p["plan_id"], "wo_id": wo_id})
    key = wo_key(w, p)
    need = key[:10]
    sched = schedule(ctx)
    own = _reserved_by(ctx, "wo_id", wo_id)
    rows = []
    for r in wo_requirements(ctx, p, w):
        mid = r["material_id"]
        used = used_before(sched, mid, (key, wo_id), skip_wo=wo_id)
        rows.append(material_row(r, need, _stock(ctx, mid), ctx.incoming.get(mid, []), own_reserved=own.get(mid, 0.0), used_before=used))
    out = _result(p, wo_id, rows, need)
    out.update(process=w["process"], sku=w["sku"], wo_qty=w["qty"], planned_start=w.get("planned_start"))
    return out


def _check_key(sid: str, plan_id: str, wo_id: str | None = None) -> str:
    return f"{sid}:{plan_id}" if wo_id is None else f"{sid}:{plan_id}:{wo_id}"


def _save(sid: str, r: dict[str, Any]) -> None:
    k = _check_key(sid, r["plan_id"], r["wo_id"])
    store().set(CHECKS, k, {**r, "session_id": sid, "check_key": k})


def _assert_open(p: dict[str, Any]) -> None:
    if p["plan_status"] == "Cancelled":
        raise FVError(PLAN_CANCELLED, f"Production plan {p['plan_number']} is cancelled; it has no material requirement.", {"plan_id": p["plan_id"]})


def check_plan(sid: str, plan_id: str, *, save: bool = True) -> dict[str, Any]:
    p = resolve(sid, plan_id)
    _assert_open(p)
    r = evaluate_plan(context(sid), p)
    if save:
        _save(sid, r)
    return r


def check_work_order(sid: str, plan_id: str, wo_id: str, *, save: bool = True) -> dict[str, Any]:
    p = resolve(sid, plan_id)
    _assert_open(p)
    r = evaluate_work_order(context(sid), p, wo_id)
    if save:
        _save(sid, r)
    return r


def _strip(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in ("session_id", "check_key")}


def latest_check(sid: str, plan_id: str) -> dict[str, Any]:
    p = resolve(sid, plan_id)
    doc = store().get(CHECKS, _check_key(sid, plan_id))
    if doc:
        return _strip(doc)
    return {
        "plan_id": p["plan_id"],
        "plan_number": p["plan_number"],
        "wo_id": None,
        "status": "NOT_CHECKED",
        "readiness_pct": 0.0,
        "required_date": None,
        "checked_at": None,
        "period_start": p["period_start"],
        "period_end": p["period_end"],
        "origin": p["origin"],
        "formula": FORMULA,
        "total_materials": 0,
        "ready_materials": 0,
        "short_count": 0,
        "materials": [],
        "shortages": [],
    }


def latest_work_order_check(sid: str, plan_id: str, wo_id: str) -> dict[str, Any] | None:
    doc = store().get(CHECKS, _check_key(sid, plan_id, wo_id))
    return _strip(doc) if doc else None


def readiness(sid: str, date_from: str | None = None, date_to: str | None = None, *, only_problems: bool = False) -> dict[str, Any]:
    start, end = parse_range(date_from, date_to)
    ctx = context(sid)
    own = session_plans(sid, start, end)
    checks = []
    for p in sorted([*own, *oem_plans(sid, start, end)], key=lambda x: (x["period_start"], x["plan_number"])):
        r = evaluate_plan(ctx, p)
        _save(sid, r)
        checks.append(r)
    total = sum(r["total_materials"] for r in checks)
    ready = sum(r["ready_materials"] for r in checks)
    rows = [
        {
            **{k: r[k] for k in ("plan_id", "plan_number", "period_start", "period_end", "status", "readiness_pct", "short_count", "checked_at", "origin")},
            "total_materials": r["total_materials"],
            "required_date": r["required_date"],
        }
        for r in checks
        if not only_problems or r["status"] != "READY"
    ]
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "formula": FORMULA,
        "session_plans": len(own),
        "kpis": {
            "readiness_pct": round(ready / total * 100, 1) if total else 0.0,
            "plans_monitored": len(checks),
            "plans_short": sum(1 for r in checks if r["status"] == "SHORTAGE"),
            "materials_short": total - ready,
        },
        "plans": rows,
    }


def requirements(sid: str, plan_id: str | None = None) -> dict[str, Any]:
    p = resolve(sid, plan_id or f"{OEM_PREFIX}{TODAY:%Y-%m}")
    lines = [pt for ln in p["lines"] for pt in ln["parts"]]
    return {"plan_id": p["plan_id"], "plan_number": p["plan_number"], "lines": lines, "requirements": explode(lines)}


def oem_summary(o: effects.Overrides, limit: int = 5) -> dict[str, Any]:
    p = oem_plan(TODAY, o.demand_change_pct)
    if p is None:
        return {"status": "NOT_CHECKED", "plan_id": None}
    r = evaluate_plan(context("", o.extra_receipts), p)
    short = sorted(r["shortages"], key=lambda m: -m["shortage_qty"])[:limit]
    return {
        **{k: r[k] for k in ("plan_id", "plan_number", "status", "readiness_pct", "short_count", "total_materials", "required_date")},
        "shortages": [{k: m[k] for k in ("material_id", "material_name", "color", "unit", "shortage_qty", "required_date")} for m in short],
        "at_risk": [m["material_id"] for m in r["materials"] if m["at_risk"]],
        "formula": FORMULA,
    }
