import asyncio
import uuid
from typing import Any

from app import actions
from app.agents.agents import module_agents, orchestrator
from app.agents.runner import run
from app.agents.tools import MODULE_TOOLS, ORCHESTRATOR_TOOLS
from app.store import store
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool
from google.genai import types


class ScriptedLlm(BaseLlm):
    calls: list[Any] = []
    final: str = "done"

    async def generate_content_async(self, llm_request, stream: bool = False):
        done = sum(1 for c in llm_request.contents for p in (c.parts or []) if p.function_response)
        if done < len(self.calls):
            name, args = self.calls[done]
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=self.final)]))


def test_every_tool_declares_cleanly():
    for fns in [*MODULE_TOOLS.values(), ORCHESTRATOR_TOOLS]:
        for f in fns:
            assert FunctionTool(f)._get_declaration().name == f.__name__


def test_real_agents_are_wired():
    agents = module_agents()
    assert len(agents) == 7 and len({a.name for a in agents.values()}) == 7
    names = {getattr(t, "name", None) or t.__name__ for t in orchestrator().tools}
    assert {a.name for a in agents.values()} <= names and "plan_summary" in names


def test_orchestrator_run_traces_sub_agent_tools_and_creates_pending_proposal():
    mes = LlmAgent(
        name="mes_agent",
        model=ScriptedLlm(
            model="fake",
            calls=[
                ("get_machine_health", {"machine_id": "IMM-04"}),
                ("propose_maintenance", {"machine_id": "IMM-04", "rationale": "cycle rising"}),
            ],
            final="IMM-04 needs maintenance.",
        ),
        tools=MODULE_TOOLS["mes"],
    )
    orch = LlmAgent(
        name="orchestrator",
        model=ScriptedLlm(
            model="fake",
            calls=[("mes_agent", {"request": "check IMM-04"}), ("plan_summary", {})],
            final="One plan. All actions are pending your approval in the Action Center.",
        ),
        tools=[AgentTool(agent=mes), *ORCHESTRATOR_TOOLS],
    )
    sid, plan = str(uuid.uuid4()), "PLAN-test"
    rec = asyncio.run(run(orch, "disruption", sid, plan, timeout_s=60))
    assert "pending your approval" in rec["text"]
    tools = [(e["agent"], e["tool"]) for e in rec["timeline"] if e["kind"] == "tool_call"]
    assert ("mes_agent", "get_machine_health") in tools and ("mes_agent", "propose_maintenance") in tools
    assert any(e["kind"] == "agent_start" and e["agent"] == "mes_agent" for e in rec["timeline"])
    acts = actions.list_actions(sid)
    assert len(acts) == 1 and acts[0]["status"] == "pending" and acts[0]["plan_id"] == plan
    assert acts[0]["impact"]["rupiah_idr"] > 0 and acts[0]["evidence"]
    assert store().get("agent_runs", rec["run_id"])["session_id"] == sid
    actions.clear(sid)
