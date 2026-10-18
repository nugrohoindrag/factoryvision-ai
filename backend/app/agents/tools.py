from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from app import actions, effects, photos, plant
from app.ai import extract, vision
from app.documents import DOCUMENTS
from app.errors import FVError
from app.tools import delivery, forecast, material, mes, planning, purchasing, quality
from app.tools.impact import impact_calculator as _impact

UNKNOWN_ENTITY = "FV-DATA-001"


def _sid(tc: ToolContext) -> str:
    return tc.state.get("session_id", "")


def _ov(tc: ToolContext) -> effects.Overrides:
    return effects.overrides(_sid(tc))


def _demand(tc: ToolContext, apply_customer_po: bool) -> dict[str, Any] | None:
    return (_ov(tc).demand_change_pct or None) if apply_customer_po else None


def _guard(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except FVError as e:
        return e.tool_result()


def get_forecast(sku: str, tool_context: ToolContext) -> dict[str, Any]:
    r = forecast.bq_forecast(sku)
    r.pop("weekly_history", None)
    return r


def read_customer_po(document_name: str, tool_context: ToolContext) -> dict[str, Any]:
    if document_name not in DOCUMENTS:
        return FVError(UNKNOWN_ENTITY, f"unknown document {document_name}", {"valid": list(DOCUMENTS)}).tool_result()
    return _guard(extract.read_customer_po, DOCUMENTS[document_name][1]())


def get_capacity(apply_customer_po: bool, tool_context: ToolContext) -> dict[str, Any]:
    o = _ov(tc := tool_context)
    cyc = {m: effects.normal_cycle_factor(planning.machine_rates()) for m in o.maintenance}
    return planning.get_capacity(demand_change_pct=_demand(tc, apply_customer_po), cycle_override=cyc or None, extra_days=o.extra_shifts or None)


def run_scheduler(apply_customer_po: bool, allow_mold_moves: bool, maintain_imm04: bool, tool_context: ToolContext) -> dict[str, Any]:
    r = planning.run_scheduler(
        demand_change_pct=_demand(tool_context, apply_customer_po), allow_mold_moves=allow_mold_moves, maintenance=["IMM-04"] if maintain_imm04 else None
    )
    r.pop("schedule", None)
    return r


def compare_capacity_options(apply_customer_po: bool, tool_context: ToolContext) -> dict[str, Any]:
    return planning.what_if(demand_change_pct=_demand(tool_context, apply_customer_po))


def get_oee(days: int, tool_context: ToolContext) -> dict[str, Any]:
    r = mes.get_oee(days=max(1, min(days, 30)))
    r["by_machine"] = {k: {x: v[x] for x in ("availability_pct", "performance_pct", "quality_pct", "oee_pct")} for k, v in r["by_machine"].items()}
    return r


def get_downtime_pareto(days: int, tool_context: ToolContext) -> dict[str, Any]:
    return mes.downtime_pareto(days=max(1, min(days, 90)))


def get_machine_health(machine_id: str, tool_context: ToolContext) -> dict[str, Any]:
    r = mes.machine_health(machine_id)
    r.pop("weekly_mtbf", None)
    if machine_id in _ov(tool_context).maintenance:
        r.update(breakdown_risk="low", note="maintenance already approved in this session")
    return r


def twin_observations(tool_context: ToolContext) -> dict[str, Any]:
    cams = vision.latest(_sid(tool_context))
    return {"cameras": cams, "note": "no camera observations yet" if not cams else None, "source": "Twin Vision Analyzer (gemini-3.5-flash-lite)"}


def get_material_requirements(apply_customer_po: bool, tool_context: ToolContext) -> dict[str, Any]:
    return material.material_requirements(demand_change_pct=_demand(tool_context, apply_customer_po))


def get_stock_projection(material_id: str, apply_customer_po: bool, tool_context: ToolContext) -> dict[str, Any]:
    receipts = [r for r in _ov(tool_context).extra_receipts if r.get("material_id") == material_id]
    r = material.stock_projection(material_id, demand_change_pct=_demand(tool_context, apply_customer_po), extra_receipts=receipts or None)
    r["projection"] = r.get("projection", [])[::3]
    return r


def get_vendor_scorecard(category: str, tool_context: ToolContext) -> dict[str, Any]:
    return purchasing.vendor_performance(category or None)


def read_vendor_email(document_name: str, tool_context: ToolContext) -> dict[str, Any]:
    if document_name not in DOCUMENTS:
        return FVError(UNKNOWN_ENTITY, f"unknown document {document_name}", {"valid": list(DOCUMENTS)}).tool_result()
    return _guard(extract.read_vendor_email, DOCUMENTS[document_name][1]().decode())


def compare_vendors_for_po(material_id: str, need_by: str, qty_kg: float, tool_context: ToolContext) -> dict[str, Any]:
    return purchasing.recommend_po(material_id, need_by or None, qty_kg or None, extra_delay=_ov(tool_context).vendor_extra_delay or None)


def get_defect_correlation(days: int, tool_context: ToolContext) -> dict[str, Any]:
    return quality.defect_correlation(days=days or 45)


def inspect_conveyor_photo(photo_id: str, expected_sku: str, expected_color: str, tool_context: ToolContext) -> dict[str, Any]:
    entry = photos.find(photo_id=photo_id)
    if not entry:
        return FVError(UNKNOWN_ENTITY, f"unknown photo {photo_id}").tool_result()
    return _guard(extract.inspect_photo, photos.read(entry), photos.mime_type(entry), expected_sku, expected_color, photo_id)


def get_orders_at_risk(tool_context: ToolContext) -> dict[str, Any]:
    from app.modules import delivery_rates

    o = _ov(tool_context)
    r = delivery.orders_at_risk(rate_override=delivery_rates(o) or None)
    r["below_stock_target"] = r["below_stock_target"][:8]
    return r


def get_atp(sku: str, tool_context: ToolContext) -> dict[str, Any]:
    r = delivery.atp_check(sku)
    for s in r["skus"]:
        s.pop("stock_by_color", None)
    return r


def _propose(
    tc: ToolContext, kind: str, title: str, rationale: str, args: dict[str, Any], origin: str, impact_args: dict[str, Any] | None = None
) -> dict[str, Any]:
    sid, plan = _sid(tc), tc.state.get("plan_id")
    for a in actions.list_actions(sid, "pending"):
        if a["plan_id"] == plan and a["kind"] == kind and a["command"]["args"] == args:
            return {"action_id": a["action_id"], "status": "pending", "note": "already proposed", "impact": a["impact"]}
    impact = _impact(kind, impact_args or args)
    evidence = [{"source": s} for s in tc.state.get("sources", [])][-6:]
    doc = actions.propose(sid, kind, title, origin, rationale, args, evidence, impact, plan)
    return {"action_id": doc["action_id"], "status": "pending (needs human approval)", "impact": doc["impact"], "impact_breakdown": impact["breakdown"]}


def propose_maintenance(machine_id: str, rationale: str, tool_context: ToolContext) -> dict[str, Any]:
    if machine_id not in plant.MACHINE_BY_ID:
        return FVError(UNKNOWN_ENTITY, f"unknown machine {machine_id}").tool_result()
    return _propose(tool_context, "maintenance", f"Preventive maintenance on {machine_id}", rationale, {"machine_id": machine_id}, "mes")


def propose_mold_move(sku: str, to_machine_id: str, rationale: str, tool_context: ToolContext) -> dict[str, Any]:
    if to_machine_id not in plant.MOLD_COMPAT.get(sku, []):
        return FVError(UNKNOWN_ENTITY, f"mold {sku} does not fit {to_machine_id}", {"fits": plant.MOLD_COMPAT.get(sku)}).tool_result()
    return _propose(tool_context, "mold_move", f"Move {sku} mold to {to_machine_id}", rationale, {"sku": sku, "to_machine_id": to_machine_id}, "planning")


def draft_po(vendor_id: str, material_id: str, qty_kg: float, expected_date: str, rationale: str, tool_context: ToolContext) -> dict[str, Any]:
    if vendor_id not in plant.VENDOR_BY_ID or vendor_id not in plant.MATERIAL_VENDORS.get(material_id, []):
        return FVError(UNKNOWN_ENTITY, f"vendor {vendor_id} does not supply {material_id}").tool_result()
    args = {"vendor_id": vendor_id, "material_id": material_id, "qty": float(qty_kg), "expected_date": expected_date}
    name = plant.VENDOR_BY_ID[vendor_id]["vendor_name"]
    return _propose(
        tool_context,
        "purchase_order",
        f"PO {material_id} {qty_kg:,.0f} kg from {name}",
        rationale,
        args,
        "purchasing",
        {**args, "demand_change_pct": _ov(tool_context).demand_change_pct or None},
    )


def propose_hold(resin_lot_id: str, rationale: str, tool_context: ToolContext) -> dict[str, Any]:
    return _propose(tool_context, "quality_hold", f"Quality hold on resin lot {resin_lot_id}", rationale, {"resin_lot_id": resin_lot_id}, "quality")


def propose_ship_date(order_id: str, new_date: str, rationale: str, tool_context: ToolContext) -> dict[str, Any]:
    return _propose(
        tool_context,
        "ship_date",
        f"Revise ship date of {order_id} to {new_date}",
        rationale,
        {"order_id": order_id, "new_date": new_date},
        "delivery",
        {"order_ids": [order_id]},
    )


def draft_customer_notice(order_ids: list[str], message: str, tool_context: ToolContext) -> dict[str, Any]:
    return _propose(
        tool_context,
        "customer_notice",
        f"Customer notice for {len(order_ids)} order(s)",
        message,
        {"order_ids": order_ids, "message": message},
        "delivery",
        {"order_ids": order_ids},
    )


def impact_calculator(kind: str, machine_id: str, sku: str, to_machine_id: str, resin_lot_id: str, tool_context: ToolContext) -> dict[str, Any]:
    args = {k: v for k, v in {"machine_id": machine_id, "sku": sku, "to_machine_id": to_machine_id, "resin_lot_id": resin_lot_id}.items() if v}
    return _guard(_impact, kind, args)


def plan_summary(tool_context: ToolContext) -> dict[str, Any]:
    from app.tools.impact import plan_total

    plan = tool_context.state.get("plan_id")
    items = [a for a in actions.list_actions(_sid(tool_context)) if a["plan_id"] == plan]
    return {
        "plan_id": plan,
        "proposals": [{k: a[k] for k in ("action_id", "kind", "title", "origin_agent", "impact", "status")} for a in items],
        "total": plan_total([a["impact"] for a in items]),
    }


def plant_briefing(tool_context: ToolContext) -> dict[str, Any]:
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

MODULE_TOOLS = {
    "forecast": [get_forecast, read_customer_po],
    "planning": [get_capacity, run_scheduler, compare_capacity_options, propose_mold_move],
    "mes": [get_oee, get_downtime_pareto, get_machine_health, twin_observations, propose_maintenance],
    "material": [get_material_requirements, get_stock_projection],
    "purchasing": [get_vendor_scorecard, read_vendor_email, compare_vendors_for_po, draft_po],
    "quality": [get_defect_correlation, inspect_conveyor_photo, propose_hold],
    "delivery": [get_orders_at_risk, get_atp, propose_ship_date, draft_customer_notice],
}
ORCHESTRATOR_TOOLS = [plant_briefing, impact_calculator, plan_summary]

for _fn in [*(f for fns in MODULE_TOOLS.values() for f in fns), *ORCHESTRATOR_TOOLS]:
    _fn.__doc__ = TOOL_DOCS[_fn.__name__]
