from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import actions, effects, modules
from app.briefing import briefing
from app.session import SessionId, user_label

router = APIRouter(prefix="/api")


@router.get("/briefing")
def get_briefing(_sid: SessionId) -> dict[str, Any]:
    return briefing()


@router.get("/modules/planning/what-if")
def planning_what_if(sid: SessionId) -> dict[str, Any]:
    return modules.what_if(effects.overrides(sid))


@router.get("/modules/{module}/summary")
def module_summary(module: str, sid: SessionId) -> dict[str, Any]:
    return modules.summary(module, effects.overrides(sid))


@router.get("/actions")
def list_actions(sid: SessionId, status: str | None = None) -> dict[str, Any]:
    items = actions.list_actions(sid, status)
    plans: dict[str, list[str]] = {}
    for a in items:
        if a["plan_id"]:
            plans.setdefault(a["plan_id"], []).append(a["action_id"])
    return {"actions": items, "plans": plans, "pending": sum(1 for a in items if a["status"] == "pending")}


class Decision(BaseModel):
    note: str | None = None


@router.post("/actions/{action_id}/approve")
def approve(action_id: str, request: Request, sid: SessionId, body: Decision | None = None) -> dict[str, Any]:
    return actions.decide(sid, action_id, approve=True, user=user_label(request), note=(body or Decision()).note)


@router.post("/actions/{action_id}/reject")
def reject(action_id: str, request: Request, sid: SessionId, body: Decision | None = None) -> dict[str, Any]:
    return actions.decide(sid, action_id, approve=False, user=user_label(request), note=(body or Decision()).note)


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, request: Request, sid: SessionId) -> dict[str, Any]:
    return actions.decide_plan(sid, plan_id, approve=True, user=user_label(request))


@router.post("/plans/{plan_id}/reject")
def reject_plan(plan_id: str, request: Request, sid: SessionId) -> dict[str, Any]:
    return actions.decide_plan(sid, plan_id, approve=False, user=user_label(request))
