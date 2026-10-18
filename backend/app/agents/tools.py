from collections.abc import Callable
from typing import Any, Protocol

from app import actions, effects, forecasts, outbox, photos, plans, plant, purchasing_docs, readiness
from app.agents import facts
from app.agents import material as material_facts
from app.agents import purchasing as purchasing_facts
from app.ai import extract, vision
from app.documents import DOCUMENTS
from app.errors import FVError
from app.tools import delivery, forecast, material, mes, planning, purchasing, quality
from app.tools.impact import impact_calculator as _impact
from app.tools.impact import plan_total

UNKNOWN_ENTITY = "FV-DATA-001"
FORECAST_FEATURE_UNAVAILABLE = "FV-AGT-001"
REPLAN_NOT_NEEDED = "FV-AGT-002"

ToolResult = dict[str, Any]


class ToolContext(Protocol):
    @property
    def state(self) -> Any: ...


def _sid(tc: ToolContext) -> str:
    return str(tc.state.get("session_id", ""))


def _ov(tc: ToolContext) -> effects.Overrides:
    return effects.overrides(_sid(tc))


def _demand(tc: ToolContext, *, apply_customer_po: bool) -> dict[str, float] | None:
    return (_ov(tc).demand_change_pct or None) if apply_customer_po else None


def _guard(fn: Callable[..., ToolResult], *args: Any) -> ToolResult:
    try:
        return fn(*args)
    except FVError as e:
        return e.tool_result()


def _unknown(message: str, detail: object = None) -> ToolResult:
    return FVError(UNKNOWN_ENTITY, message, detail).tool_result()


def get_forecast(*, sku: str) -> ToolResult:
    r = forecast.bq_forecast(sku)
    r.pop("weekly_history", None)
    return r


def read_customer_po(*, document_name: str) -> ToolResult:
    if document_name not in DOCUMENTS:
        return _unknown(f"unknown document {document_name}", {"valid": list(DOCUMENTS)})
    return _guard(extract.read_customer_po, DOCUMENTS[document_name][1]())


def _snapshot(sid: str, forecast_id: str, sku: str) -> ToolResult:
    snap = forecasts.get_snapshot(sid, forecast_id) if forecast_id else forecasts.get_snapshot(sid, forecasts.latest(sid)["forecast_id"])
    if sku and sku not in plant.PRODUCT_BY_SKU:
        return _unknown(f"unknown sku {sku}", {"valid_skus": plant.SKUS})
    return facts.snapshot_view(snap, sku or None)


def get_latest_forecast_snapshot(*, sku: str, tool_context: ToolContext) -> ToolResult:
    return _guard(_snapshot, _sid(tool_context), "", sku)


def get_forecast_snapshot(*, forecast_id: str, sku: str, tool_context: ToolContext) -> ToolResult:
    return _guard(_snapshot, _sid(tool_context), forecast_id, sku)


def _optional_forecast_api(name: str) -> Callable[..., ToolResult] | None:
    fn = getattr(forecasts, name, None)
    return fn if callable(fn) else None


def compare_forecast_to_actuals(*, forecast_id: str, tool_context: ToolContext) -> ToolResult:
    fn = _optional_forecast_api("comparison")
    if fn is None:
        return FVError(FORECAST_FEATURE_UNAVAILABLE, "forecast vs actual comparison is not available in this build").tool_result()
    return _guard(fn, _sid(tool_context), forecast_id)


def get_forecast_accuracy(*, sku: str, tool_context: ToolContext) -> ToolResult:
    fn = _optional_forecast_api("accuracy")
    if fn is None:
        return FVError(FORECAST_FEATURE_UNAVAILABLE, "forecast accuracy (MAPE / backtest) is not available in this build").tool_result()
    return _guard(fn, _sid(tool_context), sku or None)


def list_production_plans(*, status: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        rows = plans.list_plans(_sid(tool_context), status or None)
        keys = (*facts.PLAN_KEYS, "line_count", "total_planned")
        out = [{**{k: p.get(k) for k in keys}, "capacity_status": p.get("capacity_status") or facts.worst_status(p.get("lines", []))} for p in rows]
        return {"plans": out[:10], "count": len(out), "source": "production plans (session state)"}

    return _guard(run)


def get_production_plan(*, plan_id: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        sid = _sid(tool_context)
        if plan_id:
            return facts.plan_view(plans.get_plan(sid, plan_id))
        cur = facts.current_plan(sid)
        if cur is None:
            return {"plan": None, "note": "no production plan in this session yet; check_replan_trigger tells whether one should be proposed"}
        return facts.plan_view(plans.get_plan(sid, cur["plan_id"]))

    return _guard(run)


def check_replan_trigger(*, tool_context: ToolContext) -> ToolResult:
    return _guard(facts.replan_trigger, _sid(tool_context))


def propose_replan(*, reason: str, tool_context: ToolContext) -> ToolResult:
    try:
        t = facts.replan_trigger(_sid(tool_context))
    except FVError as e:
        return e.tool_result()
    if t["pending_replan_action_id"]:
        return {"action_id": t["pending_replan_action_id"], "status": "pending", "note": "a re-plan is already waiting for approval"}
    if not t["replan_needed"]:
        return FVError(REPLAN_NOT_NEEDED, detail={"latest_forecast_id": t["latest_forecast_id"], "current_plan": t["current_plan"]}).tool_result()
    d = t["molding_capacity_delta"]
    change = ", ".join(f"{k} {v:+g}%" for k, v in sorted(t["demand_change_pct"].items()))
    args = {"reason": reason, "forecast_id": t["latest_forecast_id"], "demand_change_pct": t["demand_change_pct"]}
    title = f"Re-plan production from forecast {t['latest_forecast_id']}" + (f" and PO change {change}" if change else "")
    delta = (
        f"Molding capacity delta (planning.get_capacity): {d['group']} gap {d['before_gap_pct']:+.1f}% -> {d['after_gap_pct']:+.1f}% "
        f"({d['gap_change_pts']:+.1f} pts, {d['before_status']} -> {d['after_status']}); bottleneck {d['bottleneck_machine']} at {d['bottleneck_load_pct']}% load"
    )
    evidence = [{"source": r} for r in t["reasons"]] + [{"source": delta, "capacity_delta": d}]
    r = _propose(tool_context, "replan", title, reason, args, "planning", extra_evidence=evidence)
    return {**r, "reasons": t["reasons"], "molding_capacity_delta": d, "note": "a new Draft plan is created only after a manager approves"}


def draft_production_plan(*, period_start: str, period_end: str, include_forecast: bool, tool_context: ToolContext) -> ToolResult:
    r = _guard(lambda: plans.draft(_sid(tool_context), period_start, period_end, include_forecast=include_forecast))
    r.pop("args", None)
    return r


def propose_draft_plan(*, period_start: str, period_end: str, include_forecast: bool, rationale: str, tool_context: ToolContext) -> ToolResult:
    d = _guard(lambda: plans.draft(_sid(tool_context), period_start, period_end, include_forecast=include_forecast))
    if "error" in d:
        return d
    title = f"Draft production plan {d['period_start']} to {d['period_end']}: {len(d['lines'])} line(s), {d['summary']['planned_qty']:,} pcs"
    evidence = [{"source": d["source"], "rules": d["rules"], "lines": d["lines"], "gaps": d["gaps"]}]
    return _propose(
        tool_context, "production_plan_draft", title, f"{rationale} {plans.draft_rationale(d)}".strip(), d["args"], "planning", extra_evidence=evidence
    )


def get_capacity(*, apply_customer_po: bool, tool_context: ToolContext) -> ToolResult:
    o = _ov(tool_context)
    cyc = {m: effects.normal_cycle_factor(planning.machine_rates()) for m in o.maintenance}
    demand = _demand(tool_context, apply_customer_po=apply_customer_po)
    return planning.get_capacity(
        demand_change_pct=demand, cycle_override=cyc or None, extra_days=o.extra_shifts or None, utilization_pct=o.planning_utilization_pct
    )


def run_scheduler(*, apply_customer_po: bool, allow_mold_moves: bool, maintain_imm04: bool, tool_context: ToolContext) -> ToolResult:
    r = planning.run_scheduler(
        demand_change_pct=_demand(tool_context, apply_customer_po=apply_customer_po),
        allow_mold_moves=allow_mold_moves,
        maintenance=["IMM-04"] if maintain_imm04 else None,
    )
    r.pop("schedule", None)
    return r


def compare_capacity_options(*, apply_customer_po: bool, tool_context: ToolContext) -> ToolResult:
    return planning.what_if(demand_change_pct=_demand(tool_context, apply_customer_po=apply_customer_po))


def get_oee(*, days: int) -> ToolResult:
    r = mes.get_oee(days=max(1, min(days, 30)))
    keys = ("availability_pct", "performance_pct", "quality_pct", "oee_pct")
    r["by_machine"] = {k: {x: v[x] for x in keys} for k, v in r["by_machine"].items()}
    return r


def get_downtime_pareto(*, days: int) -> ToolResult:
    return mes.downtime_pareto(days=max(1, min(days, 90)))


def get_machine_health(*, machine_id: str, tool_context: ToolContext) -> ToolResult:
    r = mes.machine_health(machine_id)
    r.pop("weekly_mtbf", None)
    if machine_id in _ov(tool_context).maintenance:
        r.update(breakdown_risk="low", note="maintenance already approved in this session")
    return r


def twin_observations(*, tool_context: ToolContext) -> ToolResult:
    cams = vision.latest(_sid(tool_context))
    return {"cameras": cams, "note": "no camera observations yet" if not cams else None, "source": "Twin Vision Analyzer (gemini-3.5-flash-lite)"}


def get_material_requirements(*, apply_customer_po: bool, tool_context: ToolContext) -> ToolResult:
    return material.material_requirements(demand_change_pct=_demand(tool_context, apply_customer_po=apply_customer_po))


def get_stock_projection(*, material_id: str, apply_customer_po: bool, tool_context: ToolContext) -> ToolResult:
    receipts = [r for r in _ov(tool_context).extra_receipts if r.get("material_id") == material_id]
    demand = _demand(tool_context, apply_customer_po=apply_customer_po)
    r = material.stock_projection(material_id, demand_change_pct=demand, extra_receipts=receipts or None)
    r["projection"] = r.get("projection", [])[::3]
    return r


def get_material_readiness(*, date_from: str, date_to: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        r = readiness.readiness(_sid(tool_context), date_from or None, date_to or None)
        return {**r, "plans": r.get("plans", [])[:10], "source": "material readiness per plan (BOM x plan vs stock and dated incoming)"}

    return _guard(run)


def get_plan_material_check(*, plan_id: str, tool_context: ToolContext) -> ToolResult:
    return _guard(material_facts.plan_check, _sid(tool_context), plan_id)


def check_plan_material(*, plan_id: str, tool_context: ToolContext) -> ToolResult:
    return _guard(lambda: material_facts.check_view(readiness.check_plan(_sid(tool_context), plan_id, save=False), saved=False))


def get_latest_mrp(*, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        r, new = material_facts.latest_or_new_run(_sid(tool_context), str(getattr(tool_context, "agent_name", "") or "agent"))
        return material_facts.mrp_view(r, computed_now=new)

    return _guard(run)


def propose_po_from_shortage(*, material_id: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        sid = _sid(tool_context)
        r, _new = material_facts.latest_or_new_run(sid, str(getattr(tool_context, "agent_name", "") or "agent"))
        doc, created = material_facts.propose_po(sid, r, material_id, plan_id=tool_context.state.get("plan_id"))
        out = {"action_id": doc["action_id"], "title": doc["title"], "args": doc["command"]["args"], "impact": doc["impact"], "mrp_run_id": r["run_id"]}
        return {
            **out,
            "status": "pending (needs human approval)" if created else "pending",
            "note": None if created else "a PO for this material is already waiting for approval",
        }

    return _guard(run)


def get_vendor_scorecard(*, category: str) -> ToolResult:
    return purchasing.vendor_performance(category or None)


def read_vendor_email(*, document_name: str, tool_context: ToolContext) -> ToolResult:
    if document_name not in DOCUMENTS:
        return _unknown(f"unknown document {document_name}", {"valid": list(DOCUMENTS)})
    out = _guard(extract.read_vendor_email, DOCUMENTS[document_name][1]().decode())
    if isinstance(out, dict) and "error" not in out:
        draft = outbox.vendor_reply_draft(_sid(tool_context), out)
        if draft:
            out = {**out, "reply_draft": {"email_id": draft["email_id"], "status": "draft in the Outbox, a person sends it"}}
    return out


def compare_vendors_for_po(*, material_id: str, need_by: str, qty_kg: float, tool_context: ToolContext) -> ToolResult:
    return purchasing.recommend_po(material_id, need_by or None, qty_kg or None, extra_delay=_ov(tool_context).vendor_extra_delay or None)


def list_purchase_orders(*, status: str, tool_context: ToolContext) -> ToolResult:
    return _guard(lambda: purchasing_facts.list_pos(_sid(tool_context), status or None))


def get_purchase_order(*, po_id: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        po = purchasing_docs.get_po(_sid(tool_context), po_id)
        return {**purchasing_facts.po_view(po), "receipts": po.get("receipts", []), "history": po.get("history", []), "source": purchasing_facts.SOURCE}

    return _guard(run)


def list_overdue_purchase_orders(*, tool_context: ToolContext) -> ToolResult:
    return _guard(lambda: purchasing_facts.list_pos(_sid(tool_context), overdue=True))


def recommend_vendor(*, material_id: str, qty: float, need_by: str) -> ToolResult:
    return _guard(purchasing_facts.vendor_options, material_id, float(qty), need_by or None)


def list_requisitions(*, status: str, tool_context: ToolContext) -> ToolResult:
    return _guard(lambda: purchasing_facts.list_prs(_sid(tool_context), status or None))


def _proposal(doc: dict[str, Any], *, created: bool, note: str) -> ToolResult:
    out = {"action_id": doc["action_id"], "title": doc["title"], "args": doc["command"]["args"], "rationale": doc["rationale"], "impact": doc["impact"]}
    return {**out, "status": "pending (needs human approval)" if created else "pending", "note": None if created else note}


def propose_requisition_from_mrp(*, run_id: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        user = str(getattr(tool_context, "agent_name", "") or "agent")
        doc, created = purchasing_facts.propose_requisition(_sid(tool_context), run_id or None, user=user, plan_id=tool_context.state.get("plan_id"))
        return _proposal(doc, created=created, note="a requisition for this MRP run is already waiting for approval")

    return _guard(run)


def propose_purchase_order(*, material_id: str, qty: float, need_by: str, vendor_id: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        sid = _sid(tool_context)
        doc, created = purchasing_facts.propose_po(sid, material_id, float(qty), need_by or None, vendor_id or None, plan_id=tool_context.state.get("plan_id"))
        return _proposal(doc, created=created, note="a PO for this material is already waiting for approval")

    return _guard(run)


def propose_pos_for_plan_shortage(*, plan_id: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        sid = _sid(tool_context)
        return purchasing_facts.propose_for_plan_shortage(sid, readiness.check_plan(sid, plan_id))

    return _guard(run)


def propose_expedite(*, po_id: str, new_promised_date: str, reason: str, tool_context: ToolContext) -> ToolResult:
    def run() -> ToolResult:
        doc, created = purchasing_facts.propose_expedite(_sid(tool_context), po_id, new_promised_date, reason, plan_id=tool_context.state.get("plan_id"))
        return _proposal(doc, created=created, note="an expedite for this PO is already waiting for approval")

    return _guard(run)


def get_defect_correlation(*, days: int) -> ToolResult:
    return quality.defect_correlation(days=days or 45)


def inspect_conveyor_photo(*, photo_id: str, expected_sku: str, expected_color: str) -> ToolResult:
    entry = photos.find(photo_id=photo_id)
    if not entry:
        return _unknown(f"unknown photo {photo_id}")
    return _guard(extract.inspect_photo, photos.read(entry), photos.mime_type(entry), expected_sku, expected_color, photo_id)


def get_orders_at_risk(*, tool_context: ToolContext) -> ToolResult:
    from app.modules import delivery_rates

    r = delivery.orders_at_risk(rate_override=delivery_rates(_ov(tool_context)) or None)
    r["below_stock_target"] = r["below_stock_target"][:8]
    return r


def get_atp(*, sku: str) -> ToolResult:
    r = delivery.atp_check(sku)
    for s in r["skus"]:
        s.pop("stock_by_color", None)
    return r


def _propose(
    tc: ToolContext,
    kind: str,
    title: str,
    rationale: str,
    args: dict[str, Any],
    origin: str,
    impact_args: dict[str, Any] | None = None,
    extra_evidence: list[dict[str, Any]] | None = None,
) -> ToolResult:
    sid, plan = _sid(tc), tc.state.get("plan_id")
    for a in actions.list_actions(sid, "pending"):
        if a["plan_id"] == plan and a["kind"] == kind and a["command"]["args"] == args:
            return {"action_id": a["action_id"], "status": "pending", "note": "already proposed", "impact": a["impact"]}
    try:
        impact = _impact(kind, impact_args or args)
    except FVError as e:
        return e.tool_result()
    evidence = [{"source": s} for s in tc.state.get("sources", [])][-6:] + (extra_evidence or [])
    doc = actions.propose(sid, kind, title, origin, rationale, args, evidence, impact, plan)
    return {"action_id": doc["action_id"], "status": "pending (needs human approval)", "impact": doc["impact"], "impact_breakdown": impact["breakdown"]}


def propose_maintenance(*, machine_id: str, rationale: str, tool_context: ToolContext) -> ToolResult:
    if machine_id not in plant.MACHINE_BY_ID:
        return _unknown(f"unknown machine {machine_id}")
    return _propose(tool_context, "maintenance", f"Preventive maintenance on {machine_id}", rationale, {"machine_id": machine_id}, "mes")


def propose_mold_move(*, sku: str, to_machine_id: str, rationale: str, tool_context: ToolContext) -> ToolResult:
    if to_machine_id not in plant.MOLD_COMPAT.get(sku, []):
        return _unknown(f"mold {sku} does not fit {to_machine_id}", {"fits": plant.MOLD_COMPAT.get(sku)})
    args = {"sku": sku, "to_machine_id": to_machine_id}
    return _propose(tool_context, "mold_move", f"Move {sku} mold to {to_machine_id}", rationale, args, "planning")


def draft_po(*, vendor_id: str, material_id: str, qty_kg: float, expected_date: str, rationale: str, tool_context: ToolContext) -> ToolResult:
    if vendor_id not in plant.VENDOR_BY_ID or vendor_id not in plant.MATERIAL_VENDORS.get(material_id, []):
        return _unknown(f"vendor {vendor_id} does not supply {material_id}")
    args = {"vendor_id": vendor_id, "material_id": material_id, "qty": float(qty_kg), "expected_date": expected_date}
    name = plant.VENDOR_BY_ID[vendor_id]["vendor_name"]
    impact_args = {**args, "demand_change_pct": _ov(tool_context).demand_change_pct or None}
    return _propose(tool_context, "purchase_order", f"PO {material_id} {qty_kg:,.0f} kg from {name}", rationale, args, "purchasing", impact_args)


def propose_hold(*, resin_lot_id: str, rationale: str, tool_context: ToolContext) -> ToolResult:
    args = {"resin_lot_id": resin_lot_id}
    return _propose(tool_context, "quality_hold", f"Quality hold on resin lot {resin_lot_id}", rationale, args, "quality")


def propose_ship_date(*, order_id: str, new_date: str, rationale: str, tool_context: ToolContext) -> ToolResult:
    args = {"order_id": order_id, "new_date": new_date}
    title = f"Revise ship date of {order_id} to {new_date}"
    return _propose(tool_context, "ship_date", title, rationale, args, "delivery", {"order_ids": [order_id]})


def draft_customer_notice(*, order_ids: list[str], message: str, tool_context: ToolContext) -> ToolResult:
    args = {"order_ids": order_ids, "message": message}
    title = f"Customer notice for {len(order_ids)} order(s)"
    return _propose(tool_context, "customer_notice", title, message, args, "delivery", {"order_ids": order_ids})


def impact_calculator(*, kind: str, machine_id: str, sku: str, to_machine_id: str, resin_lot_id: str) -> ToolResult:
    named = {"machine_id": machine_id, "sku": sku, "to_machine_id": to_machine_id, "resin_lot_id": resin_lot_id}
    return _guard(_impact, kind, {k: v for k, v in named.items() if v})


def plan_summary(*, tool_context: ToolContext) -> ToolResult:
    plan = tool_context.state.get("plan_id")
    items = [a for a in actions.list_actions(_sid(tool_context)) if a["plan_id"] == plan]
    keys = ("action_id", "kind", "title", "origin_agent", "impact", "status")
    return {"plan_id": plan, "proposals": [{k: a[k] for k in keys} for a in items], "total": plan_total([a["impact"] for a in items])}


def plant_briefing() -> ToolResult:
    from app.briefing import briefing

    return briefing()


TOOL_DOCS: dict[str, str] = {
    "get_forecast": (
        "Six-week demand forecast for one SKU (BigQuery ML ARIMA_PLUS in the cloud) and its change versus the last 7 days.\n"
        "\n"
        "Args:\n"
        "    sku: product SKU, e.g. FB-ARKA."
    ),
    "read_customer_po": (
        "Read a customer PO or delivery schedule PDF with the Document & Photo Extractor.\n"
        "\n"
        "Args:\n"
        "    document_name: name of the received document, e.g. customer-po-oem-a.pdf."
    ),
    "get_latest_forecast_snapshot": (
        "Newest Generated forecast snapshot (number FC-..., engine, window, horizon, plans that use it) with the weekly forecast per SKU, "
        "the six-week trend in percent and the insufficient-history flags. The numbers are stored in the snapshot and never change.\n"
        "\n"
        "Args:\n"
        "    sku: product SKU, e.g. FB-ARKA, or empty for every SKU in the snapshot."
    ),
    "get_forecast_snapshot": (
        "One forecast snapshot by its number, same fields as get_latest_forecast_snapshot (also works for Superseded snapshots).\n"
        "\n"
        "Args:\n"
        "    forecast_id: snapshot number, e.g. FC-2027-02-001.\n"
        "    sku: product SKU, or empty for every SKU."
    ),
    "compare_forecast_to_actuals": (
        "Forecast vs the orders that actually came in for an older snapshot, per week, with the variance.\n"
        "\n"
        "Args:\n"
        "    forecast_id: snapshot number, e.g. FC-2027-02-001."
    ),
    "get_forecast_accuracy": ("Forecast accuracy (MAPE from ML.EVALUATE or a 4-week backtest).\n\nArgs:\n    sku: product SKU, or empty for all."),
    "list_production_plans": (
        "Production plans of this session, newest first: number, period, status, forecast snapshot used, worst capacity status.\n"
        "\n"
        "Args:\n"
        "    status: Draft, Planning, Ready, Confirmed, In Execution, Completed, Cancelled, or empty for all."
    ),
    "get_production_plan": (
        "One production plan with per-line capacity: demand, forecast, planned qty and the capacity status "
        "(Within Plan, Additional Demand, Capacity Up Required) with total, planning, buffer, gap and the machines that count.\n"
        "\n"
        "Args:\n"
        "    plan_id: plan id, or empty for the current (newest not cancelled) plan."
    ),
    "check_replan_trigger": (
        "Deterministic re-plan trigger: is there a newer forecast snapshot or a customer PO change that the current plan does not cover? "
        "Returns the reasons and the molding capacity delta of the 3,500 t group before and after the change."
    ),
    "propose_replan": (
        "Propose a re-plan: after a manager approves, a new Draft production plan is built from the latest forecast snapshot and the "
        "current customer PO demand. Creates a pending proposal only; the forecast and PO change are filled in from the trigger.\n"
        "\n"
        "Args:\n"
        "    reason: one or two sentences citing the trigger and the capacity delta numbers."
    ),
    "draft_production_plan": (
        "Compute a draft production plan for a period from open customer orders, forecast not yet ordered and capacity. "
        "Firm orders first, earlier delivery first; a line above capacity is planned at what fits and its gap is reported. Saves nothing.\n"
        "\n"
        "Args:\n"
        "    period_start: YYYY-MM-DD, today (2027-02-01) or later.\n"
        "    period_end: YYYY-MM-DD, on or after the start.\n"
        "    include_forecast: also plan forecast that customers have not ordered yet (from the latest forecast snapshot)."
    ),
    "propose_draft_plan": (
        "Send the draft production plan to the Action Center as a pending proposal. After a person approves it, it becomes a Draft plan "
        "the PPIC planner finishes in the wizard (work orders, schedule, resources, confirm). The quantities are recomputed here, not taken from you.\n"
        "\n"
        "Args:\n"
        "    period_start: YYYY-MM-DD.\n"
        "    period_end: YYYY-MM-DD.\n"
        "    include_forecast: same choice as in draft_production_plan.\n"
        "    rationale: one or two sentences: why this draft, and what the planner must decide about each capacity gap."
    ),
    "get_capacity": (
        "Molding capacity check for this month: load per IMM and per tonnage group, bottleneck and capacity gap.\n"
        "\n"
        "Args:\n"
        "    apply_customer_po: true to include the demand change from the latest customer PO in this session."
    ),
    "run_scheduler": (
        "Weekly molding plan from OR-Tools CP-SAT (mold compatibility, changeovers). Returns fill rate, unmet qty and mold moves.\n"
        "\n"
        "Args:\n"
        "    apply_customer_po: include the demand change from the latest customer PO.\n"
        "    allow_mold_moves: allow molds to move to other compatible machines.\n"
        "    maintain_imm04: assume IMM-04 is maintained in week 1 (cycle back to normal)."
    ),
    "compare_capacity_options": (
        "What-if: compare baseline, IMM-04 maintenance, moving the Arka mold to IMM-05, Sunday shifts, and maintenance + Sunday shifts.\n"
        "\n"
        "Args:\n"
        "    apply_customer_po: include the demand change from the latest customer PO."
    ),
    "get_oee": (
        "OEE per machine, per shift and for the plant over the last `days` production days (time-weighted).\n"
        "\n"
        "Args:\n"
        "    days: window length in days, 1 = yesterday."
    ),
    "get_downtime_pareto": ("Top 5 downtime reasons in hours and Rupiah.\n\nArgs:\n    days: look-back window in days."),
    "get_machine_health": ("Breakdown risk of one machine from its cycle-time trend and MTBF trend.\n\nArgs:\n    machine_id: e.g. IMM-04."),
    "twin_observations": ("Latest observations of the virtual cameras (Twin Vision Analyzer) for this session."),
    "get_material_requirements": (
        "Material requirement this month = plan x BOM, against stock on hand.\n"
        "\n"
        "Args:\n"
        "    apply_customer_po: include the demand change from the latest customer PO."
    ),
    "get_stock_projection": (
        "Day-by-day projection and stockout date for a material (actual use rate, open and approved POs).\n"
        "\n"
        "Args:\n"
        "    material_id: e.g. PP-EPDM-T20.\n"
        "    apply_customer_po: include the demand change from the latest customer PO."
    ),
    "get_material_readiness": (
        "Material readiness per production plan in a date range (and the OEM monthly plan): status READY / PARTIAL / SHORTAGE, "
        "readiness percent and the number of short materials, plus KPIs.\n"
        "\n"
        "Args:\n"
        "    date_from: YYYY-MM-DD, or empty for today.\n"
        "    date_to: YYYY-MM-DD, or empty for the default range."
    ),
    "get_plan_material_check": (
        "Material check of one plan: status, readiness percent, and every short material with colour, required and available qty, "
        "shortage and need date. Uses the saved check, or computes one without saving.\n"
        "\n"
        "Args:\n"
        "    plan_id: plan id, or OEM-YYYY-MM for the OEM monthly plan."
    ),
    "check_plan_material": (
        "Read-only material impact of a (draft) production plan before it is approved: status, readiness percent and the short "
        "materials with colour, shortage and need date. Saves nothing.\n"
        "\n"
        "Args:\n"
        "    plan_id: plan id."
    ),
    "get_latest_mrp": (
        "Latest MRP run of this session (computed now over the next 30 days if none exists): run id, horizon and the materials with "
        "a net requirement, each with net qty, need date, order-by date, lead time, default vendor and recommendation."
    ),
    "propose_po_from_shortage": (
        "Propose a draft PO for a short material from the latest MRP run. Quantity, vendor, arrival date and price are taken from the "
        "MRP run and the material master, never from you. Creates a pending proposal only; one per material while it is pending.\n"
        "\n"
        "Args:\n"
        "    material_id: e.g. B-C07, from get_latest_mrp."
    ),
    "get_vendor_scorecard": (
        "On-time rate, average delay and effective lead time per vendor.\n\nArgs:\n    category: Resin, Paint, Component, Packaging, or empty for all."
    ),
    "read_vendor_email": (
        "Read a vendor email with the Document & Photo Extractor (new delivery date, delay).\n\nArgs:\n    document_name: e.g. vendor-email-andalas.txt."
    ),
    "compare_vendors_for_po": (
        "Expected arrival and price per vendor for an urgent PO (lead time + average delay + announced delay).\n"
        "\n"
        "Args:\n"
        "    material_id: e.g. PP-EPDM-T20.\n"
        "    need_by: date the material must arrive, YYYY-MM-DD (e.g. the stockout date).\n"
        "    qty_kg: order quantity in kg."
    ),
    "list_purchase_orders": (
        "Purchase orders of this session plus the seed open POs (origin seed is read-only): vendor, status, promised and expected "
        "date, value, received percent, overdue and days late, and lines with open qty.\n"
        "\n"
        "Args:\n"
        "    status: Draft, Issued, Partially Received, Received, Closed, Cancelled, or empty for all."
    ),
    "get_purchase_order": (
        "One purchase order with its lines (ordered, received, open qty), receipts and history.\n\nArgs:\n    po_id: e.g. PO-2702-S-001 or PO-2702-030."
    ),
    "list_overdue_purchase_orders": ("Open purchase orders whose expected date has passed and that are not fully received, with days late."),
    "recommend_vendor": (
        "Vendor options for any material: expected arrival (today + quoted lead time + average delay + announced delay), whether it "
        "arrives before the need date, unit price and PO value, the recommended vendor and the cheapest priced one.\n"
        "\n"
        "Args:\n"
        "    material_id: e.g. PP-EPDM-T20 or B-C07.\n"
        "    qty: order quantity in the material's unit.\n"
        "    need_by: date the material must arrive, YYYY-MM-DD, or empty."
    ),
    "list_requisitions": (
        "Purchase requisitions (PR) of this session with lines, suggested vendor, duplicate flag and linked POs.\n"
        "\n"
        "Args:\n"
        "    status: Draft, Submitted, Approved, Rejected, Cancelled, Converted, or empty for all."
    ),
    "propose_requisition_from_mrp": (
        "Propose a purchase requisition with every material that has a net requirement in an MRP run. Lines, quantities, need dates "
        "and vendor options come from the run and the vendor data, never from you. Creates a pending proposal only; on approval the "
        "PR is created and approved.\n"
        "\n"
        "Args:\n"
        "    run_id: e.g. MRP-2027-02-001, or empty for the latest run."
    ),
    "propose_purchase_order": (
        "Propose a purchase order for one material. Expected arrival, price, PO value and the premium vs the cheapest vendor are "
        "computed from the vendor data. Creates a pending proposal only; on approval a session PO is created and issued.\n"
        "\n"
        "Args:\n"
        "    material_id: e.g. B-C07.\n"
        "    qty: quantity in the material's unit, from MRP or readiness.\n"
        "    need_by: need date YYYY-MM-DD, or empty.\n"
        "    vendor_id: e.g. VEN-MERAPI, or empty for the recommended vendor."
    ),
    "propose_pos_for_plan_shortage": (
        "Check a production plan's material readiness and propose one purchase order per short material: quantity is the "
        "shortage rounded up, need date from the plan, vendor the one recommended to arrive in time. The POs are grouped as "
        "RDY-<plan number> for one approval; on approval each PO is issued and emailed to its vendor. Pending proposals only.\n"
        "\n"
        "Args:\n"
        "    plan_id: production plan id, from get_material_readiness or list_production_plans."
    ),
    "propose_expedite": (
        "Propose to expedite an open session PO to an earlier promised date. Creates a pending proposal only.\n"
        "\n"
        "Args:\n"
        "    po_id: e.g. PO-2702-S-001, from list_purchase_orders.\n"
        "    new_promised_date: YYYY-MM-DD, earlier than the current promised date.\n"
        "    reason: one sentence citing the need date or shortage."
    ),
    "get_defect_correlation": (
        "Defect rate by resin lot and by humidity band, with lift versus the overall rate, and hold candidates.\n"
        "\n"
        "Args:\n"
        "    days: look-back window (default 45)."
    ),
    "inspect_conveyor_photo": (
        "Verify a bumper photo from the INS-01 conveyor against the order line (model, colour, defects).\n"
        "\n"
        "Args:\n"
        "    photo_id: catalog photo id, e.g. P008.\n"
        "    expected_sku: SKU on the order line.\n"
        "    expected_color: colour code on the order line, e.g. C02."
    ),
    "get_orders_at_risk": (
        "Open orders whose estimated completion is after the requested date, with penalty exposure, and SKU x colour below the 6-hour stock target."
    ),
    "get_atp": ("Available-to-promise and estimated completion per open order of one SKU.\n\nArgs:\n    sku: e.g. FB-ARKA."),
    "propose_maintenance": (
        "Propose preventive maintenance (machine stops ~2 h, cycle time back to normal). Creates a pending proposal only.\n"
        "\n"
        "Args:\n"
        "    machine_id: e.g. IMM-04.\n"
        "    rationale: one or two sentences citing the numbers."
    ),
    "propose_mold_move": (
        "Propose moving a SKU's mold to another compatible IMM (a swap; both machines changeover 90 min). Creates a pending proposal only.\n"
        "\n"
        "Args:\n"
        "    sku: e.g. FB-ARKA.\n"
        "    to_machine_id: e.g. IMM-05; must fit the mold.\n"
        "    rationale: one or two sentences citing the numbers."
    ),
    "draft_po": (
        "Draft a purchase order (pending proposal; nothing is sent to the vendor before approval).\n"
        "\n"
        "Args:\n"
        "    vendor_id: e.g. VEN-MERAPI.\n"
        "    material_id: e.g. PP-EPDM-T20.\n"
        "    qty_kg: quantity in kg.\n"
        "    expected_date: expected arrival YYYY-MM-DD (from compare_vendors_for_po).\n"
        "    rationale: one or two sentences citing the numbers."
    ),
    "propose_hold": (
        "Propose a quality hold on all bumpers molded from one resin lot (becomes quality_hold OPEN after approval).\n"
        "\n"
        "Args:\n"
        "    resin_lot_id: e.g. LOT-PPT20-0918.\n"
        "    rationale: one or two sentences citing the numbers."
    ),
    "propose_ship_date": (
        "Propose a revised ship date for one order at risk (pending proposal).\n"
        "\n"
        "Args:\n"
        "    order_id: e.g. SO-270204-002.\n"
        "    new_date: YYYY-MM-DD, from the estimated completion.\n"
        "    rationale: one sentence."
    ),
    "draft_customer_notice": (
        "Draft a customer notice in English about revised ship dates (pending proposal; not sent before approval).\n"
        "\n"
        "Args:\n"
        "    order_ids: the affected orders.\n"
        "    message: the full message text, ready to copy."
    ),
    "impact_calculator": (
        "Impact in Rupiah, scrap kg, kWh and CO2e of one action kind (positive = saved or avoided). Use the numbers as they are.\n"
        "\n"
        "Args:\n"
        "    kind: maintenance, mold_move or quality_hold.\n"
        "    machine_id: for maintenance, e.g. IMM-04 (else empty).\n"
        "    sku: for mold_move, e.g. FB-ARKA (else empty).\n"
        "    to_machine_id: for mold_move, e.g. IMM-05 (else empty).\n"
        "    resin_lot_id: for quality_hold (else empty)."
    ),
    "plan_summary": ("All proposals created in this plan so far, with their impact and the plan total."),
    "plant_briefing": ("Morning briefing numbers: yesterday's OEE, top 3 losses in Rupiah, this week's risks."),
}

Tool = Callable[..., ToolResult]

MODULE_TOOLS: dict[str, list[Tool]] = {
    "forecast": [get_forecast, get_latest_forecast_snapshot, get_forecast_snapshot, compare_forecast_to_actuals, get_forecast_accuracy, read_customer_po],
    "planning": [
        list_production_plans,
        get_production_plan,
        check_replan_trigger,
        get_capacity,
        run_scheduler,
        compare_capacity_options,
        propose_replan,
        propose_mold_move,
        draft_production_plan,
        propose_draft_plan,
        check_plan_material,
    ],
    "mes": [get_oee, get_downtime_pareto, get_machine_health, twin_observations, propose_maintenance],
    "material": [get_material_requirements, get_stock_projection, get_material_readiness, get_plan_material_check, get_latest_mrp],
    "purchasing": [
        get_vendor_scorecard,
        read_vendor_email,
        compare_vendors_for_po,
        draft_po,
        get_latest_mrp,
        propose_po_from_shortage,
        list_purchase_orders,
        get_purchase_order,
        list_overdue_purchase_orders,
        recommend_vendor,
        list_requisitions,
        propose_requisition_from_mrp,
        propose_purchase_order,
        get_material_readiness,
        propose_pos_for_plan_shortage,
        propose_expedite,
    ],
    "quality": [get_defect_correlation, inspect_conveyor_photo, propose_hold],
    "delivery": [get_orders_at_risk, get_atp, propose_ship_date, draft_customer_notice],
}
ORCHESTRATOR_TOOLS: list[Tool] = [plant_briefing, impact_calculator, plan_summary]

for _fn in [*(f for fns in MODULE_TOOLS.values() for f in fns), *ORCHESTRATOR_TOOLS]:
    _fn.__doc__ = TOOL_DOCS[_fn.__name__]
