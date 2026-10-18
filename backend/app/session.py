from __future__ import annotations

import re
import threading
import time
from collections import defaultdict
from typing import Annotated, Any

from fastapi import Depends, Header, Request

from app.config import settings
from app.errors import FVError

NOT_SIGNED_IN = "FV-AUTH-001"
MISSING_SESSION = "FV-SES-001"

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _verify_firebase(token: str) -> dict[str, Any]:
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_firebase_token(token, g_requests.Request(), audience=settings.project)
    except (GoogleAuthError, ValueError) as e:
        raise FVError(NOT_SIGNED_IN, detail=str(e)[:200]) from e
    if not claims:
        raise FVError(NOT_SIGNED_IN)
    return dict(claims)


def session_id(
    request: Request,
    x_session_id: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if not x_session_id or not _UUID.match(x_session_id):
        raise FVError(MISSING_SESSION)
    bearer = authorization.split(" ", 1)[1] if authorization and authorization.lower().startswith("bearer ") else None
    if settings.require_auth:
        if not bearer:
            raise FVError(NOT_SIGNED_IN)
        request.state.user = _verify_firebase(bearer)
    elif bearer:
        # guests use the app without signing in; a signed-in user is still recognised (decisions carry their email)
        try:
            request.state.user = _verify_firebase(bearer)
        except FVError:
            request.state.user = None
    return x_session_id.lower()


SessionId = Annotated[str, Depends(session_id)]


def signed_in(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """A verified Google account (Firebase ID token), required for registration and admin routes."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise FVError(NOT_SIGNED_IN)
    claims = _verify_firebase(authorization.split(" ", 1)[1])
    if not claims.get("email") or not claims.get("email_verified", False):
        raise FVError(NOT_SIGNED_IN, detail="the Google account has no verified email")
    request.state.user = claims
    return claims


SignedIn = Annotated[dict[str, Any], Depends(signed_in)]


def user_label(request: Request) -> str:
    user: dict[str, Any] = getattr(request.state, "user", None) or {}
    return str(user.get("email") or "demo-manager")


# Gemini calls per use of a bucket: a Disruption Response runs the Orchestrator and about 14 module-agent calls.
AI_WEIGHTS = {"live_run": 15, "agent": 1, "vision": 1}


class Limiter:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = defaultdict(int)
        self._last: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def take(self, sid: str, bucket: str, cap: int, code: str) -> int:
        with self._lock:
            key = (sid, bucket)
            if self._counts[key] >= cap:
                raise FVError(code, detail={"cap": cap})
            # Session ids come from the browser, so a new tab is a fresh per-session budget. Every Gemini entry point
            # also draws from one budget per instance per UTC day; with max-instances 3 that bounds the daily spend.
            weight = AI_WEIGHTS.get(bucket, 0)
            if weight:
                day = (f"*{time.strftime('%Y-%m-%d', time.gmtime())}", "ai")
                if self._counts[day] + weight > settings.ai_daily_cap:
                    raise FVError(code, "The daily AI budget of this demo is used up; Replay still works.", {"daily_cap": settings.ai_daily_cap})
                self._counts[day] += weight
            self._counts[key] += 1
            return cap - self._counts[key]

    def debounce(self, sid: str, bucket: str, seconds: float, code: str) -> None:
        with self._lock:
            key = (sid, bucket)
            now = time.monotonic()
            last = self._last.get(key)
            if last is not None and now - last < seconds:
                raise FVError(code, detail={"retry_after_s": round(seconds - (now - last), 1)})
            self._last[key] = now

    def used(self, sid: str, bucket: str) -> int:
        return self._counts.get((sid, bucket), 0)

    def forget_debounce(self, sid: str) -> None:
        with self._lock:
            for key in [k for k in self._last if k[0] == sid]:
                del self._last[key]

    def reset(self, sid: str, keep: tuple[str, ...] = ()) -> None:
        with self._lock:
            for key in [k for k in self._counts if k[0] == sid and k[1] not in keep]:
                del self._counts[key]
            for key in [k for k in self._last if k[0] == sid]:
                del self._last[key]


limiter = Limiter()
