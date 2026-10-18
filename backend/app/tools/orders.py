from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from app import plant, warehouse
from app.errors import FVError
from app.tools import delivery, planning
from app.tools.common import TODAY, pct, to_date, working_days_in_month

UNKNOWN_ENTITY = "FV-DATA-001"
INVALID_REQUEST = "FV-REQ-001"

STATUSES = ("Received", "Planned", "In Production", "Produced", "Ready to Ship", "Shipped", "Completed", "Cancelled")
OPEN_STATUSES = STATUSES[:5]
DONE_STATUSES = ("Ready to Ship", "Shipped", "Completed")
ON_FILE_TOLERANCE = 0.02
RELEASED_WORKDAYS = 2  # work orders this many working days ahead (today included) are released to the floor
WO_HORIZON_DAYS = 60

CHANNEL_LABEL = {"PO_DOCUMENT": "Customer PO", "KANBAN_CARD": "Kanban", "EMAIL": "Email / Invoice", "MANUAL": "Manual"}
REFERENCE_LABEL = {
    "PO_DOCUMENT": "Customer PO number",
    "KANBAN_CARD": "Kanban reference",
    "EMAIL": "Email / invoice reference",
    "MANUAL": "Reference number",
}
REFERENCE_REQUIRED = {"PO_DOCUMENT", "KANBAN_CARD", "EMAIL"}

CUSTOMER_BY_ID = {c["customer_id"]: c for c in plant.CUSTOMERS}

LINE_SQL = """
SELECT order_id, order_number, line_no, customer_id, sku, order_qty, order_date, requested_delivery_date, order_status, shipped_qty,
  po_number, order_channel, customer_pic, delivery_address, dock_number
FROM fv.fact_customer_order ORDER BY requested_delivery_date, order_number, line_no
"""

HEADER_FIELDS = ("order_number", "customer_id", "order_date", "po_number", "order_channel", "customer_pic", "delivery_address", "dock_number")


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def product_name(sku: str) -> str:
    p = plant.PRODUCT_BY_SKU[sku]
    return f"{p.position} bumper {p.model} ({p.segment})"


def customers() -> list[dict[str, Any]]:
    return [{**c, "default_channel": plant.CUSTOMER_ORDER_SOURCE[str(c["customer_id"])][0]} for c in plant.CUSTOMERS]


def products() -> list[dict[str, Any]]:
    return [{"sku": p.sku, "name": product_name(p.sku), "model": p.model, "unit": "pcs"} for p in plant.PRODUCTS]


def channels() -> list[dict[str, Any]]:
    return [{"value": k, "label": v, "reference_label": REFERENCE_LABEL[k], "reference_required": k in REFERENCE_REQUIRED} for k, v in CHANNEL_LABEL.items()]


def session_lines(new_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order lines of the orders entered in this session, in the warehouse row shape."""
    return [
        {
            **{k: o.get(k) for k in HEADER_FIELDS},
            **ln,
            "requested_delivery_date": ln.get("requested_delivery_date") or o["requested_delivery_date"],
            "order_status": "Received",
            "shipped_qty": 0,
            "created_at": o.get("created_at"),
            "created_by": o.get("created_by"),
        }
        for o in new_orders
        for ln in o["lines"]
    ]


def _working_days_from_today(n: int) -> list[date]:
    days: list[date] = []
    d = TODAY
    while len(days) < n:
        if plant.is_working_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def _wo_status(k: int) -> str:
    return "In Progress" if k == 0 else ("Released" if k < RELEASED_WORKDAYS else "Scheduled")


def _peg(atp: dict[str, Any], mold_moves: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Peg each open order line to finished-goods stock first, then to the daily work orders on its mold's machine.

    Uses the Delivery ATP supply (stock + output rate x working days, in the same order), so the last work order
    of a line falls on the day the ATP check says the line completes.
    """
    days = _working_days_from_today(WO_HORIZON_DAYS)
    out: dict[str, dict[str, Any]] = {}
    for s in atp["skus"]:
        sku, rate, stock = s["sku"], float(s["output_rate_per_day"]), float(s["stock_on_hand"])
        machine = mold_moves.get(sku, s["machine_id"])
        cum = 0.0
        for o in s["orders"]:
            start, cum = cum, cum + o["order_qty"]
            wos = []
            lo = max(start, stock)
            for k, d in enumerate(days):
                if lo >= cum or rate <= 0:
                    break
                take = min(cum, stock + rate * (k + 1)) - max(lo, stock + rate * k)
                if round(take) > 0:
                    wos.append(
                        {
                            "wo_id": f"WO-{d:%y%m%d}-{machine}-{sku}",
                            "date": d.isoformat(),
                            "machine_id": machine,
                            "sku": sku,
                            "qty": round(take),
                            "status": _wo_status(k),
                        }
                    )
            out[o["order_id"]] = {**o, "from_stock_qty": round(max(0.0, min(stock, cum) - start)), "work_orders": wos}
    return out


def _line_status(line: dict[str, Any], *, cancelled: bool) -> str:
    if cancelled:
        return "Cancelled"
    if line["stored_status"] in DONE_STATUSES:
        return str(line["stored_status"])
    if line["produced_qty"] >= line["ordered_qty"]:
        return "Produced"
    if line["produced_qty"] > 0 or any(w["status"] == "In Progress" for w in line["work_orders"]):
        return "In Production"
    return "Planned" if line["planned_qty"] > 0 else "Received"


def _header(r: dict[str, Any], created: set[str], cancelled: dict[str, str]) -> dict[str, Any]:
    num = r["order_number"]
    return {
        "order_number": num,
        "customer_id": r["customer_id"],
        "customer_name": CUSTOMER_BY_ID[r["customer_id"]]["customer_name"],
        "po_number": r["po_number"],
        "order_channel": r["order_channel"],
        "channel_label": CHANNEL_LABEL.get(str(r["order_channel"]), r["order_channel"]),
        "order_date": to_date(r["order_date"]).isoformat(),
        "requested_delivery_date": to_date(r["requested_delivery_date"]).isoformat(),
        "customer_pic": r["customer_pic"],
        "delivery_address": r["delivery_address"],
        "dock_number": r["dock_number"],
        "created_in_session": num in created,
        "created_at": r["created_at"],
        "created_by": r["created_by"],
        "status_reason": cancelled.get(num),
        "lines": [],
    }


def _line(r: dict[str, Any], p: dict[str, Any], *, cancelled: bool, in_plans: int = 0) -> dict[str, Any]:
    stored, qty = r["order_status"], int(r["order_qty"])
    wos = p.get("work_orders", [])
    done = stored in DONE_STATUSES
    produced = qty if done else int(p.get("from_stock_qty", 0))
    released = any(w["status"] != "Scheduled" for w in wos)
    line = {
        "order_id": r["order_id"],
        "line_no": int(r["line_no"]),
        "sku": r["sku"],
        "product_name": product_name(r["sku"]),
        "ordered_qty": qty,
        # the monthly OEM plan covers a line once it is released or started; the session's production plans add theirs
        "planned_qty": qty if done or stored == "Planned" or produced > 0 or released else min(qty, in_plans),
        "produced_qty": produced,
        "from_stock_qty": int(p.get("from_stock_qty", 0)),
        "shipped_qty": int(r["shipped_qty"] or 0),
        "requested_delivery_date": to_date(r["requested_delivery_date"]).isoformat(),
        "stored_status": stored,
        "work_orders": wos,
        "at_risk": bool(p.get("at_risk")) and not cancelled,
        "estimated_completion": p.get("estimated_completion"),
        "days_late": int(p.get("days_late", 0)) if p.get("at_risk") and not cancelled else 0,
    }
    line["status"] = _line_status(line, cancelled=cancelled)
    return line


def _roll_up(h: dict[str, Any]) -> None:
    flow = {s: i for i, s in enumerate(STATUSES)}
    live = [ln for ln in h["lines"] if ln["status"] != "Cancelled"]
    h["status"] = min((ln["status"] for ln in live), key=flow.__getitem__) if live else "Cancelled"
    h["ordered_qty"] = sum(ln["ordered_qty"] for ln in live)
    h["planned_qty"] = sum(ln["planned_qty"] for ln in live)
    h["produced_qty"] = sum(ln["produced_qty"] for ln in live)
    h["progress_pct"] = pct(h["produced_qty"] / h["ordered_qty"], 0) if h["ordered_qty"] else 0.0
    risky = [ln for ln in live if ln["at_risk"]]
    h["at_risk"] = bool(risky)
    h["days_late"] = max((ln["days_late"] for ln in risky), default=0)
    h["estimated_completion"] = max((ln["estimated_completion"] for ln in risky), default=None)
    h["penalty_idr"] = int(CUSTOMER_BY_ID[h["customer_id"]]["penalty_idr_per_day"]) * h["days_late"]
    h["cancel_block_reason"] = cancel_block_reason(h)
    h["cancellable"] = h["cancel_block_reason"] is None


def order_view(
    rates: dict[str, float] | None = None,
    new_orders: list[dict[str, Any]] | None = None,
    cancelled: dict[str, str] | None = None,
    mold_moves: dict[str, str] | None = None,
    plan_allocations: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Every customer order as a header with its lines: what was ordered, planned and produced, and when it ships.

    Status up to Produced is derived, not typed: a line is Produced once finished-goods stock covers it, In
    Production while today's work order makes it, Planned once a released work order or the plan carries it.
    At risk is the Delivery ATP rule, so the two pages agree.
    """
    new_orders, cancelled, mold_moves, alloc = new_orders or [], cancelled or {}, mold_moves or {}, plan_allocations or {}
    extra = session_lines(new_orders)
    rows = [{**r, "created_at": None, "created_by": None} for r in warehouse.query(LINE_SQL)] + extra
    created = {o["order_number"] for o in new_orders}
    gone = [str(r["order_id"]) for r in rows if r["order_number"] in cancelled]
    pegged = _peg(delivery.atp_check(rate_override=rates or None, extra_orders=extra, cancelled=gone), mold_moves)
    heads: dict[str, dict[str, Any]] = {}
    for r in rows:
        num = str(r["order_number"])
        h = heads.get(num) or heads.setdefault(num, _header(r, created, cancelled))
        h["lines"].append(_line(r, pegged.get(str(r["order_id"]), {}), cancelled=num in cancelled, in_plans=alloc.get(str(r["order_id"]), 0)))
    for h in heads.values():
        _roll_up(h)
    return {
        "as_of": TODAY.isoformat(),
        "orders": sorted(heads.values(), key=lambda h: (not h["created_in_session"], h["requested_delivery_date"], h["order_number"])),
        "statuses": list(STATUSES),
        "at_risk_rule": delivery.ATP_RULE_FORMULA,
        "source": "fv.fact_customer_order, fv.dim_customer; work orders pegged on the Delivery ATP supply (fv.fact_stock_snapshot, fv.fact_production)",
    }


def cancel_block_reason(h: dict[str, Any]) -> str | None:
    if h["status"] == "Cancelled":
        return "The order is already cancelled."
    if h["status"] in DONE_STATUSES:
        return f"The order is already {h['status'].lower()}."
    running = sorted({w["wo_id"] for ln in h["lines"] for w in ln["work_orders"] if w["status"] == "In Progress"})
    if running:
        return f"Work order {', '.join(running)} is already in production for this order."
    return None


def filter_orders(
    view: dict[str, Any],
    status: str | None = None,
    customer_id: str | None = None,
    sku: str | None = None,
    delivery_from: str | None = None,
    delivery_to: str | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = view["orders"]
    if status:
        out = [h for h in out if h["status"] == status]
    if customer_id:
        out = [h for h in out if h["customer_id"] == customer_id]
    if sku:
        out = [h for h in out if any(ln["sku"] == sku for ln in h["lines"])]
    if delivery_from:
        out = [h for h in out if h["requested_delivery_date"] >= delivery_from]
    if delivery_to:
        out = [h for h in out if h["requested_delivery_date"] <= delivery_to]
    return out


def find_order(view: dict[str, Any], order_number: str) -> dict[str, Any]:
    h = next((x for x in view["orders"] if x["order_number"] == order_number), None)
    if h is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown order {order_number}")
    return dict(h)


def plan_status(month: date) -> str:
    cur = TODAY.replace(day=1)
    return "Completed" if month < cur else ("In Execution" if month == cur else "Confirmed")


def traceability(h: dict[str, Any], demand_change_pct: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """The production plans and work orders serving an order: order line -> monthly plan line -> work orders."""
    molds = planning.current_molds()
    plans: dict[str, dict[str, Any]] = {}
    qty_by_month: dict[str, dict[str, int]] = {}
    for ln in h["lines"]:
        if ln["status"] == "Cancelled":
            continue
        month = to_date(ln["requested_delivery_date"]).replace(day=1)
        key = f"PP-{month:%Y-%m}"
        if key not in plans:
            plans[key] = {
                "plan_number": key,
                "month": month.strftime("%Y-%m"),
                "status": plan_status(month),
                "lines": [],
                "work_orders": [],
                "from_stock_qty": 0,
            }
            qty_by_month[key] = planning.monthly_plan(month, demand_change_pct)
        p = plans[key]
        wos = ln["work_orders"] or _history_work_orders(ln)
        machine = wos[0]["machine_id"] if wos else molds.get(ln["sku"])
        p["lines"].append({"sku": ln["sku"], "machine_id": machine, "plan_qty": qty_by_month[key].get(ln["sku"]), "order_qty": ln["ordered_qty"]})
        p["work_orders"].extend({**w, "order_id": ln["order_id"]} for w in wos)
        p["from_stock_qty"] += ln["from_stock_qty"] if ln["stored_status"] not in DONE_STATUSES else 0
    return list(plans.values())


def _history_work_orders(ln: dict[str, Any]) -> list[dict[str, Any]]:
    """A delivered line: the last production run of its SKU in the week up to the delivery date."""
    if ln["stored_status"] not in DONE_STATUSES:
        return []
    d = to_date(ln["requested_delivery_date"])
    rows = warehouse.query(
        "SELECT production_date, machine_id FROM fv.fact_production WHERE sku = :sku AND production_date <= :d AND production_date >= :d0 "
        "GROUP BY production_date, machine_id ORDER BY production_date DESC LIMIT 1",
        {"sku": ln["sku"], "d": d, "d0": d - timedelta(days=7)},
    )
    return [
        {
            "wo_id": f"WO-{to_date(r['production_date']):%y%m%d}-{r['machine_id']}-{ln['sku']}",
            "date": to_date(r["production_date"]).isoformat(),
            "machine_id": r["machine_id"],
            "sku": ln["sku"],
            "qty": ln["ordered_qty"],
            "status": "Completed",
        }
        for r in rows
    ]


def order_book(view: dict[str, Any]) -> dict[str, Any]:
    """Order book KPIs over orders due in the last 30 days and every open order, counted per order (header)."""
    since = (TODAY - timedelta(days=30)).isoformat()
    heads = [h for h in view["orders"] if h["requested_delivery_date"] >= since or h["status"] in OPEN_STATUSES]
    counts = dict.fromkeys(STATUSES, 0)
    for h in heads:
        counts[h["status"]] += 1
    open_heads = [h for h in heads if h["status"] in OPEN_STATUSES]
    by_customer: dict[str, int] = {}
    for h in open_heads:
        by_customer[h["customer_id"]] = by_customer.get(h["customer_id"], 0) + h["ordered_qty"]
    ships = warehouse.query("SELECT sku, on_time FROM fv.fact_shipment WHERE ship_date >= :since", {"since": TODAY - timedelta(days=30)})
    on_time = sum(1 for s in ships if s["on_time"])
    return {
        "as_of": TODAY.isoformat(),
        "statuses": list(STATUSES),
        "status_counts": counts,
        "open_count": len(open_heads),
        "open_qty": sum(h["ordered_qty"] for h in open_heads),
        "at_risk_count": sum(1 for h in open_heads if h["at_risk"]),
        "due_this_week": sum(1 for h in open_heads if _week_start(to_date(h["requested_delivery_date"])) == _week_start(TODAY)),
        "on_time_delivery_pct": pct(on_time / len(ships)) if ships else None,
        "shipments_30d": len(ships),
        "open_by_customer": [
            {"customer_id": c, "customer_name": CUSTOMER_BY_ID[c]["customer_name"], "open_qty": q} for c, q in sorted(by_customer.items(), key=lambda x: -x[1])
        ],
        "source": "fv.fact_customer_order (requested delivery in the last 30 days, and every open order), fv.fact_shipment",
    }


def _date(v: object) -> date | None:
    try:
        return to_date(v) if v else None
    except ValueError:
        return None


def validate_order(body: dict[str, Any], view: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    """Field errors block the order; warnings (a reference already on another order, delivery within a day) do not."""
    errors: list[dict[str, str]] = []

    def err(field: str, message: str) -> None:
        errors.append({"field": field, "message": message})

    channel = str(body.get("order_channel") or "")
    if body.get("customer_id") not in CUSTOMER_BY_ID:
        err("customer_id", "Choose a customer.")
    if channel not in CHANNEL_LABEL:
        err("order_channel", "Choose how this order was received.")
    ref = str(body.get("po_number") or "").strip()
    if channel in REFERENCE_REQUIRED and not ref:
        err("po_number", f"{REFERENCE_LABEL[channel]} is required.")
    od, rd = _date(body.get("order_date")), _date(body.get("requested_delivery_date"))
    if od is None:
        err("order_date", "Order date is required.")
    if rd is None:
        err("requested_delivery_date", "Requested delivery date is required.")
    elif od and rd < od:
        err("requested_delivery_date", "Delivery cannot be before the order date.")
    elif rd < TODAY:
        err("requested_delivery_date", f"Delivery cannot be before today ({TODAY.isoformat()}).")
    lines = body.get("lines") or []
    if not lines:
        err("lines", "Add at least one product.")
    seen: set[str] = set()
    for i, ln in enumerate(lines):
        sku = str(ln.get("sku") or "")
        if sku not in plant.PRODUCT_BY_SKU:
            err(f"lines.{i}.sku", "Choose a product.")
        elif sku in seen:
            err(f"lines.{i}.sku", "This product is already on the order. Change the quantity of the existing line.")
        seen.add(sku)
        qty = ln.get("qty")
        if not isinstance(qty, int) or qty <= 0:
            err(f"lines.{i}.qty", "Quantity must be more than zero.")
        raw = ln.get("requested_delivery_date")
        ld = _date(raw)
        if raw and ld is None:
            err(f"lines.{i}.requested_delivery_date", "Not a date.")
        elif ld and ((od and ld < od) or ld < TODAY):
            err(f"lines.{i}.requested_delivery_date", "Delivery cannot be before the order date or today.")
    warnings: list[str] = []
    if ref:
        dup = next((h for h in view["orders"] if h["status"] != "Cancelled" and str(h["po_number"] or "").strip().lower() == ref.lower()), None)
        if dup:
            warnings.append(f"Reference {ref} is already on order {dup['order_number']} ({dup['customer_name']}). Check this is not the same order twice.")
    if od and rd and (rd - od).days <= 1:
        warnings.append("Delivery is requested within a day of the order date.")
    return errors, warnings


def new_order(body: dict[str, Any], user: str, created_at: str) -> dict[str, Any]:
    """The order an approved create command carries; numbers follow the seed's CO-/SO- scheme with a session tag."""
    tag = uuid.uuid4().hex[:4].upper()
    c = CUSTOMER_BY_ID[body["customer_id"]]
    rd = to_date(body["requested_delivery_date"])
    return {
        "order_number": f"CO-{rd:%y%m%d}-{body['customer_id'].removeprefix('CUS-')}-{tag}",
        "customer_id": body["customer_id"],
        "po_number": str(body.get("po_number") or "").strip() or None,
        "order_channel": body["order_channel"],
        "order_date": to_date(body["order_date"]).isoformat(),
        "requested_delivery_date": rd.isoformat(),
        "customer_pic": body.get("customer_pic") or c["pic_name"],
        "delivery_address": body.get("delivery_address") or c["delivery_address"],
        "dock_number": body.get("dock_number") or c["dock_number"],
        "created_at": created_at,
        "created_by": user,
        "lines": [
            {
                "order_id": f"SO-{tag}-{i + 1:02d}",
                "line_no": i + 1,
                "customer_id": body["customer_id"],
                "sku": ln["sku"],
                "order_qty": int(ln["qty"]),
                "requested_delivery_date": to_date(ln.get("requested_delivery_date") or rd).isoformat(),
            }
            for i, ln in enumerate(body["lines"])
        ],
    }


def monthly_schedule(month: date) -> dict[str, int]:
    rows = warehouse.query("SELECT sku, plan_qty FROM fv.fact_monthly_plan WHERE plan_month = :m", {"m": month.replace(day=1)})
    return {r["sku"]: int(r["plan_qty"] or 0) for r in rows}


def match_customer(name: str) -> dict[str, Any] | None:
    n = name.lower()
    for c in plant.CUSTOMERS:
        full, cid = str(c["customer_name"]).lower(), str(c["customer_id"]).lower()
        tag = full.split("(")[-1].rstrip(")") if "(" in full else None
        if full in n or (tag and tag in n) or cid in n:
            return dict(c)
    return None


def check_po(po: dict[str, Any]) -> dict[str, Any]:
    """Validate an extracted customer PO against master data and the schedule on file; numbers are recomputed here, not taken from the LLM."""
    cust = match_customer(str(po.get("customer_name", "")))
    if cust is None:
        raise FVError(
            UNKNOWN_ENTITY, f"customer {po.get('customer_name')!r} is not in the customer master", {"valid": [c["customer_name"] for c in plant.CUSTOMERS]}
        )
    lines = po.get("lines") or []
    if not lines:
        raise FVError(INVALID_REQUEST, detail="the PO has no lines")
    model_share = {m: dict(mix) for m, mix in plant.CUSTOMER_MIX.items()}
    out, warnings = [], []
    for ln in lines:
        sku = str(ln.get("sku", "")).upper()
        if sku not in plant.PRODUCT_BY_SKU:
            warnings.append(f"{sku or '(blank)'} is not a Vidad SKU; line ignored")
            continue
        qty = int(ln["qty"])
        month = date.fromisoformat(f"{ln.get('month') or TODAY.strftime('%Y-%m')}-01")
        share = model_share[plant.PRODUCT_BY_SKU[sku].model].get(cust["customer_id"], 0.0)
        plan = monthly_schedule(month).get(sku)
        on_file = round(plan * share) if plan and share else None
        prev = ln.get("previous_qty") or on_file
        matches = None if on_file is None or not ln.get("previous_qty") else abs(int(ln["previous_qty"]) - on_file) <= ON_FILE_TOLERANCE * on_file
        if matches is False:
            warnings.append(f"{sku}: previous qty {int(ln['previous_qty']):,} on the PO differs from {on_file:,} on file")
        out.append(
            {
                "sku": sku,
                "month": month.strftime("%Y-%m"),
                "previous_qty": int(prev) if prev else None,
                "qty": qty,
                "change_qty": qty - int(prev) if prev else None,
                "change_pct": round(100 * (qty / int(prev) - 1), 1) if prev else None,
                "on_file_qty": on_file,
                "matches_on_file": matches,
                "per_working_day": round(qty / working_days_in_month(month), 1),
            }
        )
    return {
        "customer_id": cust["customer_id"],
        "customer_name": cust["customer_name"],
        "po_number": po.get("po_number"),
        "issue_date": po.get("issue_date"),
        "notes": po.get("notes"),
        "lines": out,
        "changed": [x for x in out if x["change_pct"]],
        "warnings": warnings,
        "source": "fv.fact_monthly_plan (OEM schedule on file) x customer share; dim_customer",
    }
