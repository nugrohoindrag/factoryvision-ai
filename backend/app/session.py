from __future__ import annotations

import re
import threading
import time
from collections import defaultdict

from fastapi import Header, Request

from app.config import settings
from app.errors import FVError

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _verify_firebase(token: str) -> dict:
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_firebase_token(token, g_requests.Request(), audience=settings.project)
    except Exception as e:
        raise FVError("FV-AUTH-001", detail=str(e)[:200]) from e
    if not claims:
        raise FVError("FV-AUTH-001")
    return claims


def session_id(request: Request, x_session_id: str | None = Header(default=None), authorization: str | None = Header(default=None)) -> str:
    if not x_session_id or not _UUID.match(x_session_id):
        raise FVError("FV-SES-001")
    if settings.require_auth:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise FVError("FV-AUTH-001")
        request.state.user = _verify_firebase(authorization.split(" ", 1)[1])
    return x_session_id.lower()


def user_label(request: Request) -> str:
    user = getattr(request.state, "user", None)
    return (user or {}).get("email") or "demo-manager"


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

    def reset(self, sid: str, keep: tuple[str, ...] = ()) -> None:
        with self._lock:
            for key in [k for k in self._counts if k[0] == sid and k[1] not in keep]:
                del self._counts[key]
            for key in [k for k in self._last if k[0] == sid]:
                del self._last[key]


limiter = Limiter()
