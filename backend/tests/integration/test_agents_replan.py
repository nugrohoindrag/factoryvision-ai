import asyncio
import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast, override

import pytest
from app import actions, effects, forecasts, plans
from app.agents import tools as agent_tools
from app.agents.agents import module_agents
from app.agents.runner import TRACE_CALLBACKS, run
from app.agents.tools import MODULE_TOOLS
from app.simulation import commands
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

if TYPE_CHECKING:
    from app.simulation.engine import State

SNAP_ID = "FC-2027-02-002"


class ScriptedLlm(BaseLlm):
    calls: list[tuple[str, dict[str, Any]]] = Field(default_factory=list)
    final: str = "done"

    @override
    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        done = sum(1 for c in llm_request.contents for p in (c.parts or []) if p.function_response)
        if done < len(self.calls):
            name, args = self.calls[done]
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=self.final)]))


class Ctx:
    def __init__(self, sid: str, plan_id: str = "PLAN-t") -> None:
        self.state: dict[str, Any] = {"session_id": sid, "plan_id": plan_id, "sources": []}


def _snapshot() -> dict[str, Any]:
    weeks = ["2027-02-01", "2027-02-08", "2027-02-15", "2027-02-22", "2027-03-01", "2027-03-08"]
    lines = [
        {
            "forecast_line_id": f"{SNAP_ID}-FB-ARKA-{w}",
            "sku": "FB-ARKA",
            "customer_id": None,
            "week_start": w,
            "forecast_qty": 5000 + 300 * k,
            "history_weeks_with_orders": 13,
            "history_weeks": 13,
            "insufficient_history": False,
        }
        for k, w in enumerate(weeks)
    ]
    return {
        "forecast_id": SNAP_ID,
        "status": "Generated",
        "engine": "local trend+weekday (dev)",
        "skus": ["FB-ARKA"],
        "lines": lines,
        "history": {"FB-ARKA": [{"week_start": "2027-01-25", "qty": 4800}]},
        "used_by_plans": [],
    }


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"plans": [], "replan_calls": []}
    snap = _snapshot()

    def get_snapshot(_sid: str, forecast_id: str) -> dict[str, Any]:
        assert forecast_id == SNAP_ID
        return snap

    def replan(
        sid: str, *, reason: str, forecast_id: str | None = None, demand_change_pct: dict[str, float] | None = None, user: str = "agent"
    ) -> dict[str, Any]:
        state["replan_calls"].append({"sid": sid, "reason": reason, "forecast_id": forecast_id, "demand_change_pct": demand_change_pct, "user": user})
        delta = {"FB-ARKA": {"before_gap_pct": -4.5, "after_gap_pct": 18.3}}
        return {"plan_id": "plan-new", "plan_number": "PP-2027-02-002", "status": "Draft", "forecast_id": forecast_id, "capacity_delta": delta}

    monkeypatch.setattr(forecasts, "latest", lambda _sid: snap)
    monkeypatch.setattr(forecasts, "get_snapshot", get_snapshot)
    monkeypatch.setattr(forecasts, "list_snapshots", lambda _sid, _status=None: [snap])
    monkeypatch.setattr(plans, "list_plans", lambda _sid, _status=None: state["plans"])
    monkeypatch.setattr(plans, "get_plan", lambda _sid, pid: next(p for p in state["plans"] if p["plan_id"] == pid))
    monkeypatch.setattr(plans, "replan", replan)
    return state


@pytest.fixture
def sid() -> Any:
    s = str(uuid.uuid4())
    effects.set_scenario(s, "disruption_response")
    yield s
    actions.clear(s)


def test_new_tools_are_wired_to_the_right_agents() -> None:
    names = {m: {f.__name__ for f in fns} for m, fns in MODULE_TOOLS.items()}
    assert {"get_latest_forecast_snapshot", "get_forecast_snapshot", "compare_forecast_to_actuals", "get_forecast_accuracy"} <= names["forecast"]
    assert {"list_production_plans", "get_production_plan", "check_replan_trigger", "propose_replan"} <= names["planning"]
    agents = module_agents()
    assert "snapshot" in str(agents["forecast"].instruction)
    assert "IMM-04" in str(agents["planning"].instruction)
    assert "propose_replan" in str(agents["planning"].instruction)


def test_forecast_snapshot_tools(fake: dict[str, Any], sid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    del fake
    r = agent_tools.get_latest_forecast_snapshot(sku="FB-ARKA", tool_context=Ctx(sid))
    assert r["forecast_id"] == SNAP_ID
    assert r["skus"]["FB-ARKA"]["change_final_week_vs_last_history_week_pct"] == 35.4
    assert r["insufficient_history_skus"] == []
    assert agent_tools.get_forecast_snapshot(forecast_id=SNAP_ID, sku="", tool_context=Ctx(sid))["forecast_id"] == SNAP_ID
    assert agent_tools.get_latest_forecast_snapshot(sku="NOPE", tool_context=Ctx(sid))["error"]["code"] == "FV-DATA-001"
    monkeypatch.setattr(forecasts, "comparison", lambda _sid, fid: {"forecast_id": fid, "weeks": []}, raising=False)
    monkeypatch.setattr(forecasts, "accuracy", lambda _sid, sku: {"sku": sku, "mape_pct": 12.5}, raising=False)
    assert agent_tools.compare_forecast_to_actuals(forecast_id=SNAP_ID, tool_context=Ctx(sid))["forecast_id"] == SNAP_ID
    assert agent_tools.get_forecast_accuracy(sku="FB-ARKA", tool_context=Ctx(sid))["mape_pct"] == 12.5


def test_plan_tools_report_capacity_status(fake: dict[str, Any], sid: str) -> None:
    assert agent_tools.get_production_plan(plan_id="", tool_context=Ctx(sid))["plan"] is None
    fake["plans"].append(
        {
            "plan_id": "p1",
            "plan_number": "PP-2027-02",
            "status": "Draft",
            "forecast_id": "FC-2027-02-001",
            "lines": [{"sku": "FB-ARKA", "capacity": {"status": "Capacity Up Required", "gap": 3000, "contributions": [{"machine_id": "IMM-04"}]}}],
        }
    )
    listed = agent_tools.list_production_plans(status="", tool_context=Ctx(sid))
    assert listed["count"] == 1
    assert listed["plans"][0]["capacity_status"] == "Capacity Up Required"
    detail = agent_tools.get_production_plan(plan_id="", tool_context=Ctx(sid))
    assert detail["capacity_up_required_skus"] == ["FB-ARKA"]
    assert detail["lines"][0]["machines"] == ["IMM-04"]


def test_trigger_names_new_snapshot_and_po_change(fake: dict[str, Any], sid: str) -> None:
    fake["plans"].append({"plan_id": "p1", "plan_number": "PP-2027-02", "status": "Draft", "forecast_id": "FC-2027-02-001", "lines": []})
    t = agent_tools.check_replan_trigger(tool_context=Ctx(sid))
    assert t["replan_needed"] is True
    assert any(SNAP_ID in r and "FC-2027-02-001" in r for r in t["reasons"])
    assert any("FB-ARKA +40%" in r for r in t["reasons"])
    d = t["molding_capacity_delta"]
    assert d["before_gap_pct"] < 0 < d["after_gap_pct"]
    assert d["after_status"] == "Capacity Up Required"
    assert d["bottleneck_machine"] == "IMM-04"


def _agent_proposes_replan(sid: str, plan_id: str) -> dict[str, Any]:
    calls = [("check_replan_trigger", {}), ("propose_replan", {"reason": "New snapshot and OEM-A +40%; molding gap rises."})]
    planner = LlmAgent(
        name="production_planning_agent",
        model=ScriptedLlm(model="fake", calls=calls, final="IMM-04 is the bottleneck. Re-plan pending approval."),
        tools=list(MODULE_TOOLS["planning"]),
        **TRACE_CALLBACKS,
    )
    rec = asyncio.run(run(planner, "re-plan?", sid, plan_id, timeout_s=60))
    tools = [e["tool"] for e in rec["timeline"] if e["kind"] == "tool_call"]
    assert tools == ["check_replan_trigger", "propose_replan"]
    acts = [a for a in actions.list_actions(sid) if a["plan_id"] == plan_id]
    assert len(acts) == 1
    return acts[0]


def test_propose_replan_creates_pending_action_and_nothing_runs(fake: dict[str, Any], sid: str) -> None:
    a = _agent_proposes_replan(sid, "PLAN-r1")
    assert a["status"] == "pending"
    assert a["kind"] == "replan"
    assert a["module"] == "planning"
    assert a["command"]["name"] == "create_replan"
    assert a["command"]["args"]["forecast_id"] == SNAP_ID
    assert a["command"]["args"]["demand_change_pct"] == {"FB-ARKA": 40}
    assert any("capacity_delta" in e for e in a["evidence"])
    assert any("-4.5% -> +18.3%" in e["source"] for e in a["evidence"])
    assert fake["replan_calls"] == []
    again = agent_tools.propose_replan(reason="again", tool_context=Ctx(sid, "PLAN-r1"))
    assert again["action_id"] == a["action_id"]
    assert len(actions.list_actions(sid, "pending")) == 1


def test_approve_calls_replan_once_with_the_approver(fake: dict[str, Any], sid: str) -> None:
    a = _agent_proposes_replan(sid, "PLAN-r2")
    out = actions.decide(sid, a["action_id"], approve=True, user="manager@vidad.example")
    assert out["status"] == "approved"
    assert out["execution"]["result"]["plan_number"] == "PP-2027-02-002"
    assert fake["replan_calls"] == [
        {
            "sid": sid,
            "reason": "New snapshot and OEM-A +40%; molding gap rises.",
            "forecast_id": SNAP_ID,
            "demand_change_pct": {"FB-ARKA": 40},
            "user": "manager@vidad.example",
        }
    ]
    replay_cmd = {**a["command"], "action_id": a["action_id"], "decided_by": "manager@vidad.example"}
    assert commands.execute(cast("State", SimpleNamespace(session_id=sid)), replay_cmd)["plan_number"] == "PP-2027-02-002"
    assert len(fake["replan_calls"]) == 1
    assert agent_tools.check_replan_trigger(tool_context=Ctx(sid))["replan_needed"] is False


def test_reject_runs_nothing(fake: dict[str, Any], sid: str) -> None:
    a = _agent_proposes_replan(sid, "PLAN-r3")
    out = actions.decide(sid, a["action_id"], approve=False, user="manager")
    assert out["status"] == "rejected"
    assert fake["replan_calls"] == []


def test_no_replan_when_plan_is_current(fake: dict[str, Any]) -> None:
    s = str(uuid.uuid4())
    fake["plans"].append({"plan_id": "p1", "plan_number": "PP-2027-02", "status": "Draft", "forecast_id": SNAP_ID, "lines": []})
    r = agent_tools.propose_replan(reason="x", tool_context=Ctx(s))
    assert r["error"]["code"] == "FV-AGT-002"
    assert actions.list_actions(s) == []


def test_trace_survives_a_plain_string_tool_error() -> None:
    from app.agents.runner import after_tool

    tool = SimpleNamespace(name="get_forecast_snapshot")
    ctx = SimpleNamespace(state={"run_id": None}, agent_name="demand_forecast_agent")
    assert after_tool(tool, {}, ctx, {"error": "mandatory input parameters are not present"}) is None  # type: ignore[arg-type]
