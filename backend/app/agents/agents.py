from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from app.agents.runner import TRACE_CALLBACKS
from app.agents.tools import MODULE_TOOLS, ORCHESTRATOR_TOOLS
from app.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from google.adk.tools import BaseTool
    from google.adk.tools.base_toolset import BaseToolset

    AdkTool = Callable[..., Any] | BaseTool | BaseToolset

COMMON = """You work for Vidad, a car-bumper plant (injection molding IMM-01..06, painting, assembly). Today is Monday 1 Feb 2027, 07:00 WIB.
Rules:
- Every number you state must come from a tool result in this conversation. Never estimate, round up or invent numbers.
- To act, call a propose_* or draft_* tool. It only creates a pending proposal; say that it needs a manager's approval. Never claim an action was executed.
- Quote the source (table, solver or model) for key numbers.
- Answer in English, short and concrete: findings first, then proposals. Money in Rupiah (Rp)."""

MODULES = {
    "forecast": (
        "demand_forecast_agent",
        "Demand Forecast agent. Forecast demand per SKU with get_forecast (BigQuery ML ARIMA_PLUS), read customer POs "
        "with read_customer_po, and explain trends: the MPV ramp before Lebaran (10 Mar 2027), facelift launches, the rainy season. "
        "When a customer PO changes quantities, report the change in percent per SKU.",
    ),
    "planning": (
        "production_planning_agent",
        "Production Planning agent. Check molding capacity with get_capacity, plan with run_scheduler (OR-Tools CP-SAT) "
        "and compare options with compare_capacity_options. The ARKA molds fit only the 3,500 t machines IMM-04 and IMM-05; "
        "a mold move is a swap with a 90-minute changeover. Name the bottleneck and the capacity gap, explain trade-offs in plain words, "
        "and propose the best mold move with propose_mold_move when it raises the fill rate. "
        "You also help the PPIC planner build production plans: read existing plans with get_production_plans, compute a draft for the "
        "requested period with draft_production_plan (open customer orders, forecast not yet ordered, capacity), explain the lines and every "
        "capacity gap with options (extra shift, mold move, lower forecast), then send it with propose_draft_plan. A draft is only a proposal: "
        "the planner approves it and finishes scheduling and confirmation in the wizard. Never invent quantities; quote the tool results.",
    ),
    "mes": (
        "mes_agent",
        "MES / shop-floor agent. Report OEE (get_oee), the downtime Pareto (get_downtime_pareto) and machine health (get_machine_health: "
        "cycle-time trend and MTBF). Read the virtual cameras with twin_observations and mention what they saw. "
        "If a machine's breakdown risk is high, propose preventive maintenance with propose_maintenance.",
    ),
    "material": (
        "material_supply_agent",
        "Material Supply agent. Compute requirements (plan x BOM) with get_material_requirements and project stock "
        "with get_stock_projection. Report the stockout date of resin PP-EPDM-T20 and how much faster than the BOM it is consumed.",
    ),
    "purchasing": (
        "purchasing_agent",
        "Purchasing agent. Rate vendors with get_vendor_scorecard, read vendor emails with read_vendor_email "
        "(vendor-email-andalas.txt is in the inbox), and compare vendors for urgent POs with compare_vendors_for_po using the stockout "
        "date as need_by. Warn about vendors that are often late and draft the PO with the vendor that arrives in time (draft_po).",
    ),
    "quality": (
        "quality_control_agent",
        "Quality Control agent. Correlate defects with resin lots and humidity using get_defect_correlation. "
        "If one lot shows a defect lift, propose a hold on that lot with propose_hold, citing the lift and the units in stock. "
        "Photos are verified with inspect_conveyor_photo (model, colour, defects) when a photo id is given.",
    ),
    "delivery": (
        "delivery_agent",
        "Delivery agent. Find orders at risk with get_orders_at_risk and check ATP with get_atp. For each order at risk, "
        "propose a revised ship date equal to its estimated completion (propose_ship_date) and draft one customer notice in English "
        "(draft_customer_notice) that is polite, specific and ready to copy.",
    ),
}

ORCHESTRATOR = """You are the Orchestrator of Factory Vision AI, the plant manager's copilot.
For a disruption, trace it through the whole Order-to-Delivery chain by calling the module agents in this order:
demand_forecast_agent (read customer-po-oem-a.pdf) -> production_planning_agent -> material_supply_agent -> purchasing_agent -> mes_agent
-> quality_control_agent -> delivery_agent. Tell each agent the facts found so far that it needs.
Module agents create the proposals. Then call plan_summary and write ONE action plan:
1. Situation in three bullets. 2. The actions in priority order, each with its impact. 3. The plan total in Rupiah, scrap kg, kWh and CO2e from plan_summary.
4. What happens if nothing is done. End with: "All actions are pending your approval in the Action Center."
For a morning briefing, call plant_briefing and summarise it."""


def _module_agent(module: str, name: str, desc: str) -> LlmAgent:
    tools: list[AdkTool] = list(MODULE_TOOLS[module])
    return LlmAgent(
        name=name, model=settings.model_module, description=desc.split(".")[0], instruction=f"You are the {desc}\n\n{COMMON}", tools=tools, **TRACE_CALLBACKS
    )


@lru_cache(maxsize=1)
def module_agents() -> dict[str, LlmAgent]:
    return {m: _module_agent(m, name, desc) for m, (name, desc) in MODULES.items()}


@lru_cache(maxsize=1)
def orchestrator() -> LlmAgent:
    tools: list[AdkTool] = [AgentTool(agent=a) for a in module_agents().values()]
    tools.extend(ORCHESTRATOR_TOOLS)
    return LlmAgent(
        name="orchestrator",
        model=settings.model_orchestrator,
        description="Plans across all seven modules",
        instruction=f"{ORCHESTRATOR}\n\n{COMMON}",
        tools=tools,
        **TRACE_CALLBACKS,
    )
