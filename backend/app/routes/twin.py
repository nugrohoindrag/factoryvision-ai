from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app import pubsub, twin
from app.config import settings
from app.errors import FVError
from app.session import session_id
from simulator.engine import MAX_TICKS_PER_CALL, SCENARIOS

router = APIRouter()


class Tick(BaseModel):
    minutes: int | None = Field(default=None, ge=0, le=MAX_TICKS_PER_CALL, description="Explicit step (Replay, tests); default = wall clock")


@router.post("/api/twin/tick")
def twin_tick(body: Tick | None = None, sid: str = Depends(session_id)) -> dict:
    return twin.tick(sid, (body or Tick()).minutes)


@router.get("/api/twin/scenarios")
def twin_scenarios() -> dict:
    return {"scenarios": list(SCENARIOS)}


class Reset(BaseModel):
    scenario: str = "baseline"


@router.post("/api/demo/reset")
def demo_reset(body: Reset | None = None, sid: str = Depends(session_id)) -> dict:
    return twin.reset(sid, (body or Reset()).scenario)


def _verify_push(request: Request) -> None:
    if settings.pubsub != "gcp":
        return
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise FVError("FV-PUB-002")
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(auth.split(" ", 1)[1], g_requests.Request(), audience=settings.push_audience or None)
    except Exception as e:
        raise FVError("FV-PUB-002", detail=str(e)[:200]) from e
    if settings.push_service_account and claims.get("email") != settings.push_service_account:
        raise FVError("FV-PUB-002", detail="unexpected service account")


async def _message(request: Request) -> dict:
    _verify_push(request)
    try:
        return pubsub.decode_push(await request.json())
    except Exception as e:
        raise FVError("FV-PUB-001", detail=str(e)[:200]) from e


@router.post("/pubsub/commands")
async def pubsub_commands(request: Request) -> dict:
    return {"ok": True, "result": twin.on_command(await _message(request))}


@router.post("/pubsub/push")
async def pubsub_push(request: Request) -> dict:
    await _message(request)
    return {"ok": True}
