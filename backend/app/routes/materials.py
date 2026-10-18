from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app import mrp, readiness
from app.session import SessionId, user_label

router = APIRouter(prefix="/api")


class MrpRunIn(BaseModel):
    horizon_start: str | None = None
    horizon_end: str | None = None
    plan_ids: list[str] | None = None


class ReserveIn(BaseModel):
    plan_id: str
    wo_id: str


@router.get("/materials/readiness")
def get_readiness(
    sid: SessionId,
    *,
    date_from: Annotated[str | None, Query(alias="from")] = None,
    date_to: Annotated[str | None, Query(alias="to")] = None,
    only_problems: bool = False,
) -> dict[str, Any]:
    return readiness.readiness(sid, date_from or None, date_to or None, only_problems=only_problems)


@router.get("/materials/readiness/plans/{plan_id}")
def latest_plan_check(plan_id: str, sid: SessionId) -> dict[str, Any]:
    return readiness.latest_check(sid, plan_id)


@router.post("/materials/readiness/plans/{plan_id}")
def check_plan(plan_id: str, sid: SessionId) -> dict[str, Any]:
    return readiness.check_plan(sid, plan_id)


@router.post("/materials/readiness/plans/{plan_id}/work-orders/{wo_id}")
def check_work_order(plan_id: str, wo_id: str, sid: SessionId) -> dict[str, Any]:
    return readiness.check_work_order(sid, plan_id, wo_id)


@router.get("/materials/requirements")
def requirements(sid: SessionId, plan_id: str | None = None) -> dict[str, Any]:
    return readiness.requirements(sid, plan_id or None)


@router.post("/mrp/runs")
def run_mrp(body: MrpRunIn, request: Request, sid: SessionId) -> dict[str, Any]:
    return mrp.run_mrp(sid, horizon_start=body.horizon_start, horizon_end=body.horizon_end, plan_ids=body.plan_ids, user=user_label(request))


@router.get("/mrp/runs")
def list_runs(sid: SessionId) -> dict[str, Any]:
    return {"runs": mrp.list_runs(sid)}


@router.get("/mrp/runs/latest")
def latest_run(sid: SessionId) -> dict[str, Any] | None:
    return mrp.latest_run(sid)


@router.get("/mrp/runs/{run_id}")
def get_run(run_id: str, sid: SessionId) -> dict[str, Any]:
    return mrp.get_run(sid, run_id)


@router.get("/materials/reservations")
def list_reservations(sid: SessionId, status: str | None = None) -> dict[str, Any]:
    return {"reservations": mrp.list_reservations(sid, status or None)}


@router.post("/materials/reservations")
def reserve(body: ReserveIn, sid: SessionId) -> dict[str, Any]:
    return {"reservations": mrp.reserve_work_order(sid, body.plan_id, body.wo_id)}


@router.post("/materials/reservations/{reservation_id}/release")
def release(reservation_id: str, sid: SessionId) -> dict[str, Any]:
    return mrp.release_reservation(sid, reservation_id)


@router.get("/materials/consumption")
def consumption(sid: SessionId, wo_id: str | None = None) -> dict[str, Any]:
    return mrp.consumption_variance(sid, wo_id or None)
