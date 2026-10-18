from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app import actions, twin
from app.agents import tools as agent_tools
from app.agents.runner import summarize
from app.config import settings
from app.documents import VENDOR_EMAIL_FACTS, demo_po_line
from app.errors import FVError
from app.simulation.engine import SCENARIOS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

UNKNOWN_SCENARIO = "FV-REQ-003"

DISRUPTION_PROMPT = (
    "Disruption: OEM-A sent a revised delivery schedule (customer-po-oem-a.pdf) raising FB-ARKA; the resin vendor for PP-EPDM-T20 "
    "emailed about a late delivery (vendor-email-andalas.txt); IMM-04 is slowing down. Trace the impact across all modules and "
    "produce one action plan."
)


def path(scenario: str) -> Path:
    return settings.replay_dir / f"{scenario}.json"


def load(scenario: str) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise FVError(UNKNOWN_SCENARIO, detail={"scenario": scenario, "valid": list(SCENARIOS)})
    p = path(scenario)
    if not p.exists():
        raise FVError(UNKNOWN_SCENARIO, f"no recording for {scenario}", {"record": f"python -m app.agents.replay record --scenario {scenario}"})
    rec: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return rec


def play(session_id: str, scenario: str) -> dict[str, Any]:
    rec = load(scenario)
    twin.reset(session_id, scenario)
    plan_id = f"PLAN-{uuid.uuid4().hex[:8]}"
    created = [
        actions.propose(session_id, p["kind"], p["title"], p["origin_agent"], p["rationale"], p["args"], p["evidence"], p["impact"], plan_id)
        for p in rec["proposals"]
    ]
    return {**rec, "plan_id": plan_id, "actions": created}


class _Ctx:
    def __init__(self, state: dict[str, Any], agent_name: str) -> None:
        self.state, self.agent_name = state, agent_name


def scripted(session_id: str, plan_id: str) -> dict[str, Any]:
    timeline: list[dict[str, Any]] = []
    state: dict[str, Any] = {"session_id": session_id, "plan_id": plan_id, "sources": []}
    pace = iter(range(0, 10**6, 900))

    def call(agent: str, fn: Callable[..., dict[str, Any]], **kw: Any) -> dict[str, Any]:
        timeline.append(
            {
                "t_ms": next(pace),
                "kind": "tool_call",
                "agent": agent,
                "tool": fn.__name__,
                "args": {k: str(v)[:80] for k, v in kw.items() if k != "tool_context"},
            }
        )
        if "tool_context" in inspect.signature(fn).parameters:
            kw["tool_context"] = _Ctx(state, agent)
        r = fn(**kw)
        if isinstance(r, dict) and r.get("source"):
            state["sources"].append(f"{fn.__name__}: {r['source']}")
        timeline.append({"t_ms": next(pace), "kind": "tool_result", "agent": agent, "tool": fn.__name__, "summary": summarize(r), "error": "error" in r})
        return r

    def agent(name: str) -> None:
        timeline.append({"t_ms": next(pace), "kind": "agent_start", "agent": name})

    def end(name: str) -> None:
        timeline.append({"t_ms": next(pace), "kind": "agent_end", "agent": name})

    agent("orchestrator")
    agent("demand_forecast_agent")
    po = demo_po_line()
    timeline.append(
        {
            "t_ms": next(pace),
            "kind": "tool_call",
            "agent": "demand_forecast_agent",
            "tool": "read_customer_po",
            "args": {"document_name": "customer-po-oem-a.pdf"},
        }
    )
    timeline.append(
        {
            "t_ms": next(pace),
            "kind": "tool_result",
            "agent": "demand_forecast_agent",
            "tool": "read_customer_po",
            "summary": f"FB-ARKA {po['previous_qty']:,} -> {po['qty']:,} (+{po['change_pct']}%)",
            "error": False,
        }
    )
    fc = call("demand_forecast_agent", agent_tools.get_forecast, sku="FB-ARKA")
    end("demand_forecast_agent")

    agent("production_planning_agent")
    cap = call("production_planning_agent", agent_tools.get_capacity, apply_customer_po=True)
    g = cap["groups"][0]
    sched = call("production_planning_agent", agent_tools.run_scheduler, apply_customer_po=True, allow_mold_moves=True, maintain_imm04=True)
    call(
        "production_planning_agent",
        agent_tools.propose_mold_move,
        sku="FB-ARKA",
        to_machine_id="IMM-05",
        rationale=f"With FB-ARKA +{po['change_pct']}% the 3,500 t group needs {g['capacity_gap_pct']}% more capacity than it has; "
        f"IMM-04 is the bottleneck at {cap['bottleneck_load_pct']}% load. Running FB-ARKA on the healthier IMM-05 raises its output.",
    )
    end("production_planning_agent")

    agent("material_supply_agent")
    sp = call("material_supply_agent", agent_tools.get_stock_projection, material_id="PP-EPDM-T20", apply_customer_po=True)
    end("material_supply_agent")

    agent("purchasing_agent")
    sc = call("purchasing_agent", agent_tools.get_vendor_scorecard, category="Resin")
    em = VENDOR_EMAIL_FACTS
    timeline.append(
        {
            "t_ms": next(pace),
            "kind": "tool_call",
            "agent": "purchasing_agent",
            "tool": "read_vendor_email",
            "args": {"document_name": "vendor-email-andalas.txt"},
        }
    )
    timeline.append(
        {
            "t_ms": next(pace),
            "kind": "tool_result",
            "agent": "purchasing_agent",
            "tool": "read_vendor_email",
            "summary": f"{em['vendor_name']}: {em['original_date']} -> {em['new_date']} (+{em['delay_days']} days)",
            "error": False,
        }
    )
    cmp_ = call("purchasing_agent", agent_tools.compare_vendors_for_po, material_id="PP-EPDM-T20", need_by=sp["stockout_date"], qty_kg=160_000)
    best = next(o for o in cmp_["options"] if o["vendor_id"] == cmp_["recommended_vendor_id"])
    andalas = next(v for v in sc["vendors"] if v["vendor_id"] == "VEN-ANDALAS")
    call(
        "purchasing_agent",
        agent_tools.draft_po,
        vendor_id=best["vendor_id"],
        material_id="PP-EPDM-T20",
        qty_kg=160_000,
        expected_date=best["expected_arrival"],
        rationale=f"PP-EPDM-T20 runs out on {sp['stockout_date']} ({sp['days_to_stockout']} days). {andalas['vendor_name']} is on time "
        f"{andalas['on_time_pct']}% with {andalas['avg_delay_days']} days average delay and announced another delay; "
        f"{best['vendor_name']} arrives {best['expected_arrival']}.",
    )
    end("purchasing_agent")

    agent("mes_agent")
    mh = call("mes_agent", agent_tools.get_machine_health, machine_id="IMM-04")
    call("mes_agent", agent_tools.twin_observations)
    call(
        "mes_agent",
        agent_tools.propose_maintenance,
        machine_id="IMM-04",
        rationale=f"IMM-04 cycle {mh['current_cycle_s']} s vs ideal {mh['ideal_cycle_s']} s, rising {mh['cycle_trend_s_per_day']} s/day; "
        f"alarm in {mh['days_to_alarm']} days; MTBF fell from {mh['mtbf_hours_first_6_weeks']} h to {mh['mtbf_hours_last_2_weeks']} h.",
    )
    end("mes_agent")

    agent("quality_control_agent")
    dc = call("quality_control_agent", agent_tools.get_defect_correlation, days=45)
    f0 = dc["lot_findings"][0]
    call(
        "quality_control_agent",
        agent_tools.propose_hold,
        resin_lot_id=dc["suspect_lot"],
        rationale=f"{f0['defect_name']} is {f0['lift']}x the plant rate on {dc['suspect_lot']} ({f0['rate_pct']}% vs {f0['overall_rate_pct']}%); "
        f"{dc['hold_candidates']['units_in_stock']} bumpers from this lot are in stock.",
    )
    end("quality_control_agent")

    agent("delivery_agent")
    risk = call("delivery_agent", agent_tools.get_orders_at_risk)
    for o in risk["orders_at_risk"]:
        call(
            "delivery_agent",
            agent_tools.propose_ship_date,
            order_id=o["order_id"],
            new_date=o["estimated_completion"],
            rationale=f"{o['order_qty']} x {o['sku']} short by {o['shortfall_qty']} on {o['requested_delivery_date']} because IMM-04 output is down.",
        )
    ids = [o["order_id"] for o in risk["orders_at_risk"]]
    lines = "\n".join(
        f"- {o['order_id']}: {o['order_qty']} x {o['sku']}, requested {o['requested_delivery_date']}, new date {o['estimated_completion']}"
        for o in risk["orders_at_risk"]
    )
    msg = (
        f"Dear {risk['orders_at_risk'][0]['customer_name']} team,\n\nOur injection molding machine for {risk['orders_at_risk'][0]['sku']} needs preventive "
        f"maintenance this week. To protect quality we are re-sequencing production, which moves these deliveries:\n{lines}\n\n"
        "We have moved the mold to a second machine and are pulling in resin to recover as fast as possible, and will confirm each "
        "call-off 48 hours ahead.\n\nBest regards,\nVidad Delivery Team"
    )
    call("delivery_agent", agent_tools.draft_customer_notice, order_ids=ids, message=msg)
    end("delivery_agent")

    summary = call("orchestrator", agent_tools.plan_summary)
    end("orchestrator")
    tot = summary["total"]
    text = _narrative(po, fc, cap, sched, sp, cmp_, mh, dc, risk, tot)
    return {"timeline": timeline, "text": text}


Facts = dict[str, Any]


def _narrative(po: Facts, fc: Facts, cap: Facts, sched: Facts, sp: Facts, cmp_: Facts, mh: Facts, dc: Facts, risk: Facts, tot: Facts) -> str:
    g = cap["groups"][0]
    return f"""**Situation**
- OEM-A raised FB-ARKA by {po["change_pct"]}% for February; the forecast already shows {fc["change_by_final_week_pct"]}% more FB-ARKA demand in six weeks (Lebaran ramp).
- The 3,500 t group (IMM-04/05, the only machines that fit the ARKA molds) is short by {g["capacity_gap_pct"]}%; IMM-04 is the bottleneck at {cap["bottleneck_load_pct"]}% load and its cycle time is {mh["current_cycle_s"]} s vs {mh["ideal_cycle_s"]} s ideal (breakdown risk {mh["breakdown_risk"]}, alarm in {mh["days_to_alarm"]} days).
- Resin PP-EPDM-T20 runs out on {sp["stockout_date"]} ({sp["days_to_stockout"]} days); the usual vendor is late. Lot {dc["suspect_lot"]} shows a gas mark and bubble lift. {risk["count"]} orders are at risk (penalty exposure Rp{risk["total_penalty_idr"]:,}).

**Actions (priority order)**
1. Preventive maintenance on IMM-04 (2 h stop) — restores the cycle time and removes the breakdown risk.
2. Move the FB-ARKA mold to IMM-05 (swap with RB-ARKA, 90 min changeover) — CP-SAT fill rate with maintenance and mold moves: {sched.get("fill_rate_pct")}%.
3. PO 160,000 kg PP-EPDM-T20 from {cmp_["recommended_vendor"]} (arrives before the stockout).
4. Quality hold on resin lot {dc["suspect_lot"]}.
5. Revise the ship dates of {risk["count"]} orders and send the drafted customer notice.

**Plan total (impact_calculator):** Rp{tot["rupiah_idr"]:,} saved or avoided, {tot["scrap_kg"]:,} kg scrap avoided, {tot["kwh"]:,} kWh and {tot["co2e_kg"]:,} kg CO2e saved per month.

**If nothing is done:** IMM-04 is likely to fail within about a week, molding stops for lack of resin around {sp["stockout_date"]}, and the {risk["count"]} late orders cost Rp{risk["total_penalty_idr"]:,} in penalties.

All actions are pending your approval in the Action Center."""


def record(scenario: str, *, use_scripted: bool) -> Path:
    sid = str(uuid.uuid4())
    twin.reset(sid, scenario)
    plan_id = f"PLAN-{uuid.uuid4().hex[:8]}"
    if use_scripted:
        out, how = scripted(sid, plan_id), "scripted playbook (deterministic tools, templated narrative, no Gemini)"
    else:
        from app.agents.agents import orchestrator
        from app.agents.runner import run

        rec = asyncio.run(run(orchestrator(), DISRUPTION_PROMPT, sid, plan_id, timeout_s=300))
        out, how = {"timeline": rec["timeline"], "text": rec["text"]}, f"live run on Vertex AI ({settings.model_orchestrator} + {settings.model_module})"
    proposals = [
        {k: a[k] for k in ("kind", "title", "origin_agent", "rationale", "evidence", "impact")} | {"args": a["command"]["args"]}
        for a in actions.list_actions(sid)
        if a["plan_id"] == plan_id
    ]
    doc = {
        "scenario": scenario,
        "recorded_with": how,
        "scripted": use_scripted,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "prompt": DISRUPTION_PROMPT,
        **out,
        "proposals": proposals,
    }
    p = path(scenario)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    actions.clear(sid)
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Record a replay")
    ap.add_argument("cmd", choices=["record"])
    ap.add_argument("--scenario", default="disruption_response")
    ap.add_argument("--scripted", action="store_true", help="use the deterministic playbook instead of Gemini")
    a = ap.parse_args()
    sys.stdout.write(f"wrote {record(a.scenario, use_scripted=a.scripted)}\n")
