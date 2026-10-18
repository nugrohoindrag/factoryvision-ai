from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app import materials, plant
from app.errors import FVError
from app.store import store
from app.tools import purchasing
from app.tools.common import TODAY, idr, to_date

# shortcut: stand-in for the backend agent's module, replaced at merge; only the contract names and shapes matter here.

Doc = dict[str, Any]
UNKNOWN_ENTITY = "FV-DATA-001"
PRS, POS, GRS = "purchase_requisitions", "purchase_orders", "goods_receipts"
PR_FLOW = {"submit": ("Draft", "Submitted"), "approve": ("Submitted", "Approved"), "reject": ("Submitted", "Rejected"), "cancel": ("Draft", "Cancelled")}
PO_FLOW = {"issue": ("Draft", "Issued"), "close": ("Received", "Closed"), "cancel": ("Issued", "Cancelled")}
OPEN_PO = ("Issued", "Partially Received")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _key(sid: str, doc_id: str) -> str:
    return f"{sid}:{doc_id}"


def _docs(sid: str, coll: str) -> list[Doc]:
    return sorted(store().where(coll, "session_id", sid), key=lambda d: str(d.get("created_at")))


def _next(sid: str, coll: str, prefix: str) -> str:
    return f"{prefix}-{TODAY:%y%m}-{len(_docs(sid, coll)) + 1:03d}"


def _hist(by: str, action: str, frm: str | None, to: str, reason: str | None = None) -> Doc:
    return {"at": _now(), "by": by, "action": action, "from": frm, "to": to, "reason": reason}


def list_vendors() -> Doc:
    return {"vendors": list(plant.VENDORS)}


def get_vendor(vendor_id: str) -> Doc:
    if vendor_id not in plant.VENDOR_BY_ID:
        raise FVError(UNKNOWN_ENTITY, f"unknown vendor {vendor_id}")
    return dict(plant.VENDOR_BY_ID[vendor_id])


def recommend(material_id: str, qty: float, need_by: str | None) -> Doc:
    if not plant.MATERIAL_VENDORS.get(material_id):
        raise FVError(UNKNOWN_ENTITY, f"no vendor supplies {material_id}")
    return purchasing.recommend_po(material_id, need_by, qty)


def _pr_line(n: int, material_id: str, qty: float, need_by: str | None, source: str) -> Doc:
    mat = materials.get_material(material_id)
    vid = next(iter(plant.MATERIAL_VENDORS.get(material_id, [])), None)
    return {
        "line_id": f"L{n}",
        "material_id": material_id,
        "material_name": mat.get("material_name"),
        "unit": mat.get("unit"),
        "qty": float(qty),
        "need_by": need_by,
        "suggested_vendor_id": vid,
        "suggested_vendor_name": plant.VENDOR_BY_ID[vid]["vendor_name"] if vid in plant.VENDOR_BY_ID else None,
        "source": source,
        "duplicate_of": None,
    }


def create_requisition(
    sid: str,
    *,
    lines: list[Doc] | None = None,
    from_mrp_run: str | None = None,
    from_plan: str | None = None,
    material_ids: list[str] | None = None,
    note: str | None = None,
    user: str = "user",
) -> Doc:
    from app import mrp

    del from_plan
    raw = [(ln["material_id"], ln["qty"], ln.get("need_by"), "manual") for ln in lines or []]
    if from_mrp_run:
        rows = [r for r in mrp.get_run(sid, from_mrp_run)["results"] if (r.get("net_qty") or 0) > 0]
        raw += [
            (r["material_id"], r["net_qty"], r["required_date"], f"mrp:{from_mrp_run}") for r in rows if not material_ids or r["material_id"] in material_ids
        ]
    if not raw:
        raise FVError(UNKNOWN_ENTITY, "a requisition needs at least one line")
    pr_id = _next(sid, PRS, "PR")
    doc = {
        "pr_id": pr_id,
        "session_id": sid,
        "status": "Draft",
        "created_at": _now(),
        "created_by": user,
        "source": raw[0][3],
        "note": note,
        "lines": [_pr_line(n, *r) for n, r in enumerate(raw, 1)],
        "po_ids": [],
        "history": [_hist(user, "create", None, "Draft")],
    }
    store().set(PRS, _key(sid, pr_id), doc)
    return get_requisition(sid, pr_id)


def get_requisition(sid: str, pr_id: str) -> Doc:
    doc = store().get(PRS, _key(sid, pr_id))
    if doc is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown requisition {pr_id}")
    return {k: v for k, v in doc.items() if k != "session_id"}


def list_requisitions(sid: str, status: str | None = None) -> Doc:
    return {"requisitions": [get_requisition(sid, d["pr_id"]) for d in _docs(sid, PRS) if not status or d["status"] == status]}


def update_requisition(sid: str, pr_id: str, lines: list[Doc], user: str) -> Doc:
    pr = get_requisition(sid, pr_id)
    pr["lines"] = [_pr_line(n, ln["material_id"], ln["qty"], ln.get("need_by"), "manual") for n, ln in enumerate(lines, 1)]
    pr["history"].append(_hist(user, "edit", pr["status"], pr["status"]))
    store().set(PRS, _key(sid, pr_id), {**pr, "session_id": sid})
    return pr


def transition_requisition(sid: str, pr_id: str, action: str, *, user: str, reason: str | None = None) -> Doc:
    pr = get_requisition(sid, pr_id)
    frm, to = PR_FLOW[action]
    if pr["status"] != frm and not (action == "cancel" and pr["status"] == "Submitted"):
        raise FVError(UNKNOWN_ENTITY, f"requisition {pr_id} is {pr['status']}")
    pr["history"].append(_hist(user, action, pr["status"], to, reason))
    pr["status"] = to
    store().set(PRS, _key(sid, pr_id), {**pr, "session_id": sid})
    return pr


def convert_requisition(sid: str, pr_id: str, *, vendor_overrides: dict[str, str] | None = None, user: str) -> Doc:
    pr = get_requisition(sid, pr_id)
    groups: dict[str, list[Doc]] = {}
    for ln in pr["lines"]:
        groups.setdefault((vendor_overrides or {}).get(ln["material_id"]) or ln["suggested_vendor_id"], []).append(ln)
    ids = [create_po(sid, vendor_id=v, lines=ls, pr_id=pr_id, user=user)["po_id"] for v, ls in groups.items()]
    pr["po_ids"], pr["status"] = ids, "Converted"
    store().set(PRS, _key(sid, pr_id), {**pr, "session_id": sid})
    return {"po_ids": ids}


def _po_view(doc: Doc) -> Doc:
    exp = to_date(doc["expected_date"])
    open_ = doc["status"] in OPEN_PO
    return {k: v for k, v in doc.items() if k != "session_id"} | {
        "overdue": open_ and exp < TODAY,
        "days_late": max(0, (TODAY - exp).days) if open_ else 0,
    }


def create_po(
    sid: str,
    *,
    vendor_id: str,
    lines: list[Doc],
    promised_date: str | None = None,
    pr_id: str | None = None,
    note: str | None = None,
    user: str = "user",
    issue: bool = False,
) -> Doc:
    vendor = get_vendor(vendor_id)
    po_id = f"PO-{TODAY:%y%m}-S-{len(_docs(sid, POS)) + 1:03d}"
    promised = promised_date or (TODAY + timedelta(days=int(vendor["lead_time_days"]))).isoformat()
    out = []
    for n, ln in enumerate(lines, 1):
        mat = materials.get_material(ln["material_id"])
        price = float(mat.get("unit_cost_idr") or vendor.get("unit_price_idr") or 0)
        qty = float(ln["qty"])
        out.append(
            {
                "line_id": f"L{n}",
                "material_id": ln["material_id"],
                "material_name": mat.get("material_name"),
                "unit": mat.get("unit"),
                "qty": qty,
                "unit_price_idr": price,
                "value_idr": idr(qty * price),
                "received_qty": 0.0,
                "open_qty": qty,
            }
        )
    doc = {
        "po_id": po_id,
        "session_id": sid,
        "origin": "session",
        "vendor_id": vendor_id,
        "vendor_name": vendor["vendor_name"],
        "status": "Draft",
        "created_at": _now(),
        "order_date": TODAY.isoformat(),
        "promised_date": promised,
        "expected_date": promised,
        "pr_id": pr_id,
        "note": note,
        "currency": "IDR",
        "total_value_idr": sum(ln["value_idr"] for ln in out),
        "received_pct": 0.0,
        "lines": out,
        "receipts": [],
        "history": [_hist(user, "create", None, "Draft")],
    }
    store().set(POS, _key(sid, po_id), doc)
    return transition_po(sid, po_id, "issue", user=user) if issue else get_po(sid, po_id)


def get_po(sid: str, po_id: str) -> Doc:
    doc = store().get(POS, _key(sid, po_id))
    if doc is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown purchase order {po_id}")
    return _po_view(doc)


def _save_po(sid: str, po: Doc) -> Doc:
    store().set(POS, _key(sid, po["po_id"]), {k: v for k, v in po.items() if k not in ("overdue", "days_late")} | {"session_id": sid})
    return get_po(sid, po["po_id"])


def transition_po(sid: str, po_id: str, action: str, *, user: str) -> Doc:
    po = get_po(sid, po_id)
    frm, to = PO_FLOW[action]
    if po["status"] != frm:
        raise FVError(UNKNOWN_ENTITY, f"purchase order {po_id} is {po['status']}")
    po["history"].append(_hist(user, action, po["status"], to))
    po["status"] = to
    return _save_po(sid, po)


def expedite_po(sid: str, po_id: str, new_promised_date: str, reason: str, *, user: str) -> Doc:
    po = get_po(sid, po_id)
    if po["status"] not in OPEN_PO or new_promised_date >= po["promised_date"]:
        raise FVError(UNKNOWN_ENTITY, f"purchase order {po_id} cannot be expedited to {new_promised_date}")
    po["history"].append(_hist(user, "expedite", po["status"], po["status"], reason))
    po["promised_date"] = po["expected_date"] = new_promised_date
    return _save_po(sid, po)


def list_pos(sid: str, status: str | None = None, vendor_id: str | None = None, material_id: str | None = None, overdue: bool | None = None) -> Doc:  # noqa: FBT001
    rows = [get_po(sid, d["po_id"]) for d in _docs(sid, POS)]
    rows = [
        r
        for r in rows
        if (not status or r["status"] == status)
        and (not vendor_id or r["vendor_id"] == vendor_id)
        and (not material_id or any(ln["material_id"] == material_id for ln in r["lines"]))
        and (overdue is None or r["overdue"] == overdue)
    ]
    return {"orders": rows}


def receive(
    sid: str, po_id: str, lines: list[Doc], *, received_date: str | None = None, lot_id: str | None = None, note: str | None = None, user: str = "user"
) -> Doc:
    po = get_po(sid, po_id)
    gr_id = f"GR-{TODAY:%y%m}-S-{len(_docs(sid, GRS)) + 1:03d}"
    gr = {
        "gr_id": gr_id,
        "session_id": sid,
        "created_at": _now(),
        "po_id": po_id,
        "received_date": received_date or TODAY.isoformat(),
        "lot_id": lot_id,
        "note": note,
        "by": user,
        "lines": lines,
    }
    store().set(GRS, _key(sid, gr_id), gr)
    po["status"] = "Partially Received"
    _save_po(sid, po)
    return {k: v for k, v in gr.items() if k != "session_id"}


def list_receipts(sid: str, po_id: str | None = None) -> Doc:
    return {"receipts": [d for d in _docs(sid, GRS) if not po_id or d["po_id"] == po_id]}


def po_document(sid: str, po_id: str) -> str:
    po = get_po(sid, po_id)
    return f"PURCHASE ORDER {po['po_id']}\nVendor: {po['vendor_name']}\nTotal: Rp{po['total_value_idr']:,}\n"


def summary(sid: str) -> Doc:
    orders = list_pos(sid)["orders"]
    pending = list_requisitions(sid, "Submitted")["requisitions"]
    open_ = [o for o in orders if o["status"] in OPEN_PO]
    return {
        "kpis": {
            "open_pos": len(open_),
            "overdue_pos": sum(1 for o in open_ if o["overdue"]),
            "value_open_idr": sum(o["total_value_idr"] for o in open_),
            "prs_waiting": len(pending),
        },
        "overdue": [o for o in open_ if o["overdue"]],
        "pending_requisitions": pending,
    }


def clear(sid: str) -> None:
    for coll, field in ((PRS, "pr_id"), (POS, "po_id"), (GRS, "gr_id")):
        for d in store().where(coll, "session_id", sid):
            store().delete(coll, _key(sid, d[field]))
