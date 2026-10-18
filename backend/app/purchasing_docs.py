from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any

from app import effects, materials, plant, warehouse
from app.errors import FVError
from app.store import store
from app.tools.common import TODAY, pct, to_date
from app.tools.material import delays_by_vendor

UNKNOWN_VENDOR = "FV-PUR-001"
NO_VENDOR = "FV-PUR-002"
NO_LINES = "FV-PUR-003"
BAD_QTY = "FV-PUR-004"
BAD_TRANSITION = "FV-PUR-005"
PR_NOT_FOUND = "FV-PUR-006"
NOT_SUPPLIED = "FV-PUR-007"
NOT_EARLIER = "FV-PUR-008"
OVER_RECEIPT = "FV-PUR-009"
INVALID_DATE = "FV-PUR-010"
REASON_REQUIRED = "FV-PUR-011"
PO_NOT_FOUND = "FV-PUR-012"
SEED_READ_ONLY = "FV-PUR-013"
UNKNOWN_ACTION = "FV-PUR-014"
UNKNOWN_STATUS = "FV-PUR-015"
NOT_ON_PO = "FV-PUR-016"
UNKNOWN_MATERIAL = "FV-MAT-002"

PRS = "purchase_requisitions"
POS = "purchase_orders"
GRS = "goods_receipts"

RESIN = "PP-EPDM-T20"
TOLERANCE = 0.05
TOLERANCE_PCT = 5.0
EPS = 1e-6
QUOTATION = "price on quotation"
BUYER = "Vidad bumper plant"
CURRENCY = "IDR"
SEED_PO_SQL = (
    "SELECT po_id, vendor_id, material_id, order_qty, unit_price_idr, order_date, promised_date, received_date, po_status, received_qty, lot_id "
    "FROM fv.fact_purchase_order ORDER BY po_id"
)

PR_STATUSES = ("Draft", "Submitted", "Approved", "Rejected", "Cancelled", "Converted")
PO_STATUSES = ("Draft", "Issued", "Partially Received", "Received", "Closed", "Cancelled")
PR_FLOW: dict[str, tuple[tuple[str, ...], str]] = {
    "submit": (("Draft",), "Submitted"),
    "approve": (("Submitted",), "Approved"),
    "reject": (("Submitted",), "Rejected"),
    "cancel": (("Draft", "Submitted"), "Cancelled"),
    "convert": (("Approved",), "Converted"),
}
PO_FLOW: dict[str, tuple[tuple[str, ...], str]] = {
    "issue": (("Draft",), "Issued"),
    "close": (("Received", "Partially Received"), "Closed"),
    "cancel": (("Draft", "Issued"), "Cancelled"),
}
OPEN_PR = ("Draft", "Submitted", "Approved")
OPEN_PO = ("Draft", "Issued", "Partially Received")
RECEIVING = ("Issued", "Partially Received")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _key(sid: str, doc_id: str) -> str:
    return f"{sid}:{doc_id}"


# --- pure rules -----------------------------------------------------------------------------------------------------


def next_number(existing: list[str], prefix: str, width: int = 3) -> str:
    taken = [int(x[len(prefix) :]) for x in existing if x.startswith(prefix) and x[len(prefix) :].isdigit()]
    return f"{prefix}{max(taken, default=0) + 1:0{width}d}"


def pr_prefix(d: date) -> str:
    return f"PR-{d:%y%m}-"


def po_prefix(d: date) -> str:
    return f"PO-{d:%y%m}-S-"


def gr_prefix(d: date) -> str:
    return f"GR-{d:%y%m}-S-"


LOT_PREFIX = "LOT-PPT20-S-"


def next_status(flow: dict[str, tuple[tuple[str, ...], str]], action: str, status: str, doc_id: str) -> str:
    if action not in flow:
        raise FVError(UNKNOWN_ACTION, f"{action} is not an action on {doc_id}.", {"action": action, "valid": list(flow)})
    allowed, to = flow[action]
    if status not in allowed:
        raise FVError(
            BAD_TRANSITION,
            f"{doc_id} is {status}; {action} needs it to be {' or '.join(allowed)}.",
            {"id": doc_id, "action": action, "status": status, "allowed_from": list(allowed)},
        )
    return to


def parse_date(value: object, field: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as e:
        raise FVError(INVALID_DATE, f"{field} is not a date.", {"fields": [{"field": field, "message": "Not a date.", "value": str(value)}]}) from e


def check_qty(qty: object, field: str = "qty") -> float:
    try:
        q = float(qty)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        q = 0.0
    if q <= 0:
        raise FVError(BAD_QTY, "A quantity must be greater than zero.", {"fields": [{"field": field, "message": "Must be greater than zero.", "value": qty}]})
    return round(q, 4)


def check_reason(reason: str | None, action: str) -> str:
    r = (reason or "").strip()
    if len(r) < 3:
        raise FVError(
            REASON_REQUIRED, f"A reason is required to {action}.", {"fields": [{"field": "reason", "message": "Give a reason of at least 3 characters."}]}
        )
    return r


def vendor_price(vendor: dict[str, Any], material: plant.Material) -> int:
    quoted = int(vendor.get("unit_price_idr") or 0)
    if quoted > 0 and vendor.get("category") == material.material_group:
        return quoted
    return int(material.unit_cost_idr or 0) or quoted


def price_label(price: int) -> str | None:
    return QUOTATION if price <= 0 else None


def line_value(qty: float, price: int) -> int:
    return round(float(qty) * int(price))


def choose(options: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [o for o in options if o["arrives_before_need"] in (True, None)]
    return min(ok or options, key=lambda o: (o["expected_arrival"], o["unit_price_idr"] <= 0, o["unit_price_idr"]))


def premium(best: dict[str, Any], options: list[dict[str, Any]]) -> int:
    priced = [o["po_value_idr"] for o in options if o["unit_price_idr"] > 0]
    if best["unit_price_idr"] <= 0 or not priced:
        return 0
    return int(best["po_value_idr"] - min(priced))


def receipt_limit(open_qty: float) -> float:
    return round(max(open_qty, 0.0) * (1 + TOLERANCE), 4)


def line_done(line: dict[str, Any]) -> bool:
    return float(line.get("received_qty") or 0) >= float(line["qty"]) - EPS


def status_after_receipt(lines: list[dict[str, Any]]) -> str:
    if all(line_done(ln) for ln in lines):
        return "Received"
    return "Partially Received" if any(float(ln.get("received_qty") or 0) > 0 for ln in lines) else "Issued"


def open_qty(line: dict[str, Any]) -> float:
    return round(max(float(line["qty"]) - float(line.get("received_qty") or 0), 0.0), 4)


def received_pct(lines: list[dict[str, Any]]) -> float:
    ordered = sum(float(ln["qty"]) for ln in lines)
    got = sum(min(float(ln.get("received_qty") or 0), float(ln["qty"])) for ln in lines)
    return pct(got / ordered) if ordered > 0 else 0.0


def lateness(status: str, expected: str | None, today: date = TODAY) -> tuple[bool, int]:
    if status not in RECEIVING or not expected:
        return False, 0
    late = (today - to_date(expected)).days
    return late > 0, max(late, 0)


def find_duplicate(line: dict[str, Any], open_lines: list[tuple[str, dict[str, Any]]]) -> str | None:
    for doc_id, other in open_lines:
        if other["material_id"] == line["material_id"] and other.get("need_by") == line.get("need_by") and other.get("source") == line.get("source"):
            return doc_id
    return None


def group_by_vendor(lines: list[dict[str, Any]], overrides: dict[str, str] | None = None) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for ln in lines:
        vid = (overrides or {}).get(ln["material_id"]) or ln["suggested_vendor_id"]
        out.setdefault(str(vid), []).append(ln)
    return out


def allocate(lines: list[dict[str, Any]], material_id: str, qty: float) -> float:
    left = qty
    mine = [ln for ln in lines if ln["material_id"] == material_id]
    consumed = 0.0
    for k, ln in enumerate(mine):
        take = left if k == len(mine) - 1 else min(left, open_qty(ln))
        consumed += min(take, open_qty(ln))
        ln["received_qty"] = round(float(ln.get("received_qty") or 0) + take, 4)
        left = round(left - take, 4)
        if left <= EPS:
            break
    return round(consumed, 4)


def scorecard(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    delays = [(to_date(r["received_date"]) - to_date(r["promised_date"])).days for r in rows]
    leads = [(to_date(r["received_date"]) - to_date(r["order_date"])).days for r in rows]
    return {
        "po_count": len(rows),
        "on_time_pct": pct(sum(1 for d in delays if d <= 0) / len(rows)),
        "avg_delay_days": round(sum(delays) / len(delays), 1),
        "max_delay_days": max(delays),
        "effective_lead_time_days": round(sum(leads) / len(leads), 1),
        "session_po_count": sum(1 for r in rows if r.get("origin") == "session"),
    }


# --- master data ----------------------------------------------------------------------------------------------------


def _material(material_id: str) -> plant.Material:
    m = plant.MATERIAL_BY_ID.get(material_id)
    if m is None:
        raise FVError(UNKNOWN_MATERIAL, f"Unknown material {material_id}.", {"material_id": material_id})
    return m


def _vendor(vendor_id: str) -> dict[str, Any]:
    v = plant.VENDOR_BY_ID.get(vendor_id)
    if v is None:
        raise FVError(UNKNOWN_VENDOR, f"Unknown vendor {vendor_id}.", {"vendor_id": vendor_id, "valid": list(plant.VENDOR_BY_ID)})
    return v


def _supplies(vendor_id: str, material_id: str) -> None:
    valid = plant.MATERIAL_VENDORS.get(material_id, [])
    if vendor_id not in valid:
        raise FVError(
            NOT_SUPPLIED,
            f"{plant.VENDOR_BY_ID[vendor_id]['vendor_name']} does not supply {material_id}.",
            {"vendor_id": vendor_id, "material_id": material_id, "valid": valid},
        )


def vendor_materials(vendor_id: str) -> list[str]:
    return [mid for mid, vids in plant.MATERIAL_VENDORS.items() if vendor_id in vids]


@lru_cache(maxsize=1)
def _seed_rows() -> tuple[dict[str, Any], ...]:
    return tuple(dict(r) for r in warehouse.query(SEED_PO_SQL))


def reset_cache() -> None:
    _seed_rows.cache_clear()


def _delays() -> dict[str, float]:
    return {str(k): float(v) for k, v in delays_by_vendor().items()}


# --- store access used by materials.incoming and materials.stock -----------------------------------------------------


def _session_pos(sid: str) -> list[dict[str, Any]]:
    return store().where(POS, "session_id", sid)


def _receipts(sid: str) -> list[dict[str, Any]]:
    return sorted(store().where(GRS, "session_id", sid), key=lambda g: (str(g["created_at"]), g["gr_id"]))


def open_lines(sid: str) -> list[dict[str, Any]]:
    out = []
    for po in _session_pos(sid):
        if po["status"] not in RECEIVING:
            continue
        for ln in po["lines"]:
            q = open_qty(ln)
            if q > EPS:
                out.append(
                    {"po_id": po["po_id"], "vendor_id": po["vendor_id"], "material_id": ln["material_id"], "qty": q, "promised_date": po["promised_date"]}
                )
    return out


def received_by_po(sid: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for g in _receipts(sid):
        for ln in g["lines"]:
            k = (str(g["po_id"]), str(ln["material_id"]))
            out[k] = out.get(k, 0.0) + float(ln["qty"])
    return out


def received_on_hand(sid: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for g in _receipts(sid):
        for ln in g["lines"]:
            k = (str(ln["material_id"]), str(ln["warehouse_id"]))
            out[k] = out.get(k, 0.0) + float(ln["qty"])
    return out


# --- vendors and recommendation (R01..R05) --------------------------------------------------------------------------


def _scorecards(sid: str | None) -> dict[str, dict[str, Any]]:
    rows: dict[str, list[dict[str, Any]]] = {}
    for r in _seed_rows():
        if r["received_date"] is not None:
            rows.setdefault(str(r["vendor_id"]), []).append(r)
    for po in _session_pos(sid) if sid else []:
        got = [g for g in _receipts(sid or "") if g["po_id"] == po["po_id"]]
        if got and all(line_done(ln) for ln in po["lines"]):
            last = max(str(g["received_date"]) for g in got)
            rows.setdefault(po["vendor_id"], []).append(
                {"order_date": po["order_date"], "promised_date": po["promised_date"], "received_date": last, "origin": "session"}
            )
    return {vid: card for vid, rs in rows.items() if (card := scorecard(rs))}


def _material_row(vendor: dict[str, Any], material_id: str) -> dict[str, Any]:
    m = plant.MATERIAL_BY_ID[material_id]
    price = vendor_price(vendor, m)
    return {"material_id": material_id, "material_name": m.material_name, "unit": m.unit, "unit_price_idr": price, "price_label": price_label(price)}


def _vendor_row(v: dict[str, Any], card: dict[str, Any] | None) -> dict[str, Any]:
    empty = {"po_count": 0, "on_time_pct": None, "avg_delay_days": None, "max_delay_days": None, "effective_lead_time_days": None, "session_po_count": 0}
    price = int(v.get("unit_price_idr") or 0)
    return {
        "vendor_id": v["vendor_id"],
        "vendor_name": v["vendor_name"],
        "category": v["category"],
        "quoted_lead_time_days": v["lead_time_days"],
        "unit_price_idr": price,
        "price_label": price_label(price),
        "materials": [_material_row(v, mid) for mid in vendor_materials(v["vendor_id"])],
        **(card or empty),
    }


def list_vendors(*, sid: str | None = None) -> list[dict[str, Any]]:
    cards = _scorecards(sid)
    return [_vendor_row(v, cards.get(v["vendor_id"])) for v in plant.VENDORS]


def get_vendor(vendor_id: str, *, sid: str | None = None) -> dict[str, Any]:
    v = _vendor(vendor_id)
    cards = _scorecards(sid)
    row = _vendor_row(v, cards.get(vendor_id))
    pos = [p for p in list_pos(sid or "", vendor_id=vendor_id)]
    received = [p for p in _all_pos(sid or "") if p["vendor_id"] == vendor_id and p["status"] in ("Received", "Closed") and p["received_date"]]
    received.sort(key=lambda p: (str(p["received_date"]), p["po_id"]), reverse=True)
    return {
        **row,
        "scorecard": {k: row[k] for k in ("po_count", "on_time_pct", "avg_delay_days", "max_delay_days", "effective_lead_time_days", "session_po_count")},
        "open_pos": [p for p in pos if p["status"] in OPEN_PO],
        "received_pos": received[:10],
    }


def recommend(material_id: str, qty: float, need_by: str | None = None, *, sid: str | None = None) -> dict[str, Any]:
    m = _material(material_id)
    q = check_qty(qty)
    need = parse_date(need_by, "need_by")
    vids = plant.MATERIAL_VENDORS.get(material_id, [])
    if not vids:
        raise FVError(NO_VENDOR, f"No vendor supplies {material_id}.", {"material_id": material_id})
    cards = _scorecards(sid)
    extra = effects.overrides(sid).vendor_extra_delay if sid else {}
    opts = []
    for vid in vids:
        v = plant.VENDOR_BY_ID[vid]
        card = cards.get(vid)
        avg = float(card["avg_delay_days"]) if card else 0.0
        add = float(extra.get(vid, 0.0))
        delay = max(0.0, avg) + add
        expected = (TODAY + timedelta(days=round(v["lead_time_days"] + delay))).isoformat()
        price = vendor_price(v, m)
        opts.append(
            {
                "vendor_id": vid,
                "vendor_name": v["vendor_name"],
                "quoted_lead_time_days": v["lead_time_days"],
                "avg_delay_days": round(avg, 1),
                "extra_delay_days": add,
                "expected_delay_days": round(delay, 1),
                "expected_arrival": expected,
                "arrives_before_need": (expected <= need) if need else None,
                "unit_price_idr": price,
                "price_label": price_label(price),
                "po_value_idr": line_value(q, price),
            }
        )
    best = choose(opts)
    for o in opts:
        o["recommended"] = o["vendor_id"] == best["vendor_id"]
    priced = [o for o in opts if o["unit_price_idr"] > 0]
    return {
        "material_id": material_id,
        "material_name": m.material_name,
        "unit": m.unit,
        "qty": q,
        "need_by": need,
        "options": opts,
        "recommended_vendor_id": best["vendor_id"],
        "recommended_vendor": best["vendor_name"],
        "recommended": best,
        "arrives_in_time": best["arrives_before_need"] in (True, None),
        "cheapest_vendor_id": min(priced, key=lambda o: o["unit_price_idr"])["vendor_id"] if priced else None,
        "premium_vs_cheapest_idr": premium(best, opts),
        "rule": "Expected arrival = today + quoted lead time + average delay (+ scenario delay); earliest in time wins, then the cheapest.",
        "source": "fv.fact_purchase_order, fv.dim_vendor, session POs",
    }


# --- requisitions (R06..R13) ----------------------------------------------------------------------------------------


def _pr_doc(sid: str, pr_id: str) -> dict[str, Any]:
    doc = store().get(PRS, _key(sid, pr_id))
    if doc is None:
        raise FVError(PR_NOT_FOUND, f"Requisition {pr_id} does not exist in this session.", {"pr_id": pr_id})
    return doc


def _public(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "session_id"}


def _open_demand(sid: str, skip: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    out = [(p["pr_id"], ln) for p in store().where(PRS, "session_id", sid) if p["status"] in OPEN_PR and p["pr_id"] != skip for ln in p["lines"]]
    out += [(p["po_id"], ln) for p in _session_pos(sid) if p["status"] in OPEN_PO for ln in p["lines"]]
    return out


def _pr_line(sid: str, raw: dict[str, Any], k: int, default_source: str) -> dict[str, Any]:
    mid = str(raw.get("material_id") or "")
    m = _material(mid)
    qty = check_qty(raw.get("qty"), f"lines[{k}].qty")
    need = parse_date(raw.get("need_by"), f"lines[{k}].need_by")
    vid = raw.get("suggested_vendor_id") or raw.get("vendor_id")
    if vid:
        _vendor(str(vid))
        _supplies(str(vid), mid)
    else:
        vid = recommend(mid, qty, need, sid=sid)["recommended_vendor_id"]
    return {
        "line_id": k + 1,
        "material_id": mid,
        "material_name": m.material_name,
        "unit": m.unit,
        "qty": qty,
        "need_by": need,
        "suggested_vendor_id": vid,
        "suggested_vendor_name": plant.VENDOR_BY_ID[str(vid)]["vendor_name"],
        "source": str(raw.get("source") or default_source),
        "note": raw.get("note"),
        "duplicate_of": None,
    }


def _flag(sid: str, lines: list[dict[str, Any]], skip: str | None = None) -> None:
    demand = _open_demand(sid, skip)
    for ln in lines:
        ln["duplicate_of"] = find_duplicate(ln, demand)


def _mrp_lines(sid: str, run_id: str, material_ids: list[str] | None) -> list[dict[str, Any]]:
    from app import mrp

    run = mrp.get_run(sid, run_id)
    return [
        {"material_id": r["material_id"], "qty": round(float(r["net_qty"]), 3), "need_by": r["required_date"], "note": r.get("recommendation")}
        for r in run["results"]
        if float(r["net_qty"]) > 0 and (not material_ids or r["material_id"] in material_ids)
    ]


def _plan_lines(sid: str, plan_id: str, material_ids: list[str] | None) -> list[dict[str, Any]]:
    from app import readiness

    check = readiness.check_plan(sid, plan_id)
    return [
        {
            "material_id": r["material_id"],
            "qty": round(float(r["shortage_qty"]), 3),
            "need_by": r["required_date"],
            "note": f"{r['status']} for {check['plan_number']}",
        }
        for r in check["materials"]
        if r["status"] in ("SHORTAGE", "PARTIAL") and float(r["shortage_qty"]) > 0 and (not material_ids or r["material_id"] in material_ids)
    ]


def _history(by: str, action: str, frm: str | None, to: str, reason: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"at": _now(), "by": by, "action": action, "from": frm, "to": to, "reason": reason, **extra}


def create_requisition(
    sid: str,
    *,
    lines: list[dict[str, Any]] | None = None,
    from_mrp_run: str | None = None,
    from_plan: str | None = None,
    material_ids: list[str] | None = None,
    note: str | None = None,
    user: str = "user",
) -> dict[str, Any]:
    raw: list[dict[str, Any]] = []
    source = "manual"
    if from_mrp_run:
        source = f"mrp:{from_mrp_run}"
        raw += [{**r, "source": source} for r in _mrp_lines(sid, from_mrp_run, material_ids)]
    if from_plan:
        src = f"readiness:{from_plan}"
        source = source if from_mrp_run else src
        raw += [{**r, "source": src} for r in _plan_lines(sid, from_plan, material_ids)]
    raw += list(lines or [])
    if not raw:
        raise FVError(NO_LINES, "The requisition has no lines.", {"from_mrp_run": from_mrp_run, "from_plan": from_plan, "material_ids": material_ids})
    built = [_pr_line(sid, r, k, "manual") for k, r in enumerate(raw)]
    _flag(sid, built)
    pr_id = next_number([p["pr_id"] for p in store().where(PRS, "session_id", sid)], pr_prefix(TODAY))
    doc = {
        "pr_id": pr_id,
        "session_id": sid,
        "status": "Draft",
        "created_at": _now(),
        "created_by": user,
        "source": source,
        "note": note,
        "lines": built,
        "po_ids": [],
        "history": [_history(user, "create", None, "Draft")],
    }
    store().set(PRS, _key(sid, pr_id), doc)
    return _public(doc)


def update_requisition(sid: str, pr_id: str, lines: list[dict[str, Any]] | None, user: str = "user", *, note: str | None = None) -> dict[str, Any]:
    doc = _pr_doc(sid, pr_id)
    if doc["status"] != "Draft":
        raise FVError(
            BAD_TRANSITION,
            f"{pr_id} is {doc['status']}; only a Draft requisition can be edited.",
            {"id": pr_id, "action": "edit", "status": doc["status"], "allowed_from": ["Draft"]},
        )
    if lines is not None:
        if not lines:
            raise FVError(NO_LINES, "The requisition has no lines.", {"pr_id": pr_id})
        built = [_pr_line(sid, r, k, "manual") for k, r in enumerate(lines)]
        _flag(sid, built, skip=pr_id)
        doc["lines"] = built
    if note is not None:
        doc["note"] = note
    doc["history"].append(_history(user, "edit", "Draft", "Draft"))
    store().set(PRS, _key(sid, pr_id), doc)
    return _public(doc)


def transition_requisition(sid: str, pr_id: str, action: str, *, user: str = "user", reason: str | None = None) -> dict[str, Any]:
    doc = _pr_doc(sid, pr_id)
    if action == "convert":
        raise FVError(
            UNKNOWN_ACTION, "Use convert_requisition to turn a requisition into POs.", {"action": action, "valid": ["submit", "approve", "reject", "cancel"]}
        )
    to = next_status(PR_FLOW, action, doc["status"], pr_id)
    if action == "reject":
        reason = check_reason(reason, "reject a requisition")
    doc["history"].append(_history(user, action, doc["status"], to, reason))
    doc["status"] = to
    if action == "approve":
        doc.update(approved_by=user, approved_at=_now())
    store().set(PRS, _key(sid, pr_id), doc)
    return _public(doc)


def _check_status(status: str | None, valid: tuple[str, ...]) -> None:
    if status and status not in valid:
        raise FVError(UNKNOWN_STATUS, f"{status} is not a status.", {"status": status, "valid": list(valid)})


def list_requisitions(sid: str, status: str | None = None) -> list[dict[str, Any]]:
    _check_status(status, PR_STATUSES)
    docs = [_public(d) for d in store().where(PRS, "session_id", sid) if not status or d["status"] == status]
    return sorted(docs, key=lambda d: (str(d["created_at"]), d["pr_id"]), reverse=True)


def get_requisition(sid: str, pr_id: str) -> dict[str, Any]:
    return _public(_pr_doc(sid, pr_id))


def convert_requisition(sid: str, pr_id: str, *, vendor_overrides: dict[str, str] | None = None, user: str = "user") -> dict[str, Any]:
    doc = _pr_doc(sid, pr_id)
    to = next_status(PR_FLOW, "convert", doc["status"], pr_id)
    for mid, vid in (vendor_overrides or {}).items():
        _vendor(vid)
        if any(ln["material_id"] == mid for ln in doc["lines"]):
            _supplies(vid, mid)
    po_ids = []
    for vid, group in group_by_vendor(doc["lines"], vendor_overrides).items():
        lines = [{k: ln[k] for k in ("material_id", "qty", "need_by", "source", "note")} for ln in group]
        po = create_po(sid, vendor_id=vid, lines=lines, pr_id=pr_id, note=doc.get("note"), user=user)
        po_ids.append(po["po_id"])
    doc["history"].append(_history(user, "convert", doc["status"], to, po_ids=po_ids))
    doc.update(status=to, po_ids=[*doc["po_ids"], *po_ids])
    store().set(PRS, _key(sid, pr_id), doc)
    return {"pr_id": pr_id, "po_ids": po_ids, "requisition": _public(doc)}


# --- purchase orders (R14..R24) -------------------------------------------------------------------------------------


def _po_lines(vendor_id: str, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not lines:
        raise FVError(NO_LINES, "The purchase order has no lines.", {"vendor_id": vendor_id})
    v = plant.VENDOR_BY_ID[vendor_id]
    out = []
    for k, raw in enumerate(lines):
        mid = str(raw.get("material_id") or "")
        m = _material(mid)
        _supplies(vendor_id, mid)
        qty = check_qty(raw.get("qty"), f"lines[{k}].qty")
        price = vendor_price(v, m)
        out.append(
            {
                "line_id": k + 1,
                "material_id": mid,
                "material_name": m.material_name,
                "unit": m.unit,
                "qty": qty,
                "unit_price_idr": price,
                "price_label": price_label(price),
                "value_idr": line_value(qty, price),
                "received_qty": 0.0,
                "need_by": parse_date(raw.get("need_by"), f"lines[{k}].need_by"),
                "source": str(raw.get("source") or "manual"),
                "note": raw.get("note"),
            }
        )
    return out


def _po_doc(sid: str, po_id: str) -> dict[str, Any] | None:
    return store().get(POS, _key(sid, po_id))


def _session_po(sid: str, po_id: str) -> dict[str, Any]:
    doc = _po_doc(sid, po_id)
    if doc is None:
        if any(r["po_id"] == po_id for r in _seed_rows()):
            raise FVError(SEED_READ_ONLY, f"{po_id} is a seed PO; only receiving is allowed on it.", {"po_id": po_id})
        raise FVError(PO_NOT_FOUND, f"Purchase order {po_id} does not exist.", {"po_id": po_id})
    return doc


def _ledger_incoming(sid: str, po: dict[str, Any], sign: float, ref_note: str) -> None:
    for ln in po["lines"]:
        q = open_qty(ln)
        if q <= EPS:
            continue
        materials.record_transaction(
            sid,
            {
                "date": TODAY.isoformat(),
                "material_id": ln["material_id"],
                "type": "Incoming",
                "qty": round(sign * q, 4),
                "ref": f"{po['po_id']} {ref_note}",
            },
        )


def create_po(
    sid: str,
    *,
    vendor_id: str,
    lines: list[dict[str, Any]],
    promised_date: str | None = None,
    pr_id: str | None = None,
    note: str | None = None,
    user: str = "user",
    issue: bool = False,
) -> dict[str, Any]:
    v = _vendor(vendor_id)
    built = _po_lines(vendor_id, lines)
    promised = parse_date(promised_date, "promised_date") or (TODAY + timedelta(days=int(v["lead_time_days"]))).isoformat()
    po_id = next_number([p["po_id"] for p in _session_pos(sid)], po_prefix(TODAY))
    doc = {
        "po_id": po_id,
        "session_id": sid,
        "origin": "session",
        "vendor_id": vendor_id,
        "vendor_name": v["vendor_name"],
        "status": "Draft",
        "order_date": TODAY.isoformat(),
        "promised_date": promised,
        "pr_id": pr_id,
        "note": note,
        "currency": CURRENCY,
        "created_at": _now(),
        "created_by": user,
        "lines": built,
        "history": [_history(user, "create", None, "Draft", pr_id=pr_id)],
    }
    store().set(POS, _key(sid, po_id), doc)
    if issue:
        return transition_po(sid, po_id, "issue", user=user)
    return get_po(sid, po_id)


def update_po(
    sid: str, po_id: str, *, lines: list[dict[str, Any]] | None = None, promised_date: str | None = None, note: str | None = None, user: str = "user"
) -> dict[str, Any]:
    doc = _session_po(sid, po_id)
    if doc["status"] != "Draft":
        raise FVError(
            BAD_TRANSITION,
            f"{po_id} is {doc['status']}; only a Draft PO can be edited.",
            {"id": po_id, "action": "edit", "status": doc["status"], "allowed_from": ["Draft"]},
        )
    if lines is not None:
        doc["lines"] = _po_lines(doc["vendor_id"], lines)
    if promised_date is not None:
        doc["promised_date"] = parse_date(promised_date, "promised_date") or doc["promised_date"]
    if note is not None:
        doc["note"] = note
    doc["history"].append(_history(user, "edit", "Draft", "Draft"))
    store().set(POS, _key(sid, po_id), doc)
    return get_po(sid, po_id)


def transition_po(sid: str, po_id: str, action: str, *, user: str = "user", reason: str | None = None) -> dict[str, Any]:
    doc = _session_po(sid, po_id)
    to = next_status(PO_FLOW, action, doc["status"], po_id)
    if action == "cancel" and any(float(ln.get("received_qty") or 0) > 0 for ln in doc["lines"]):
        raise FVError(
            BAD_TRANSITION,
            f"{po_id} has goods received; close it instead of cancelling.",
            {"id": po_id, "action": action, "status": doc["status"], "allowed_from": ["Draft", "Issued"]},
        )
    if action == "issue":
        _ledger_incoming(sid, doc, 1.0, "issued")
    elif doc["status"] in RECEIVING:
        _ledger_incoming(sid, doc, -1.0, "cancelled" if action == "cancel" else "closed")
    doc["history"].append(_history(user, action, doc["status"], to, reason))
    doc["status"] = to
    store().set(POS, _key(sid, po_id), doc)
    return get_po(sid, po_id)


def expedite_po(sid: str, po_id: str, new_promised_date: str, reason: str, *, user: str = "user") -> dict[str, Any]:
    doc = _session_po(sid, po_id)
    if doc["status"] not in RECEIVING:
        raise FVError(
            BAD_TRANSITION,
            f"{po_id} is {doc['status']}; expedite needs it to be Issued or Partially Received.",
            {"id": po_id, "action": "expedite", "status": doc["status"], "allowed_from": list(RECEIVING)},
        )
    new = parse_date(new_promised_date, "new_promised_date")
    why = check_reason(reason, "expedite a PO")
    if new is None or new >= doc["promised_date"]:
        raise FVError(
            NOT_EARLIER,
            f"The new promised date must be earlier than {doc['promised_date']}.",
            {"po_id": po_id, "promised_date": doc["promised_date"], "new_promised_date": new},
        )
    old = doc["promised_date"]
    doc["promised_date"] = new
    doc["history"].append(_history(user, "expedite", doc["status"], doc["status"], why, old_promised_date=old, new_promised_date=new))
    store().set(POS, _key(sid, po_id), doc)
    return get_po(sid, po_id)


def _view(doc: dict[str, Any], o: effects.Overrides, delays: dict[str, float], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    delay = materials.vendor_delay(doc["vendor_id"], o, delays)
    expected = materials.expected_after(doc["promised_date"], delay) if doc.get("promised_date") else None
    mine = [_public(g) for g in receipts if g["po_id"] == doc["po_id"]]
    overdue, late = lateness(doc["status"], expected)
    lines = [{**ln, "open_qty": open_qty(ln) if doc["status"] in OPEN_PO else 0.0} for ln in doc["lines"]]
    received_date = doc.get("received_date") or (max(str(g["received_date"]) for g in mine) if mine and doc["status"] in ("Received", "Closed") else None)
    return {
        **{k: v for k, v in doc.items() if k not in ("session_id", "lines", "history")},
        "expected_date": expected,
        "expected_delay_days": round(delay, 1),
        "received_date": received_date,
        "total_value_idr": sum(int(ln["value_idr"]) for ln in lines),
        "received_pct": received_pct(lines),
        "overdue": overdue,
        "days_late": late,
        "lines": lines,
        "receipts": mine,
        "history": list(doc.get("history") or []),
    }


def _seed_doc(r: dict[str, Any], got: dict[tuple[str, str], float]) -> dict[str, Any]:
    mid, vid = str(r["material_id"]), str(r["vendor_id"])
    m = plant.MATERIAL_BY_ID.get(mid)
    v = plant.VENDOR_BY_ID.get(vid, {})
    qty = float(r["order_qty"] or 0)
    session_got = got.get((str(r["po_id"]), mid), 0.0)
    price = int(r["unit_price_idr"] or 0) or (vendor_price(v, m) if v and m else 0)
    line = {
        "line_id": 1,
        "material_id": mid,
        "material_name": m.material_name if m else mid,
        "unit": m.unit if m else None,
        "qty": qty,
        "unit_price_idr": price,
        "price_label": price_label(price),
        "value_idr": line_value(qty, price),
        "received_qty": round(float(r["received_qty"] or 0) + session_got, 4),
        "need_by": None,
        "source": "seed",
        "note": None,
    }
    if r["po_status"] == "Open":
        status = status_after_receipt([line])
        received_date = None
    else:
        status = "Received"
        line["received_qty"] = max(float(line["received_qty"]), qty)
        received_date = to_date(r["received_date"]).isoformat() if r["received_date"] is not None else None
    return {
        "po_id": r["po_id"],
        "origin": "seed",
        "vendor_id": vid,
        "vendor_name": v.get("vendor_name", vid),
        "status": status,
        "order_date": to_date(r["order_date"]).isoformat(),
        "promised_date": to_date(r["promised_date"]).isoformat(),
        "received_date": received_date,
        "pr_id": None,
        "note": None,
        "currency": CURRENCY,
        "lot_id": r.get("lot_id"),
        "read_only": True,
        "lines": [line],
        "history": [],
    }


def _all_pos(sid: str, *, seed_open_only: bool = False) -> list[dict[str, Any]]:
    o = effects.overrides(sid)
    delays = _delays()
    receipts = _receipts(sid)
    got = received_by_po(sid)
    docs = [{**d, "read_only": False} for d in _session_pos(sid)]
    docs += [_seed_doc(r, got) for r in _seed_rows() if not seed_open_only or r["po_status"] == "Open"]
    return [_view(d, o, delays, receipts) for d in docs]


def list_pos(
    sid: str, status: str | None = None, vendor_id: str | None = None, material_id: str | None = None, overdue: bool | None = None
) -> list[dict[str, Any]]:
    _check_status(status, PO_STATUSES)
    if vendor_id:
        _vendor(vendor_id)
    rows = [
        p
        for p in _all_pos(sid, seed_open_only=True)
        if (not status or p["status"] == status)
        and (not vendor_id or p["vendor_id"] == vendor_id)
        and (not material_id or any(ln["material_id"] == material_id for ln in p["lines"]))
        and (overdue is None or p["overdue"] == overdue)
    ]
    return sorted(rows, key=lambda p: (p["order_date"], p["origin"] == "session", p["po_id"]), reverse=True)


def get_po(sid: str, po_id: str) -> dict[str, Any]:
    o = effects.overrides(sid)
    doc = _po_doc(sid, po_id)
    if doc is not None:
        return _view({**doc, "read_only": False}, o, _delays(), _receipts(sid))
    seed = next((r for r in _seed_rows() if r["po_id"] == po_id), None)
    if seed is None:
        raise FVError(PO_NOT_FOUND, f"Purchase order {po_id} does not exist.", {"po_id": po_id})
    return _view(_seed_doc(seed, received_by_po(sid)), o, _delays(), _receipts(sid))


def po_document(sid: str, po_id: str) -> str:
    p = get_po(sid, po_id)
    v = plant.VENDOR_BY_ID.get(p["vendor_id"], {})
    out = [
        f"{BUYER.upper()}  -  Purchasing",
        "PURCHASE ORDER",
        "",
        f"PO number:     {p['po_id']}" + ("  (seed)" if p["origin"] == "seed" else ""),
        f"Status:        {p['status']}",
        f"Vendor:        {p['vendor_name']} ({p['vendor_id']})",
        f"Order date:    {p['order_date']}",
        f"Promised date: {p['promised_date']}",
        f"Quoted lead:   {v.get('lead_time_days', '-')} days",
        f"Requisition:   {p['pr_id'] or '-'}",
        f"Currency:      {p['currency']}",
        "",
        f"{'No':<3} {'Material':<14} {'Description':<34} {'Qty':>12} {'Unit':<5} {'Unit price':>12} {'Value':>16}",
    ]
    for ln in p["lines"]:
        price = f"{ln['unit_price_idr']:,}" if ln["unit_price_idr"] > 0 else "on quotation"
        out.append(
            f"{ln['line_id']:<3} {ln['material_id']:<14} {str(ln['material_name'])[:34]:<34} {ln['qty']:>12,.2f} {str(ln['unit'] or ''):<5} {price:>12} {ln['value_idr']:>16,}"
        )
    out += [
        "",
        f"Total value: {p['currency']} {p['total_value_idr']:,}",
        f"Receiving tolerance: +{TOLERANCE_PCT:g}% of the ordered quantity.",
        "Deliver to the raw-material warehouse; quote the PO number on the delivery note.",
    ]
    if p["note"]:
        out += ["", f"Note: {p['note']}"]
    return "\n".join(out) + "\n"


# --- goods receipt (R25..R31) ---------------------------------------------------------------------------------------


def receive(
    sid: str,
    po_id: str,
    lines: list[dict[str, Any]],
    *,
    received_date: str | None = None,
    lot_id: str | None = None,
    note: str | None = None,
    user: str = "user",
) -> dict[str, Any]:
    doc, view = _po_doc(sid, po_id), get_po(sid, po_id)
    if view["status"] not in RECEIVING:
        raise FVError(
            BAD_TRANSITION,
            f"{po_id} is {view['status']}; goods can be received only on an Issued or Partially Received PO.",
            {"id": po_id, "action": "receive", "status": view["status"], "allowed_from": list(RECEIVING)},
        )
    if not lines:
        raise FVError(NO_LINES, "The goods receipt has no lines.", {"po_id": po_id})
    on_po = {ln["material_id"] for ln in view["lines"]}
    want: dict[str, float] = {}
    for k, raw in enumerate(lines):
        mid = str(raw.get("material_id") or "")
        qty = check_qty(raw.get("qty"), f"lines[{k}].qty")
        if mid not in on_po:
            raise FVError(NOT_ON_PO, f"{mid} is not on {po_id}.", {"po_id": po_id, "material_id": mid, "valid": sorted(on_po)})
        want[mid] = round(want.get(mid, 0.0) + qty, 4)
    for mid, qty in want.items():
        left = sum(open_qty(ln) for ln in view["lines"] if ln["material_id"] == mid)
        limit = receipt_limit(left)
        if qty > limit + EPS:
            raise FVError(
                OVER_RECEIPT,
                f"{qty:g} of {mid} is more than the open {left:g} plus {TOLERANCE_PCT:g}% tolerance.",
                {"po_id": po_id, "material_id": mid, "qty": qty, "open_qty": left, "max_qty": limit, "tolerance_pct": TOLERANCE_PCT},
            )
    rd = parse_date(received_date, "received_date") or TODAY.isoformat()
    receipts = _receipts(sid)
    if not lot_id and RESIN in want:
        lot_id = next_number([str(g.get("lot_id") or "") for g in receipts], LOT_PREFIX, 4)
    gr_id = next_number([g["gr_id"] for g in receipts], gr_prefix(TODAY))
    before = {mid: _on_hand(sid, mid) for mid in want}
    gr_lines = []
    for mid, qty in want.items():
        m = plant.MATERIAL_BY_ID[mid]
        left = sum(open_qty(ln) for ln in view["lines"] if ln["material_id"] == mid)
        gr_lines.append(
            {
                "material_id": mid,
                "material_name": m.material_name,
                "unit": m.unit,
                "qty": qty,
                "warehouse_id": m.default_warehouse_id,
                "incoming_consumed": round(min(qty, left), 4),
            }
        )
    gr = {
        "gr_id": gr_id,
        "session_id": sid,
        "po_id": po_id,
        "origin": view["origin"],
        "vendor_id": view["vendor_id"],
        "vendor_name": view["vendor_name"],
        "received_date": rd,
        "lot_id": lot_id,
        "note": note,
        "by": user,
        "created_at": _now(),
        "lines": gr_lines,
    }
    store().set(GRS, _key(sid, gr_id), gr)
    if doc is not None:
        for mid, qty in want.items():
            allocate(doc["lines"], mid, qty)
        to = status_after_receipt(doc["lines"])
        doc["history"].append(_history(user, "receive", doc["status"], to, gr_id=gr_id))
        doc["status"] = to
        store().set(POS, _key(sid, po_id), doc)
    for ln in gr_lines:
        materials.record_transaction(
            sid,
            {
                "date": rd,
                "material_id": ln["material_id"],
                "warehouse_id": ln["warehouse_id"],
                "type": "Receipt",
                "qty": ln["qty"],
                "balance_after": round(before[ln["material_id"]] + ln["qty"], 4),
                "ref": f"{gr_id} {po_id}",
            },
        )
    stock = [
        {k: r[k] for k in ("material_id", "warehouse_id", "on_hand_qty", "reserved_qty", "incoming_qty", "available_qty", "unit")}
        for mid in want
        for r in materials.stock(sid, material_id=mid, warehouse_id=plant.MATERIAL_BY_ID[mid].default_warehouse_id)
    ]
    after = get_po(sid, po_id)
    return {**_public(gr), "po_status": after["status"], "po": after, "stock": stock}


def _on_hand(sid: str, material_id: str) -> float:
    wh = plant.MATERIAL_BY_ID[material_id].default_warehouse_id
    return sum(float(r["on_hand_qty"]) for r in materials.stock(sid, material_id=material_id, warehouse_id=wh))


def list_receipts(sid: str, po_id: str | None = None) -> list[dict[str, Any]]:
    rows = [_public(g) for g in _receipts(sid) if not po_id or g["po_id"] == po_id]
    return list(reversed(rows))


# --- summary and reset (R22, R32, R34) ------------------------------------------------------------------------------


def summary(sid: str) -> dict[str, Any]:
    pos = list_pos(sid)
    open_pos = [p for p in pos if p["status"] in RECEIVING]
    overdue = [p for p in open_pos if p["overdue"]]
    pending = list_requisitions(sid, "Submitted")
    value = sum(line_value(ln["open_qty"], ln["unit_price_idr"]) for p in open_pos for ln in p["lines"])
    return {
        "kpis": {
            "open_pos": len(open_pos),
            "overdue_pos": len(overdue),
            "open_value_idr": value,
            "pending_requisitions": len(pending),
            "draft_pos": sum(1 for p in pos if p["status"] == "Draft"),
            "approved_requisitions": len(list_requisitions(sid, "Approved")),
        },
        "overdue": overdue,
        "pending_requisitions": pending,
    }


def clear(sid: str) -> None:
    for d in store().where(PRS, "session_id", sid):
        store().delete(PRS, _key(sid, d["pr_id"]))
    for d in store().where(POS, "session_id", sid):
        store().delete(POS, _key(sid, d["po_id"]))
    for d in store().where(GRS, "session_id", sid):
        store().delete(GRS, _key(sid, d["gr_id"]))
