from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.errors import FVError
from app.store import store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from google.adk.agents import BaseAgent
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.tools import BaseTool, ToolContext

MODEL_UNAVAILABLE = "FV-AI-001"
AGENT_TIMEOUT = "FV-AI-003"
SUMMARY_SKIP = ("source", "assumptions", "projection", "weekly_forecast", "machines", "schedule", "by_machine", "by_shift")

Item = dict[str, Any]

_timelines: dict[str, list[Item]] = {}
_queues: dict[str, asyncio.Queue[Item]] = {}


def _brief(v: object, n: int = 160) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def summarize(result: object) -> str:
    if not isinstance(result, dict):
        return _brief(result)
    keys = [k for k in result if k not in SUMMARY_SKIP]
    return ", ".join(f"{k}={_brief(result[k], 40)}" for k in keys[:5])


_t0: dict[str, float] = {}


def _add(run_id: str | None, item: Item) -> None:
    if not run_id or run_id not in _timelines:
        return
    start = _t0.setdefault(run_id, time.monotonic())
    item = {"t_ms": int((time.monotonic() - start) * 1000), **item}
    _timelines[run_id].append(item)
    q = _queues.get(run_id)
    if q:
        q.put_nowait(item)


def before_agent(callback_context: CallbackContext) -> types.Content | None:
    _add(callback_context.state.get("run_id"), {"kind": "agent_start", "agent": callback_context.agent_name})
    return None


def after_agent(callback_context: CallbackContext) -> types.Content | None:
    _add(callback_context.state.get("run_id"), {"kind": "agent_end", "agent": callback_context.agent_name})
    return None


def before_tool(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext) -> dict[str, Any] | None:
    if not tool.name.endswith("_agent"):
        brief = {k: _brief(v, 80) for k, v in args.items()}
        _add(tool_context.state.get("run_id"), {"kind": "tool_call", "agent": tool_context.agent_name, "tool": tool.name, "args": brief})
    return None


def after_tool(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext, tool_response: dict[str, Any]) -> dict[str, Any] | None:
    del args
    if tool.name.endswith("_agent"):
        return None
    src = tool_response.get("source") if isinstance(tool_response, dict) else None
    if src:
        tool_context.state["sources"] = [*tool_context.state.get("sources", []), f"{tool.name}: {src}"]
    err = tool_response.get("error") if isinstance(tool_response, dict) else None
    summary = _brief(err["code"] if err else summarize(tool_response))
    _add(tool_context.state.get("run_id"), {"kind": "tool_result", "agent": tool_context.agent_name, "tool": tool.name, "summary": summary, "error": bool(err)})
    return None


TRACE_CALLBACKS: dict[str, Any] = {
    "before_agent_callback": before_agent,
    "after_agent_callback": after_agent,
    "before_tool_callback": before_tool,
    "after_tool_callback": after_tool,
}


async def run(agent: BaseAgent, prompt: str, session_id: str, plan_id: str | None = None, timeout_s: float = 90.0, run_id: str | None = None) -> Item:
    run_id = run_id or f"RUN-{uuid.uuid4().hex[:10]}"
    _timelines.setdefault(run_id, [])
    service = InMemorySessionService()
    runner = Runner(app_name="fv", agent=agent, session_service=service)
    state: Item = {"session_id": session_id, "plan_id": plan_id, "run_id": run_id, "sources": []}
    sess = await service.create_session(app_name="fv", user_id=session_id, state=state)
    started = datetime.now(UTC)
    final: list[str] = [""]

    async def consume() -> None:
        msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        async for ev in runner.run_async(user_id=session_id, session_id=sess.id, new_message=msg):
            if ev.content and ev.content.parts and ev.author == agent.name:
                t = "".join(p.text or "" for p in ev.content.parts if not p.thought)
                if t.strip():
                    final[0] = t

    try:
        await asyncio.wait_for(consume(), timeout=timeout_s)
    except TimeoutError as e:
        raise FVError(AGENT_TIMEOUT, detail={"run_id": run_id, "timeout_s": timeout_s}) from e
    except FVError:
        raise
    except Exception as e:
        raise FVError(MODEL_UNAVAILABLE, detail={"run_id": run_id, "error": _brief(e, 300)}) from e
    record: Item = {
        "run_id": run_id,
        "session_id": session_id,
        "agent": agent.name,
        "prompt": prompt,
        "plan_id": plan_id,
        "text": final[0],
        "timeline": _timelines.pop(run_id, []),
        "started_at": started.isoformat(),
        "duration_s": round((datetime.now(UTC) - started).total_seconds(), 1),
    }
    _t0.pop(run_id, None)
    store().set("agent_runs", run_id, record)
    return record


async def stream(agent: BaseAgent, prompt: str, session_id: str, plan_id: str | None, timeout_s: float) -> AsyncIterator[Item]:
    run_id = f"RUN-{uuid.uuid4().hex[:10]}"
    q: asyncio.Queue[Item] = asyncio.Queue()
    _queues[run_id] = q
    _timelines[run_id] = []
    task = asyncio.create_task(run(agent, prompt, session_id, plan_id, timeout_s, run_id))
    yield {"kind": "run_start", "run_id": run_id, "plan_id": plan_id}
    try:
        while not task.done() or not q.empty():
            try:
                yield await asyncio.wait_for(q.get(), timeout=0.5)
            except TimeoutError:
                continue
        try:
            rec = task.result()
            yield {"kind": "done", "run_id": run_id, "text": rec["text"], "duration_s": rec["duration_s"]}
        except FVError as e:
            yield {"kind": "error", "run_id": run_id, **e.body()}
    finally:
        _queues.pop(run_id, None)
