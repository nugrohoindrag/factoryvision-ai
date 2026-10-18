from __future__ import annotations

from typing import Any

from app import actions, materials, mrp, purchasing_docs
from app.agents import material as material_facts
from app.errors import FVError
from app.tools.common import idr, to_date

Doc = dict[str, Any]

NOTHING_TO_REQUISITION = "FV-AGT-020"
VENDOR_NOT_FOR_MATERIAL = "FV-AGT-021"
QTY_NOT_POSITIVE = "FV-AGT-022"
PO_NOT_EXPEDITABLE = "FV-AGT-023"
EXPEDITE_NOT_EARLIER = "FV-AGT-024"

OPEN_PO = ("Issued", "Partially Received")
PO_KEYS = (
    "po_id",
    "origin",
    "vendor_id",
    "vendor_name",
    "status",
    "order_date",
    "promised_date",
    "expected_date",
    "pr_id",
    "total_value_idr",
    "received_pct",
    "overdue",
    "days_late",
)
PO_LINE_KEYS = ("material_id", "material_name", "unit", "qty", "unit_price_idr", "value_idr", "received_qty", "open_qty")
PR_KEYS = ("pr_id", "status", "created_at", "created_by", "source", "note", "po_ids")
PR_LINE_KEYS = ("material_id", "material_name", "unit", "qty", "need_by", "suggested_vendor_id", "suggested_vendor_name", "duplicate_of")
OPTION_KEYS = ("vendor_id", "vendor_name", "expected_arrival", "arrives_before_need", "unit_price_idr", "po_value_idr", "avg_delay_days")
MAX_ROWS = 25
SOURCE = "purchasing_docs (session POs and seed open POs from fv.fact_purchase_order)"


def _n(v: object) -> str:
    return f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)


def po_view(po: Doc) -> Doc:
    return {**{k: po.get(k) for k in PO_KEYS}, "lines": [{k: ln.get(k) for k in PO_LINE_KEYS} for ln in po.get("lines", [])]}


def pr_view(pr: Doc) -> Doc:
    return {**{k: pr.get(k) for k in PR_KEYS}, "lines": [{k: ln.get(k) for k in PR_LINE_KEYS} for ln in pr.get("lines", [])]}


def list_pos(sid: str, status: str | None = None, *, overdue: bool | None = None) -> Doc:
    rows = purchasing_docs.list_pos(sid, status=status, overdue=overdue)["orders"]
    return {"count": len(rows), "orders": [po_view(r) for r in rows[:MAX_ROWS]], "truncated": len(rows) > MAX_ROWS, "source": SOURCE}


def list_prs(sid: str, status: str | None = None) -> Doc:
    rows = purchasing_docs.list_requisitions(sid, status)["requisitions"]
    return {"count": len(rows), "requisitions": [pr_view(r) for r in rows[:MAX_ROWS]], "source": "purchasing_docs requisitions of this session"}


def vendor_options(material_id: str, qty: float, need_by: str | None) -> Doc:
    rec = purchasing_docs.recommend(material_id, qty, need_by)
    opts = [{k: o.get(k) for k in OPTION_KEYS} for o in rec.get("options", [])]
    priced = [o for o in opts if (o.get("unit_price_idr") or 0) > 0]
    cheapest = min(priced, key=lambda o: o["po_value_idr"]) if priced else None
    return {
        "material_id": material_id,
        "qty": qty,
        "need_by": need_by,
        "options": opts,
        "recommended_vendor_id": rec.get("recommended_vendor_id"),
        "cheapest_vendor_id": cheapest["vendor_id"] if cheapest else None,
        "cheapest_po_value_idr": cheapest["po_value_idr"] if cheapest else None,
        "source": "purchasing_docs.recommend (today + quoted lead time + average delay + announced delay)",
    }


def _premium(option: Doc, vo: Doc) -> int | None:
    if not (option.get("unit_price_idr") or 0) or vo["cheapest_po_value_idr"] is None:
        return None
    return idr(float(option["po_value_idr"]) - float(vo["cheapest_po_value_idr"]))


def _pending(sid: str, kind: str, key: str, value: object) -> Doc | None:
    return next((a for a in actions.list_actions(sid, "pending") if a["kind"] == kind and a["command"]["args"].get(key) == value), None)


def _unit(material_id: str) -> str | None:
    try:
        return materials.get_material(material_id).get("unit")
    except FVError:
        return None


def _timing(option: Doc, need_by: str | None) -> str:
    if not need_by:
        return f"expected {option['expected_arrival']}"
    late = (to_date(option["expected_arrival"]) - to_date(need_by)).days
    return f"expected {option['expected_arrival']} vs need {need_by} ({f'{late} days late' if late > 0 else 'in time'})"


def propose_requisition(sid: str, run_id: str | None, *, user: str, plan_id: str | None = None) -> tuple[Doc, bool]:
    run = mrp.get_run(sid, run_id) if run_id else material_facts.latest_or_new_run(sid, user)[0]
    rows = [r for r in run.get("results", []) if (r.get("net_qty") or 0) > 0]
    if not rows:
        raise FVError(NOTHING_TO_REQUISITION, detail={"run_id": run.get("run_id")})
    pending = _pending(sid, "purchase_requisition", "from_mrp_run", run["run_id"])
    if pending is not None:
        return pending, False
    options: dict[str, Doc] = {}
    parts = []
    for r in rows:
        mid, qty, need = r["material_id"], float(r["net_qty"]), str(r["required_date"])
        try:
            vo = vendor_options(mid, qty, need)
        except FVError as e:
            options[mid] = e.tool_result()
            parts.append(f"{mid} {_n(qty)} {r.get('unit')} by {need} (no vendor option)")
            continue
        options[mid] = vo
        best = next((o for o in vo["options"] if o["vendor_id"] == vo["recommended_vendor_id"]), None)
        tail = f", {best['vendor_name']} {_timing(best, need)}, Rp{_n(best['po_value_idr'])}" if best else ""
        parts.append(f"{mid}{' ' + r['color'] if r.get('color') else ''} {_n(qty)} {r.get('unit')} by {need}{tail}")
    args = {"from_mrp_run": run["run_id"], "material_ids": [r["material_id"] for r in rows]}
    rationale = f"MRP {run['run_id']} has {len(rows)} material(s) with a net requirement: " + "; ".join(parts) + "."
    evidence: list[Doc] = [
        {"source": f"MRP run {run['run_id']}", "mrp_results": rows},
        {"source": "vendor options per material (purchasing_docs.recommend)", "vendor_options": options},
    ]
    title = f"Purchase requisition from {run['run_id']}: {len(rows)} material(s)"
    return actions.propose(sid, "purchase_requisition", title, "purchasing", rationale, args, evidence, material_facts.ZERO_IMPACT, plan_id), True


def propose_po(sid: str, material_id: str, qty: float, need_by: str | None, vendor_id: str | None, *, plan_id: str | None = None) -> tuple[Doc, bool]:
    if qty <= 0:
        raise FVError(QTY_NOT_POSITIVE, detail={"material_id": material_id, "qty": qty})
    vo = vendor_options(material_id, qty, need_by)
    chosen_id = vendor_id or vo["recommended_vendor_id"]
    option = next((o for o in vo["options"] if o["vendor_id"] == chosen_id), None)
    if option is None:
        raise FVError(VENDOR_NOT_FOR_MATERIAL, detail={"material_id": material_id, "vendor_id": chosen_id, "valid": [o["vendor_id"] for o in vo["options"]]})
    pending = material_facts.pending_po(sid, material_id)
    if pending is not None:
        return pending, False
    unit = _unit(material_id)
    args = {
        "vendor_id": chosen_id,
        "material_id": material_id,
        "qty": float(qty),
        "expected_date": option["expected_arrival"],
        "unit": unit,
        "need_by": need_by,
    }
    premium = _premium(option, vo)
    cheapest = next((o for o in vo["options"] if o["vendor_id"] == vo["cheapest_vendor_id"]), None)
    price = f"PO value Rp{_n(option['po_value_idr'])}" if option.get("unit_price_idr") else "price on quotation"
    vs = f", premium Rp{_n(premium)} vs the cheapest ({cheapest['vendor_name']})" if premium is not None and cheapest else ""
    rationale = f"{option['vendor_name']} for {_n(qty)} {unit} {material_id}: {_timing(option, need_by)}; {price}{vs}."
    evidence = [{**vo, "chosen_vendor_id": chosen_id, "premium_vs_cheapest_idr": premium}]
    title = f"PO {material_id} {_n(qty)} {unit} from {option['vendor_name']}"
    impact = material_facts.po_impact(args, sid)
    return actions.propose(sid, "purchase_order", title, "purchasing", rationale, args, evidence, impact, plan_id), True


def propose_expedite(sid: str, po_id: str, new_promised_date: str, reason: str, *, plan_id: str | None = None) -> tuple[Doc, bool]:
    po = purchasing_docs.get_po(sid, po_id)
    if po.get("origin") == "seed" or po.get("status") not in OPEN_PO:
        raise FVError(PO_NOT_EXPEDITABLE, detail={"po_id": po_id, "status": po.get("status"), "origin": po.get("origin")})
    if to_date(new_promised_date) >= to_date(po["promised_date"]):
        raise FVError(EXPEDITE_NOT_EARLIER, detail={"po_id": po_id, "promised_date": po["promised_date"], "new_promised_date": new_promised_date})
    pending = _pending(sid, "expedite_po", "po_id", po_id)
    if pending is not None:
        return pending, False
    view = po_view(po)
    days = (to_date(po["promised_date"]) - to_date(new_promised_date)).days
    args = {"po_id": po_id, "new_promised_date": new_promised_date, "reason": reason, "vendor_id": po.get("vendor_id")}
    rationale = (
        f"{po_id} from {po.get('vendor_name')} ({po.get('status')}, Rp{_n(po.get('total_value_idr'))}) is promised {po['promised_date']}, "
        f"expected {po.get('expected_date')}; pull in by {days} days to {new_promised_date}. {reason}"
    ).strip()
    evidence = [{"source": f"purchase order {po_id} ({SOURCE})", "purchase_order": view}]
    title = f"Expedite {po_id} to {new_promised_date}"
    return actions.propose(sid, "expedite_po", title, "purchasing", rationale, args, evidence, material_facts.ZERO_IMPACT, plan_id), True
