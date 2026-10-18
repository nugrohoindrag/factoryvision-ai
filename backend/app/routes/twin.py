import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app import pubsub, twin
from app.config import settings
from app.errors import FVError
from app.session import SessionId
from app.simulation.engine import MAX_TICKS_PER_CALL, SCENARIOS

INVALID_PUSH_MESSAGE = "FV-PUB-001"
PUSH_NOT_AUTHORISED = "FV-PUB-002"

router = APIRouter()
log = logging.getLogger("fv.pubsub")


class Tick(BaseModel):
    minutes: int | None = Field(default=None, ge=0, le=MAX_TICKS_PER_CALL, description="Explicit step (Replay, tests); default = wall clock")


@router.post("/api/twin/tick")
def twin_tick(sid: SessionId, body: Tick | None = None) -> dict[str, Any]:
    return twin.tick(sid, (body or Tick()).minutes)


@router.get("/api/twin/scenarios")
def twin_scenarios() -> dict[str, Any]:
    return {"scenarios": list(SCENARIOS)}


class Reset(BaseModel):
    scenario: str = "baseline"


@router.post("/api/demo/reset")
def demo_reset(sid: SessionId, body: Reset | None = None) -> dict[str, Any]:
    return twin.reset(sid, (body or Reset()).scenario)


def _verify_push(request: Request) -> None:
    if settings.pubsub != "gcp":
        return
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise FVError(PUSH_NOT_AUTHORISED)
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(auth.split(" ", 1)[1], g_requests.Request(), audience=settings.push_audience or None)
    except (GoogleAuthError, ValueError) as e:
        raise FVError(PUSH_NOT_AUTHORISED, detail=str(e)[:200]) from e
    if settings.push_service_account and claims.get("email") != settings.push_service_account:
        raise FVError(PUSH_NOT_AUTHORISED, detail="unexpected service account")


async def _message(request: Request) -> dict[str, Any]:
    _verify_push(request)
    try:
        return pubsub.decode_push(await request.json())
    except (KeyError, TypeError, ValueError) as e:
        raise FVError(INVALID_PUSH_MESSAGE, detail=str(e)[:200]) from e


@router.post("/pubsub/commands")
async def pubsub_commands(request: Request) -> dict[str, Any]:
    message = await _message(request)
    try:
        return {"ok": True, "result": twin.on_command(message)}
    except FVError as e:
        # A rejected command fails the same way on every delivery; answer 2xx so Pub/Sub acks it instead of retrying.
        log.warning("command %s rejected: %s", message.get("action_id"), e)
        return {"ok": False, **e.body()}


@router.post("/pubsub/push")
async def pubsub_push(request: Request) -> dict[str, Any]:
    await _message(request)
    return {"ok": True}
