from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.errors import FVError
from app.store import store

MODEL_UNAVAILABLE = "FV-AI-001"
AGENT_TIMEOUT = "FV-AI-003"

_timelines: dict[str, list[dict[str, Any]]] = {}
_queues: dict[str, asyncio.Queue] = {}


def _brief(v, n: int = 160) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


class TimelinePlugin(BasePlugin):
    def __init__(self) -> None:
        super().__init__(name="fv_timeline")
        self._t0: dict[str, float] = {}

    def _add(self, run_id: str | None, item: dict[str, Any]) -> None:
        if not run_id or run_id not in _timelines:
            return
        start = self._t0.setdefault(run_id, time.monotonic())
        item = {"t_ms": int((time.monotonic() - start) * 1000), **item}
        _timelines[run_id].append(item)
        q = _queues.get(run_id)
        if q:
            q.put_nowait(item)

    async def before_agent_callback(self, *, agent, callback_context):
        self._add(callback_context.state.get("run_id"), {"kind": "agent_start", "agent": agent.name})

    async def after_agent_callback(self, *, agent, callback_context):
        self._add(callback_context.state.get("run_id"), {"kind": "agent_end", "agent": agent.name})

    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        if tool.name.endswith("_agent"):
            return
        self._add(
            tool_context.state.get("run_id"),
            {"kind": "tool_call", "agent": tool_context.agent_name, "tool": tool.name, "args": {k: _brief(v, 80) for k, v in tool_args.items()}},
        )

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        if tool.name.endswith("_agent"):
            return
        src = result.get("source") if isinstance(result, dict) else None
        if src:
            tool_context.state["sources"] = [*tool_context.state.get("sources", []), f"{tool.name}: {src}"]
        err = result.get("error") if isinstance(result, dict) else None
        self._add(
            tool_context.state.get("run_id"),
            {
                "kind": "tool_result",
                "agent": tool_context.agent_name,
                "tool": tool.name,
                "summary": _brief(err["code"] if err else _summary(result)),
                "error": bool(err),
            },
        )


def _summary(result) -> str:
    if not isinstance(result, dict):
        return _brief(result)
    keys = [k for k in result if k not in ("source", "assumptions", "projection", "weekly_forecast", "machines", "schedule", "by_machine", "by_shift")]
    return ", ".join(f"{k}={_brief(result[k], 40)}" for k in keys[:5])


async def run(agent, prompt: str, session_id: str, plan_id: str | None = None, timeout_s: float = 90.0, run_id: str | None = None) -> dict[str, Any]:
    run_id = run_id or f"RUN-{uuid.uuid4().hex[:10]}"
    _timelines.setdefault(run_id, [])
    service = InMemorySessionService()
    runner = Runner(app_name="fv", agent=agent, session_service=service, plugins=[TimelinePlugin()])
    state = {"session_id": session_id, "plan_id": plan_id, "run_id": run_id, "sources": []}
    sess = await service.create_session(app_name="fv", user_id=session_id, state=state)
    started = datetime.now(UTC)
    text = ""

    async def _go():
        nonlocal text
        msg = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        async for ev in runner.run_async(user_id=session_id, session_id=sess.id, new_message=msg):
            if ev.content and ev.content.parts and ev.author == agent.name:
                t = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))
                if t.strip():
                    text = t

    try:
        await asyncio.wait_for(_go(), timeout=timeout_s)
    except TimeoutError as e:
        raise FVError(AGENT_TIMEOUT, detail={"run_id": run_id, "timeout_s": timeout_s}) from e
    except FVError:
        raise
    except Exception as e:
        raise FVError(MODEL_UNAVAILABLE, detail={"run_id": run_id, "error": _brief(e, 300)}) from e
    record = {
        "run_id": run_id,
        "session_id": session_id,
        "agent": agent.name,
        "prompt": prompt,
        "plan_id": plan_id,
        "text": text,
        "timeline": _timelines.pop(run_id, []),
        "started_at": started.isoformat(),
        "duration_s": round((datetime.now(UTC) - started).total_seconds(), 1),
    }
    store().set("agent_runs", run_id, record)
    return record


async def stream(agent, prompt: str, session_id: str, plan_id: str | None, timeout_s: float) -> AsyncIterator[dict[str, Any]]:
    run_id = f"RUN-{uuid.uuid4().hex[:10]}"
    q: asyncio.Queue = asyncio.Queue()
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
