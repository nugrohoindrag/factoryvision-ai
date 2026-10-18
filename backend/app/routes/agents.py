from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import actions, twin
from app.agents import replay
from app.ai import genai
from app.config import settings
from app.errors import FVError
from app.modules import MODULES
from app.session import limiter, session_id
from app.store import store
from app.simulation.engine import SCENARIOS

router = APIRouter(prefix="/api")


@router.get("/mode")
def mode() -> dict:
    return {"ai_enabled": genai.enabled(), "replays": [s for s in SCENARIOS if replay.path(s).exists()], "scenarios": list(SCENARIOS),
            "models": {"orchestrator": settings.model_orchestrator, "module": settings.model_module, "vision": settings.model_vision},
            "caps": {"live_runs": settings.live_runs_per_session, "agent_asks": settings.agent_asks_per_session, "vision_calls": settings.vision_calls_per_session}}


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


@router.post("/agents/{module}/ask")
async def ask(module: str, body: Ask, sid: str = Depends(session_id)) -> dict:
    if module not in MODULES:
        raise FVError("FV-REQ-002", detail={"module": module, "valid": list(MODULES)})
    if not genai.enabled():
        raise FVError("FV-AI-001", "AI is disabled in this environment; use Replay")
    limiter.take(sid, "agent", settings.agent_asks_per_session, "FV-RATE-002")
    from app.agents.agents import module_agents
    from app.agents.runner import run

    plan_id = f"ASK-{uuid.uuid4().hex[:8]}"
    rec = await run(module_agents()[module], body.question, sid, plan_id, timeout_s=45)
    created = [a for a in actions.list_actions(sid) if a["plan_id"] == plan_id]
    return {"module": module, "run_id": rec["run_id"], "text": rec["text"], "timeline": rec["timeline"], "actions": created, "duration_s": rec["duration_s"]}


def _sse(items) -> StreamingResponse:
    async def gen():
        async for item in items:
            yield f"data: {json.dumps(item, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/disruptions/{scenario}")
async def disruption(scenario: str, sid: str = Depends(session_id)):
    if scenario not in SCENARIOS or scenario == "baseline":
        raise FVError("FV-REQ-003", detail={"scenario": scenario, "valid": [s for s in SCENARIOS if s != "baseline"]})
    if not genai.enabled():
        raise FVError("FV-AI-001", "AI is disabled in this environment; use Replay")
    limiter.take(sid, "live_run", settings.live_runs_per_session, "FV-RATE-001")
    from app.agents.agents import orchestrator
    from app.agents.runner import stream

    twin.reset(sid, scenario)
    plan_id = f"PLAN-{uuid.uuid4().hex[:8]}"
    return _sse(stream(orchestrator(), replay.DISRUPTION_PROMPT, sid, plan_id, timeout_s=180))


@router.get("/replay/{scenario}")
def get_replay(scenario: str) -> dict:
    return replay.load(scenario)


@router.post("/replay/{scenario}")
def play_replay(scenario: str, sid: str = Depends(session_id)) -> dict:
    return replay.play(sid, scenario)


@router.get("/runs/{run_id}")
def get_run(run_id: str, sid: str = Depends(session_id)) -> dict:
    rec = store().get("agent_runs", run_id)
    if not rec or rec["session_id"] != sid:
        raise FVError("FV-DATA-001", f"unknown run {run_id}")
    return rec
