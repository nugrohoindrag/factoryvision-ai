# shortcut: thin stand-in for app.readiness (owner: readiness); the integrator keeps the real module.
from __future__ import annotations

from typing import Any


def _not_checked(plan_id: str, wo_id: str | None = None) -> dict[str, Any]:
    return {
        "plan_id": plan_id,
        "plan_number": None,
        "wo_id": wo_id,
        "status": "NOT_CHECKED",
        "readiness_pct": None,
        "required_date": None,
        "checked_at": None,
        "materials": [],
        "shortages": [],
    }


def check_plan(sid: str, plan_id: str, *, save: bool = True) -> dict[str, Any]:
    del sid, save
    return _not_checked(plan_id)


def latest_check(sid: str, plan_id: str) -> dict[str, Any]:
    del sid
    return _not_checked(plan_id)


def readiness(sid: str, date_from: str | None = None, date_to: str | None = None, only_problems: bool = False) -> dict[str, Any]:  # noqa: FBT001, FBT002 - contract signature
    del sid, only_problems
    return {"from": date_from, "to": date_to, "kpis": {"readiness_pct": None, "plans_monitored": 0, "plans_short": 0, "materials_short": 0}, "plans": []}
