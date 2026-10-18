from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app import actions, effects, modules
from app.briefing import briefing
from app.session import session_id, user_label

router = APIRouter(prefix="/api")


@router.get("/briefing")
def get_briefing(sid: str = Depends(session_id)) -> dict:
    return briefing()


@router.get("/modules/planning/what-if")
def planning_what_if(sid: str = Depends(session_id)) -> dict:
    return modules.what_if(effects.overrides(sid))


@router.get("/modules/{module}/summary")
def module_summary(module: str, sid: str = Depends(session_id)) -> dict:
    return modules.summary(module, effects.overrides(sid))


@router.get("/actions")
def list_actions(status: str | None = None, sid: str = Depends(session_id)) -> dict:
    items = actions.list_actions(sid, status)
    plans: dict[str, list] = {}
    for a in items:
        if a["plan_id"]:
            plans.setdefault(a["plan_id"], []).append(a["action_id"])
    return {"actions": items, "plans": plans, "pending": sum(1 for a in items if a["status"] == "pending")}


class Decision(BaseModel):
    note: str | None = None


@router.post("/actions/{action_id}/approve")
def approve(action_id: str, request: Request, body: Decision | None = None, sid: str = Depends(session_id)) -> dict:
    return actions.decide(sid, action_id, True, user_label(request), (body or Decision()).note)


@router.post("/actions/{action_id}/reject")
def reject(action_id: str, request: Request, body: Decision | None = None, sid: str = Depends(session_id)) -> dict:
    return actions.decide(sid, action_id, False, user_label(request), (body or Decision()).note)


@router.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, request: Request, sid: str = Depends(session_id)) -> dict:
    return actions.decide_plan(sid, plan_id, True, user_label(request))


@router.post("/plans/{plan_id}/reject")
def reject_plan(plan_id: str, request: Request, sid: str = Depends(session_id)) -> dict:
    return actions.decide_plan(sid, plan_id, False, user_label(request))
