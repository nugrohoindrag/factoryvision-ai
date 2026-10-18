import asyncio
import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import Any, override

import pytest
from app import actions, effects, mrp, purchasing_docs
from app.agents import tools as agent_tools
from app.agents.agents import module_agents
from app.agents.runner import TRACE_CALLBACKS, run
from app.agents.tools import MODULE_TOOLS
from app.errors import FVError
from app.main import app
from app.simulation import commands
from app.simulation.engine import State
from fastapi.testclient import TestClient
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

client = TestClient(app, raise_server_exceptions=False)
RUN_ID = "MRP-2027-02-001"
PO_ID = "PO-2702-S-001"
SEED_PO = "PO-2702-030"
APPROVER = "buyer.lead@vidad.example"


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
    def __init__(self, sid: str, plan_id: str = "PLAN-pu") -> None:
        self.state: dict[str, Any] = {"session_id": sid, "plan_id": plan_id, "sources": []}
        self.agent_name = "purchasing_agent"


def h(s: str) -> dict[str, str]:
    return {"X-Session-Id": s}


def _row(material_id: str, net: float) -> dict[str, Any]:
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
    }


def _options(material_id: str, qty: float, need_by: str | None) -> dict[str, Any]:
    opts: list[dict[str, Any]] = [
        {"vendor_id": "VEN-ANDALAS", "vendor_name": "PT Andalas", "expected_arrival": "2027-02-19", "unit_price_idr": 100, "avg_delay_days": 6.2},
        {"vendor_id": "VEN-MERAPI", "vendor_name": "PT Merapi", "expected_arrival": "2027-02-10", "unit_price_idr": 120, "avg_delay_days": 0.4},
        {"vendor_id": "VEN-QUOTE", "vendor_name": "PT Quote", "expected_arrival": "2027-02-08", "unit_price_idr": 0, "avg_delay_days": 0},
    ]
    for o in opts:
        o["po_value_idr"] = int(o["unit_price_idr"] * qty)
        o["arrives_before_need"] = o["expected_arrival"] < need_by if need_by else None
    return {"material_id": material_id, "qty": qty, "need_by": need_by, "options": opts, "recommended_vendor_id": "VEN-MERAPI"}


def _po(po_id: str, **kw: Any) -> dict[str, Any]:
    line = {"line_id": "L1", "material_id": "PP-EPDM-T20", "unit": "kg", "qty": 50_000.0, "unit_price_idr": 120, "value_idr": 6_000_000}
    return {
        "po_id": po_id,
        "origin": "session",
        "vendor_id": "VEN-ANDALAS",
        "vendor_name": "PT Andalas",
        "status": "Issued",
        "order_date": "2027-01-25",
        "promised_date": "2027-02-12",
        "expected_date": "2027-02-18",
        "pr_id": None,
        "total_value_idr": 6_000_000,
        "received_pct": 0.0,
        "overdue": False,
        "days_late": 0,
        "lines": [{**line, "received_qty": 0.0, "open_qty": 50_000.0}],
        "receipts": [],
        "history": [{"at": "2027-01-25T07:00:00", "by": "user", "action": "issue", "from": "Draft", "to": "Issued", "reason": None}],
        **kw,
    }


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, list[Any]] = {"create_requisition": [], "transition_requisition": [], "create_po": [], "expedite_po": [], "list_pos": []}
    run_doc = {"run_id": RUN_ID, "created_at": "2027-02-01T07:00:00", "results": [_row("B-C07", 420.0), _row("B-C01", 0.0)]}
    pos = {PO_ID: _po(PO_ID), SEED_PO: _po(SEED_PO, origin="seed"), "PO-2702-S-002": _po("PO-2702-S-002", status="Received")}

    def create_requisition(sid: str, **kw: Any) -> dict[str, Any]:
        calls["create_requisition"].append({"sid": sid, **kw})
        return {"pr_id": "PR-2702-001", "status": "Draft", "lines": []}

    def transition_requisition(sid: str, pr_id: str, action: str, *, user: str, reason: str | None = None) -> dict[str, Any]:
        calls["transition_requisition"].append((sid, pr_id, action, user, reason))
        line = {"material_id": "B-C07", "qty": 420.0, "need_by": "2027-02-15", "suggested_vendor_id": "VEN-WARNA", "duplicate_of": None}
        return {"pr_id": pr_id, "status": {"submit": "Submitted", "approve": "Approved"}[action], "lines": [line]}

    def create_po(sid: str, **kw: Any) -> dict[str, Any]:
        calls["create_po"].append({"sid": sid, **kw})
        return _po(f"PO-2702-S-{100 + len(calls['create_po'])}", vendor_id=kw["vendor_id"], status="Issued" if kw.get("issue") else "Draft")

    def get_po(_sid: str, po_id: str) -> dict[str, Any]:
        if po_id not in pos:
            raise FVError("FV-DATA-001", f"unknown purchase order {po_id}")
        return pos[po_id]

    def expedite_po(sid: str, po_id: str, new_promised_date: str, reason: str, *, user: str) -> dict[str, Any]:
        calls["expedite_po"].append((sid, po_id, new_promised_date, reason, user))
        return {**pos[po_id], "promised_date": new_promised_date, "expected_date": new_promised_date}

    def list_pos(
        sid: str, status: str | None = None, vendor_id: str | None = None, material_id: str | None = None, *, overdue: bool | None = None
    ) -> dict[str, Any]:
        calls["list_pos"].append((sid, status, vendor_id, material_id, overdue))
        rows = [p for p in pos.values() if (not status or p["status"] == status)]
        return {"orders": [{**p, "overdue": True, "days_late": 3} for p in rows] if overdue else rows}

    def list_requisitions(_sid: str, status: str | None = None) -> dict[str, Any]:
        pr = {"pr_id": "PR-2702-001", "status": "Submitted", "created_by": "user", "history": [1, 2], "lines": [{"material_id": "B-C07", "qty": 420.0}]}
        return {"requisitions": [pr] if status in (None, "Submitted") else []}

    monkeypatch.setattr(mrp, "get_run", lambda _sid, rid: run_doc if rid == RUN_ID else (_ for _ in ()).throw(FVError("FV-DATA-001")))
    monkeypatch.setattr(mrp, "latest_run", lambda _sid: run_doc)
    monkeypatch.setattr(purchasing_docs, "recommend", _options)
    for name, fn in [
        ("create_requisition", create_requisition),
        ("transition_requisition", transition_requisition),
        ("create_po", create_po),
        ("get_po", get_po),
        ("expedite_po", expedite_po),
        ("list_pos", list_pos),
        ("list_requisitions", list_requisitions),
    ]:
        monkeypatch.setattr(purchasing_docs, name, fn)
    return {"calls": calls, "run": run_doc, "pos": pos}


@pytest.fixture
def sid() -> Iterator[str]:
    s = str(uuid.uuid4())
    client.post("/api/demo/reset", headers=h(s), json={"scenario": "baseline"})
    yield s
    actions.clear(s)


def _agent(sid: str, calls: list[tuple[str, dict[str, Any]]], plan_id: str) -> list[str]:
    buyer = LlmAgent(
        name="purchasing_agent",
        model=ScriptedLlm(model="fake", calls=calls, final="Pending approval in the Action Center."),
        tools=list(MODULE_TOOLS["purchasing"]),
        **TRACE_CALLBACKS,
    )
    rec = asyncio.run(run(buyer, "what should we buy?", sid, plan_id, timeout_s=60))
    return [e["tool"] for e in rec["timeline"] if e["kind"] == "tool_call"]


def _replay(a: dict[str, Any]) -> dict[str, Any]:
    cmd = {**a["command"], "action_id": a["action_id"], "decided_by": APPROVER}
    return commands.execute(State.new(a["session_id"]), cmd)


def test_purchasing_tools_and_instruction_are_wired() -> None:
    names = {f.__name__ for f in MODULE_TOOLS["purchasing"]}
    assert {
        "list_purchase_orders",
        "get_purchase_order",
        "list_overdue_purchase_orders",
        "recommend_vendor",
        "list_requisitions",
        "propose_requisition_from_mrp",
        "propose_purchase_order",
        "propose_expedite",
        "propose_po_from_shortage",
    } <= names
    other = {f.__name__ for m, fns in MODULE_TOOLS.items() if m != "purchasing" for f in fns}
    assert not {"propose_requisition_from_mrp", "propose_purchase_order", "propose_expedite"} & other
    text = str(module_agents()["purchasing"].instruction)
    for phrase in ("premium vs the cheapest", "expected arrival against the need date", "never make numbers up", "pending approval in the Action Center"):
        assert phrase in text
    assert actions.KINDS["purchase_requisition"] == ("purchasing", "create_requisition")


def test_read_tools_trim_and_cite(fake: dict[str, Any], sid: str) -> None:
    ctx = Ctx(sid)
    listed = agent_tools.list_purchase_orders(status="Issued", tool_context=ctx)
    assert listed["count"] == 2
    assert {o["po_id"] for o in listed["orders"]} == {PO_ID, SEED_PO}
    assert "history" not in listed["orders"][0]
    assert listed["orders"][0]["lines"][0]["open_qty"] == 50_000.0
    assert listed["source"]
    late = agent_tools.list_overdue_purchase_orders(tool_context=ctx)
    assert fake["calls"]["list_pos"][-1] == (sid, None, None, None, True)
    assert all(o["overdue"] for o in late["orders"])
    one = agent_tools.get_purchase_order(po_id=PO_ID, tool_context=ctx)
    assert one["history"][0]["to"] == "Issued"
    assert agent_tools.get_purchase_order(po_id="NOPE", tool_context=ctx)["error"]["code"] == "FV-DATA-001"
    rec = agent_tools.recommend_vendor(material_id="PP-EPDM-T20", qty=1000, need_by="2027-02-15")
    assert rec["recommended_vendor_id"] == "VEN-MERAPI"
    assert rec["cheapest_vendor_id"] == "VEN-ANDALAS"
    assert rec["cheapest_po_value_idr"] == 100_000
    prs = agent_tools.list_requisitions(status="", tool_context=ctx)
    assert prs["count"] == 1
    assert "history" not in prs["requisitions"][0]
    assert actions.list_actions(sid) == []


def test_requisition_proposal_is_pending_with_evidence_and_runs_nothing(fake: dict[str, Any], sid: str) -> None:
    assert _agent(sid, [("propose_requisition_from_mrp", {"run_id": RUN_ID})], "PLAN-r1") == ["propose_requisition_from_mrp"]
    [a] = actions.list_actions(sid)
    assert a["status"] == "pending"
    assert a["kind"] == "purchase_requisition"
    assert a["command"] == {"name": "create_requisition", "args": {"from_mrp_run": RUN_ID, "material_ids": ["B-C07"]}}
    assert a["plan_id"] == "PLAN-r1"
    assert RUN_ID in a["rationale"]
    assert "PT Merapi expected 2027-02-10 vs need 2027-02-15 (in time)" in a["rationale"]
    assert [r["net_qty"] for r in a["evidence"][0]["mrp_results"]] == [420.0]
    assert a["evidence"][1]["vendor_options"]["B-C07"]["recommended_vendor_id"] == "VEN-MERAPI"
    assert fake["calls"]["create_requisition"] == []
    again = agent_tools.propose_requisition_from_mrp(run_id="", tool_context=Ctx(sid))
    assert again["action_id"] == a["action_id"]
    assert again["note"]
    fake["run"]["results"] = [_row("B-C07", 0.0)]
    actions.clear(sid)
    assert agent_tools.propose_requisition_from_mrp(run_id=RUN_ID, tool_context=Ctx(sid))["error"]["code"] == "FV-AGT-020"


def test_approve_requisition_runs_once_with_the_approver(fake: dict[str, Any], sid: str) -> None:
    a = agent_tools.propose_requisition_from_mrp(run_id=RUN_ID, tool_context=Ctx(sid))
    out = client.post(f"/api/actions/{a['action_id']}/approve", headers={**h(sid), "X-User": APPROVER})
    assert out.status_code == 200, out.text
    done = actions.get(sid, a["action_id"])
    user = done["decided_by"]
    assert done["execution"]["result"]["pr_id"] == "PR-2702-001"
    assert done["execution"]["result"]["status"] == "Approved"
    assert fake["calls"]["create_requisition"] == [
        {"sid": sid, "lines": None, "from_mrp_run": RUN_ID, "from_plan": None, "material_ids": ["B-C07"], "note": None, "user": user}
    ]
    assert [(c[2], c[3]) for c in fake["calls"]["transition_requisition"]] == [("submit", user), ("approve", user)]
    assert _replay(done)["pr_id"] == "PR-2702-001"
    assert len(fake["calls"]["create_requisition"]) == 1
    again = client.post(f"/api/actions/{a['action_id']}/approve", headers=h(sid))
    assert again.status_code == 409
    assert len(fake["calls"]["create_requisition"]) == 1


def test_reject_runs_nothing(fake: dict[str, Any], sid: str) -> None:
    pr = agent_tools.propose_requisition_from_mrp(run_id=RUN_ID, tool_context=Ctx(sid))
    po = agent_tools.propose_purchase_order(material_id="B-C07", qty=420, need_by="2027-02-15", vendor_id="", tool_context=Ctx(sid))
    for a in (pr, po):
        out = actions.decide(sid, a["action_id"], approve=False, user="manager")
        assert out["status"] == "rejected"
        assert out["execution"] is None
    assert fake["calls"]["create_requisition"] == []
    assert fake["calls"]["create_po"] == []
    assert effects.overrides(sid).extra_receipts == []


def test_po_proposal_names_vendor_arrival_value_and_premium(fake: dict[str, Any], sid: str) -> None:
    calls = [("propose_purchase_order", {"material_id": "PP-EPDM-T20", "qty": 1000, "need_by": "2027-02-15", "vendor_id": ""})]
    assert _agent(sid, calls, "PLAN-p1") == ["propose_purchase_order"]
    [a] = actions.list_actions(sid)
    assert a["status"] == "pending"
    assert a["command"]["name"] == "place_po"
    args = a["command"]["args"]
    assert args == {
        "vendor_id": "VEN-MERAPI",
        "material_id": "PP-EPDM-T20",
        "qty": 1000.0,
        "expected_date": "2027-02-10",
        "unit": "kg",
        "need_by": "2027-02-15",
    }
    assert "PT Merapi" in a["rationale"]
    assert "expected 2027-02-10 vs need 2027-02-15" in a["rationale"]
    assert "PO value Rp120,000" in a["rationale"]
    assert "premium Rp20,000 vs the cheapest (PT Andalas)" in a["rationale"]
    assert a["evidence"][0]["premium_vs_cheapest_idr"] == 20_000
    assert len(a["evidence"][0]["options"]) == 3
    assert fake["calls"]["create_po"] == []
    ctx = Ctx(sid)
    assert agent_tools.propose_purchase_order(material_id="PP-EPDM-T20", qty=1, need_by="", vendor_id="", tool_context=ctx)["action_id"] == a["action_id"]
    bad = agent_tools.propose_purchase_order(material_id="B-C07", qty=5, need_by="", vendor_id="VEN-NOPE", tool_context=ctx)
    assert bad["error"]["code"] == "FV-AGT-021"
    assert agent_tools.propose_purchase_order(material_id="B-C07", qty=0, need_by="", vendor_id="", tool_context=ctx)["error"]["code"] == "FV-AGT-022"
    late = agent_tools.propose_purchase_order(material_id="B-C07", qty=10, need_by="2027-02-15", vendor_id="VEN-ANDALAS", tool_context=ctx)
    assert "4 days late" in late["rationale"]
    quote = agent_tools.propose_purchase_order(material_id="B-C09", qty=10, need_by="", vendor_id="VEN-QUOTE", tool_context=ctx)
    assert "price on quotation" in quote["rationale"]
    assert "premium" not in quote["rationale"]


def test_approve_po_records_an_issued_session_po_and_keeps_the_resin_receipt(fake: dict[str, Any], sid: str) -> None:
    a = agent_tools.propose_purchase_order(material_id="PP-EPDM-T20", qty=1000, need_by="2027-02-15", vendor_id="", tool_context=Ctx(sid))
    out = actions.decide(sid, a["action_id"], approve=True, user=APPROVER)
    result = out["execution"]["result"]
    assert result["lot"].startswith("LOT-PPT20-")
    assert result["session_po_id"] == "PO-2702-S-101"
    assert result["session_po_status"] == "Issued"
    [c] = fake["calls"]["create_po"]
    assert c["vendor_id"] == "VEN-MERAPI"
    assert c["lines"] == [{"material_id": "PP-EPDM-T20", "qty": 1000.0}]
    assert c["issue"] is True
    assert c["user"] == APPROVER
    assert a["action_id"] in c["note"]
    assert _replay(out)["session_po_id"] == "PO-2702-S-101"
    assert len(fake["calls"]["create_po"]) == 1
    with pytest.raises(FVError) as e:
        actions.decide(sid, a["action_id"], approve=True, user=APPROVER)
    assert e.value.code == "FV-ACT-002"
    assert len(fake["calls"]["create_po"]) == 1
    assert [r["material_id"] for r in effects.overrides(sid).extra_receipts] == ["PP-EPDM-T20"]


def test_po_from_shortage_goes_through_the_same_path(fake: dict[str, Any], sid: str) -> None:
    calls = [("get_latest_mrp", {}), ("propose_po_from_shortage", {"material_id": "B-C07"})]
    assert _agent(sid, calls, "PLAN-s1") == ["get_latest_mrp", "propose_po_from_shortage"]
    [a] = actions.list_actions(sid)
    out = actions.decide(sid, a["action_id"], approve=True, user=APPROVER)["execution"]["result"]
    assert out["po_id"] == a["action_id"]
    assert out["logged"] is True
    assert out["session_po_id"] == "PO-2702-S-101"
    [c] = fake["calls"]["create_po"]
    assert (c["vendor_id"], c["lines"], c["issue"], c["user"]) == ("VEN-WARNA", [{"material_id": "B-C07", "qty": 420.0}], True, APPROVER)
    assert RUN_ID in c["note"]


def test_expedite_flow(fake: dict[str, Any], sid: str) -> None:
    calls = [
        ("list_purchase_orders", {"status": ""}),
        ("propose_expedite", {"po_id": PO_ID, "new_promised_date": "2027-02-08", "reason": "Resin runs out 2027-02-09."}),
    ]
    assert _agent(sid, calls, "PLAN-e1") == ["list_purchase_orders", "propose_expedite"]
    [a] = actions.list_actions(sid)
    assert a["kind"] == "expedite_po"
    assert a["command"] == {
        "name": "expedite_po",
        "args": {"po_id": PO_ID, "new_promised_date": "2027-02-08", "reason": "Resin runs out 2027-02-09.", "vendor_id": "VEN-ANDALAS"},
    }
    assert "pull in by 4 days" in a["rationale"]
    assert a["evidence"][0]["purchase_order"]["promised_date"] == "2027-02-12"
    assert fake["calls"]["expedite_po"] == []
    ctx = Ctx(sid)
    assert agent_tools.propose_expedite(po_id=PO_ID, new_promised_date="2027-02-07", reason="x", tool_context=ctx)["action_id"] == a["action_id"]
    assert agent_tools.propose_expedite(po_id=SEED_PO, new_promised_date="2027-02-08", reason="x", tool_context=ctx)["error"]["code"] == "FV-AGT-023"
    received = agent_tools.propose_expedite(po_id="PO-2702-S-002", new_promised_date="2027-02-08", reason="x", tool_context=ctx)
    assert received["error"]["code"] == "FV-AGT-023"
    actions.clear(sid)
    assert agent_tools.propose_expedite(po_id=PO_ID, new_promised_date="2027-02-12", reason="x", tool_context=ctx)["error"]["code"] == "FV-AGT-024"
    assert agent_tools.propose_expedite(po_id="NOPE", new_promised_date="2027-02-01", reason="x", tool_context=ctx)["error"]["code"] == "FV-DATA-001"
    b = agent_tools.propose_expedite(po_id=PO_ID, new_promised_date="2027-02-08", reason="Resin runs out.", tool_context=ctx)
    out = actions.decide(sid, b["action_id"], approve=True, user=APPROVER)
    assert out["execution"]["result"]["promised_date"] == "2027-02-08"
    assert fake["calls"]["expedite_po"] == [(sid, PO_ID, "2027-02-08", "Resin runs out.", APPROVER)]
    _replay(out)
    assert len(fake["calls"]["expedite_po"]) == 1
    assert effects.overrides(sid).extra_receipts == []


def test_approved_resin_po_shows_in_the_po_list(sid: str) -> None:
    a = agent_tools.draft_po(
        vendor_id="VEN-MERAPI", material_id="PP-EPDM-T20", qty_kg=160_000, expected_date="2027-02-06", rationale="r", tool_context=Ctx(sid)
    )
    out = actions.decide(sid, a["action_id"], approve=True, user=APPROVER)["execution"]["result"]
    po = purchasing_docs.get_po(sid, out["session_po_id"])
    assert (po["origin"], po["status"], po["vendor_id"]) == ("session", "Issued", "VEN-MERAPI")
    assert out["session_po_id"] in {o["po_id"] for o in purchasing_docs.list_pos(sid)["orders"]}
    assert out["session_po_id"] not in {o["po_id"] for o in purchasing_docs.list_pos(str(uuid.uuid4()))["orders"]}
