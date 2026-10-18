from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app import outbox
from app.briefing import briefing
from app.session import SessionId, user_label

router = APIRouter(prefix="/api")


@router.get("/outbox")
def list_outbox(sid: SessionId) -> dict[str, Any]:
    emails = outbox.list_emails(sid)
    return {"emails": emails, "sent": sum(1 for e in emails if e["status"] == "sent"), "configured": outbox.configured()}


@router.post("/outbox/briefing")
def email_briefing(request: Request, sid: SessionId) -> dict[str, Any]:
    """The morning shift briefing to the plant manager (Cloud Scheduler can call this every morning)."""
    return outbox.shift_briefing(sid, briefing(), actor=user_label(request)) or {"status": "failed"}


@router.post("/outbox/{email_id}/send")
def resend(email_id: str, sid: SessionId) -> dict[str, Any]:
    return outbox.deliver(outbox.get(sid, email_id))


@router.get("/outbox/{email_id}/eml")
def download(email_id: str, sid: SessionId) -> Response:
    doc = outbox.get(sid, email_id)
    return Response(outbox.eml(doc), media_type="message/rfc822", headers={"Content-Disposition": f'attachment; filename="{email_id}.eml"'})
