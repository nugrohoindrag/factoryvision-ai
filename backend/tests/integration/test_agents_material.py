import asyncio
import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import Any, override

import pytest
from app import actions, effects, materials, mrp, readiness
from app.agents import tools as agent_tools
from app.agents.agents import module_agents
from app.agents.runner import TRACE_CALLBACKS, run
from app.agents.tools import MODULE_TOOLS
from app.main import app
from fastapi.testclient import TestClient
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

client = TestClient(app, raise_server_exceptions=False)
RUN_ID = "MRP-2027-02-001"


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
    def __init__(self, sid: str, plan_id: str = "PLAN-m", agent_name: str = "material_supply_agent") -> None:
        self.state: dict[str, Any] = {"session_id": sid, "plan_id": plan_id, "sources": []}
        self.agent_name = agent_name


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _row(material_id: str, net: float, **kw: Any) -> dict[str, Any]:
    return {
        "material_id": material_id,
        "material_name": f"Basecoat {material_id[-3:]}",
        "color": material_id[-3:],
        "unit": "kg",
        "gross_qty": net + 120,
        "available_qty": 120,
        "net_qty": net,
        "required_date": "2027-02-15",
        "order_by_date": "2027-02-09",
        "lead_time_days": 6,
        "default_vendor_id": "VEN-WARNA",
        "recommendation": f"Procure {net} kg before 2027-02-09" if net else None,
        **kw,
    }


def _check(status: str) -> dict[str, Any]:
    short = {
        "material_id": "B-C07",
        "material_name": "Basecoat C07",
        "material_group": "Basecoat",
        "color": "C07",
        "unit": "kg",
        "required_qty": 640.0,
        "required_date": "2027-02-15",
        "available_qty": 220.0,
        "shortage_qty": 420.0,
        "status": "PARTIAL",
        "at_risk": True,
        "note": None,
    }
    ready = {**short, "material_id": "P1", "color": None, "shortage_qty": 0.0, "status": "READY", "at_risk": False}
    return {
        "plan_id": "plan-1",
        "plan_number": "PP-2027-02-001",
        "wo_id": None,
        "status": status,
        "readiness_pct": 50.0,
        "required_date": "2027-02-15",
        "checked_at": "2027-02-01T07:00:00",
        "materials": [short, ready],
        "shortages": [short],
    }


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"runs": [], "check_calls": [], "saved": None}
    rows = [_row("B-C07", 420.0), _row("B-C01", 0.0)]

    def run_mrp(
        sid: str, *, horizon_start: str | None = None, horizon_end: str | None = None, plan_ids: list[str] | None = None, user: str = "You"
    ) -> dict[str, Any]:
        del sid, horizon_start, horizon_end, plan_ids
        r = {
            "run_id": RUN_ID,
            "horizon_start": "2027-02-01",
            "horizon_end": "2027-03-03",
            "plan_ids": [],
            "created_at": "2027-02-01T07:00:00",
            "created_by": user,
            "total_materials": 2,
            "shortage_materials": 1,
            "results": rows,
        }
        state["runs"].append(r)
        return r

    def get_run(_sid: str, run_id: str) -> dict[str, Any]:
        return next(r for r in state["runs"] if r["run_id"] == run_id)

    def check_plan(_sid: str, plan_id: str, *, save: bool = True) -> dict[str, Any]:
        state["check_calls"].append({"plan_id": plan_id, "save": save})
        return _check("PARTIAL")

    def get_material(material_id: str) -> dict[str, Any]:
        return {"material_id": material_id, "material_name": f"Basecoat {material_id[-3:]}", "unit": "kg", "unit_cost_idr": 165_000, "lead_time_days": 6}

    monkeypatch.setattr(mrp, "run_mrp", run_mrp)
    monkeypatch.setattr(mrp, "get_run", get_run)
    monkeypatch.setattr(mrp, "latest_run", lambda _sid: state["runs"][-1] if state["runs"] else None)
    monkeypatch.setattr(readiness, "check_plan", check_plan)
    monkeypatch.setattr(
        readiness, "latest_check", lambda _sid, pid: state["saved"] or {"plan_id": pid, "status": "NOT_CHECKED", "materials": [], "shortages": []}
    )
    monkeypatch.setattr(materials, "get_material", get_material)
    return state


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    client.post("/api/demo/reset", headers=h(s), json={"scenario": "baseline"})
    yield s
    actions.clear(s)


def _propose(sid: str, material_id: str = "B-C07") -> Any:
    return client.post(f"/api/mrp/runs/{RUN_ID}/propose-po", headers=h(sid), json={"material_id": material_id})


def test_material_tools_are_wired() -> None:
    names = {m: {f.__name__ for f in fns} for m, fns in MODULE_TOOLS.items()}
    assert {"get_material_readiness", "get_plan_material_check", "get_latest_mrp"} <= names["material"]
    assert {"get_latest_mrp", "propose_po_from_shortage", "draft_po"} <= names["purchasing"]
    assert "check_plan_material" in names["planning"]
    assert not {"propose_po_from_shortage", "draft_po"} & names["planning"]
    agents = module_agents()
    assert "get_latest_mrp" in str(agents["material"].instruction)
    assert "colour" in str(agents["material"].instruction)
    assert "propose_po_from_shortage" in str(agents["purchasing"].instruction)
    assert "check_plan_material" in str(agents["planning"].instruction)


def test_propose_po_creates_one_pending_action(fake: dict[str, Any], sid: str) -> None:
    mrp.run_mrp(sid)
    r = _propose(sid)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["status"] == "pending"
    assert a["kind"] == "purchase_order"
    assert a["command"]["name"] == "place_po"
    args = a["command"]["args"]
    assert args == {"vendor_id": "VEN-WARNA", "material_id": "B-C07", "qty": 420.0, "expected_date": "2027-02-15", "unit": "kg", "mrp_run_id": RUN_ID}
    assert RUN_ID in a["rationale"]
    assert "Rp69,300,000" in a["rationale"]
    assert any(e.get("mrp_result", {}).get("net_qty") == 420.0 for e in a["evidence"])
    again = _propose(sid).json()
    assert again["action_id"] == a["action_id"]
    assert len(actions.list_actions(sid, "pending")) == 1
    assert effects.overrides(sid).extra_receipts == []


def test_propose_po_errors(fake: dict[str, Any], sid: str) -> None:
    mrp.run_mrp(sid)
    assert _propose(sid, "NOPE").json()["error"]["code"] == "FV-AGT-010"
    r = _propose(sid, "B-C01")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "FV-AGT-011"
    fake["runs"][-1]["results"].append(_row("B-C02", 10.0, default_vendor_id=None))
    assert _propose(sid, "B-C02").json()["error"]["code"] == "FV-AGT-012"
    assert actions.list_actions(sid) == []


def test_approve_adds_an_incoming_receipt_for_a_non_resin_material(fake: dict[str, Any], sid: str) -> None:
    mrp.run_mrp(sid)
    a = _propose(sid).json()
    d = client.post(f"/api/actions/{a['action_id']}/approve", headers=h(sid))
    assert d.status_code == 200, d.text
    out = d.json()
    assert out["status"] == "approved"
    assert out["execution"]["result"]["po_id"] == a["action_id"]
    receipts = effects.overrides(sid).extra_receipts
    assert receipts == [{"po_id": a["action_id"], "expected_date": "2027-02-15", "qty": 420.0, "vendor_id": "VEN-WARNA", "material_id": "B-C07"}]
    snap = client.post("/api/twin/tick", headers=h(sid), json={"minutes": 1}).json()
    assert snap["resin"]["incoming"] == []
    assert _propose(sid).json()["action_id"] != a["action_id"]


def test_reject_executes_nothing(fake: dict[str, Any], sid: str) -> None:
    mrp.run_mrp(sid)
    a = _propose(sid).json()
    out = client.post(f"/api/actions/{a['action_id']}/reject", headers=h(sid)).json()
    assert out["status"] == "rejected"
    assert out["execution"] is None
    assert effects.overrides(sid).extra_receipts == []


def test_readiness_and_check_tools(fake: dict[str, Any], sid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    def fake_readiness(s: str, date_from: str | None = None, date_to: str | None = None, only_problems: bool = False) -> dict[str, Any]:  # noqa: FBT001, FBT002
        seen.append((s, date_from, date_to, only_problems))
        plan = {"plan_id": "OEM-2027-02", "plan_number": "OEM monthly plan 2027-02", "status": "PARTIAL", "readiness_pct": 80.0, "short_count": 1}
        return {
            "from": "2027-02-01",
            "to": "2027-02-28",
            "kpis": {"readiness_pct": 80.0, "plans_monitored": 1, "plans_short": 1, "materials_short": 1},
            "plans": [plan],
        }

    monkeypatch.setattr(readiness, "readiness", fake_readiness)
    r = agent_tools.get_material_readiness(date_from="", date_to="2027-02-28", tool_context=Ctx(sid))
    assert seen == [(sid, None, "2027-02-28", False)]
    assert r["kpis"]["plans_short"] == 1
    assert r["plans"][0]["plan_id"] == "OEM-2027-02"
    assert r["source"]

    c = agent_tools.get_plan_material_check(plan_id="plan-1", tool_context=Ctx(sid))
    assert fake["check_calls"] == [{"plan_id": "plan-1", "save": False}]
    assert c["saved_check"] is False
    assert c["status"] == "PARTIAL"
    assert c["shortages"][0]["color"] == "C07"
    assert c["shortages"][0]["shortage_qty"] == 420.0
    assert c["ready_count"] == 1
    assert c["at_risk"] == ["B-C07"]

    fake["saved"] = _check("SHORTAGE")
    c2 = agent_tools.get_plan_material_check(plan_id="plan-1", tool_context=Ctx(sid))
    assert c2["saved_check"] is True
    assert c2["status"] == "SHORTAGE"
    assert len(fake["check_calls"]) == 1

    p = agent_tools.check_plan_material(plan_id="plan-1", tool_context=Ctx(sid, agent_name="production_planning_agent"))
    assert fake["check_calls"][-1] == {"plan_id": "plan-1", "save": False}
    assert p["status"] == "PARTIAL"
    assert actions.list_actions(sid) == []


def test_latest_mrp_computes_once_then_reads(fake: dict[str, Any], sid: str) -> None:
    first = agent_tools.get_latest_mrp(tool_context=Ctx(sid))
    assert first["computed_now"] is True
    assert first["created_by"] == "material_supply_agent"
    assert [r["material_id"] for r in first["shortages"]] == ["B-C07"]
    second = agent_tools.get_latest_mrp(tool_context=Ctx(sid))
    assert second["computed_now"] is False
    assert second["run_id"] == RUN_ID
    assert len(fake["runs"]) == 1


def test_purchasing_agent_proposes_po_from_shortage(fake: dict[str, Any], sid: str) -> None:
    calls = [("get_latest_mrp", {}), ("propose_po_from_shortage", {"material_id": "B-C07"})]
    buyer = LlmAgent(
        name="purchasing_agent",
        model=ScriptedLlm(model="fake", calls=calls, final="B-C07 PO pending approval."),
        tools=list(MODULE_TOOLS["purchasing"]),
        **TRACE_CALLBACKS,
    )
    rec = asyncio.run(run(buyer, "shortages?", sid, "PLAN-p1", timeout_s=60))
    assert [e["tool"] for e in rec["timeline"] if e["kind"] == "tool_call"] == ["get_latest_mrp", "propose_po_from_shortage"]
    acts = actions.list_actions(sid)
    assert len(acts) == 1
    assert acts[0]["plan_id"] == "PLAN-p1"
    assert acts[0]["command"]["args"]["qty"] == 420.0
    assert acts[0]["status"] == "pending"
    again = agent_tools.propose_po_from_shortage(material_id="B-C07", tool_context=Ctx(sid, "PLAN-p1", "purchasing_agent"))
    assert again["action_id"] == acts[0]["action_id"]
    assert again["note"]
    assert agent_tools.propose_po_from_shortage(material_id="B-C01", tool_context=Ctx(sid))["error"]["code"] == "FV-AGT-011"


def test_planning_agent_checks_material_before_approve(fake: dict[str, Any], sid: str) -> None:
    calls = [("check_plan_material", {"plan_id": "plan-1"})]
    planner = LlmAgent(
        name="production_planning_agent",
        model=ScriptedLlm(model="fake", calls=calls, final="B-C07 is short by 420 kg on 2027-02-15."),
        tools=list(MODULE_TOOLS["planning"]),
        **TRACE_CALLBACKS,
    )
    rec = asyncio.run(run(planner, "material impact of plan-1?", sid, "PLAN-x", timeout_s=60))
    assert [e["tool"] for e in rec["timeline"] if e["kind"] == "tool_call"] == ["check_plan_material"]
    assert fake["check_calls"] == [{"plan_id": "plan-1", "save": False}]
    assert actions.list_actions(sid) == []


def test_resin_po_from_mrp_keeps_the_resin_impact(fake: dict[str, Any], sid: str) -> None:
    mrp.run_mrp(sid)
    fake["runs"][-1]["results"].append(_row("PP-EPDM-T20", 160_000.0, color=None, default_vendor_id="VEN-MERAPI", lead_time_days=5))
    a = _propose(sid, "PP-EPDM-T20").json()
    assert a["command"]["args"]["vendor_id"] == "VEN-MERAPI"
    assert set(a["impact"]) == {"rupiah_idr", "scrap_kg", "kwh", "co2e_kg"}
    assert a["impact"]["rupiah_idr"] is not None
