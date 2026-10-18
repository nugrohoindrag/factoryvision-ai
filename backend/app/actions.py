from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app import outbox, pubsub
from app.errors import FVError
from app.store import Doc, store

ACTION_NOT_FOUND = "FV-ACT-001"
ACTION_ALREADY_DECIDED = "FV-ACT-002"
ACTION_EXECUTION_FAILED = "FV-ACT-003"

log = logging.getLogger(__name__)

COLLECTION = "actions"
STATUSES = ("pending", "approved", "rejected", "failed")
IMPACT_KEYS = ("rupiah_idr", "scrap_kg", "kwh", "co2e_kg")

KINDS = {
    "customer_po": ("orders", "apply_customer_po"),
    "customer_order": ("orders", "create_customer_order"),
    "order_cancel": ("orders", "cancel_customer_order"),
    "maintenance": ("mes", "start_maintenance"),
    "mold_move": ("planning", "move_mold"),
    "replan": ("planning", "create_replan"),
    "production_plan_draft": ("planning", "create_draft_plan"),
    "extra_shift": ("planning", "add_shift"),
    "purchase_requisition": ("purchasing", "create_requisition"),
    "purchase_order": ("purchasing", "place_po"),
    "expedite_po": ("purchasing", "expedite_po"),
    "quality_hold": ("quality", "hold_lot"),
    "ship_date": ("delivery", "revise_ship_date"),
    "customer_notice": ("delivery", "notify_customer"),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def propose(
    session_id: str,
    kind: str,
    title: str,
    origin_agent: str,
    rationale: str,
    args: dict[str, Any],
    evidence: list[dict[str, Any]] | None = None,
    impact: dict[str, Any] | None = None,
    plan_id: str | None = None,
    action_id: str | None = None,
) -> Doc:
    if kind not in KINDS:
        msg = f"unknown action kind {kind}"
        raise ValueError(msg)
    module, command = KINDS[kind]
    doc: Doc = {
        "action_id": action_id or f"ACT-{uuid.uuid4().hex[:10]}",
        "session_id": session_id,
        "plan_id": plan_id,
        "kind": kind,
        "module": module,
        "title": title,
        "origin_agent": origin_agent,
        "rationale": rationale,
        "evidence": evidence or [],
        "impact": {k: (impact or {}).get(k) for k in IMPACT_KEYS},
        "command": {"name": command, "args": args},
        "status": "pending",
        "created_at": _now(),
        "decided_at": None,
        "decided_by": None,
        "execution": None,
    }
    store().set(COLLECTION, doc["action_id"], doc)
    return doc


def get(session_id: str, action_id: str) -> Doc:
    doc = store().get(COLLECTION, action_id)
    if not doc or doc["session_id"] != session_id:
        raise FVError(ACTION_NOT_FOUND, detail={"action_id": action_id})
    return doc


def list_actions(session_id: str, status: str | None = None) -> list[Doc]:
    docs = store().where(COLLECTION, "session_id", session_id)
    if status:
        docs = [d for d in docs if d["status"] == status]
    return sorted(docs, key=lambda d: str(d["created_at"]))


def decide(session_id: str, action_id: str, *, approve: bool, user: str, note: str | None = None) -> Doc:
    doc = get(session_id, action_id)
    if doc["status"] != "pending":
        raise FVError(ACTION_ALREADY_DECIDED, detail={"action_id": action_id, "status": doc["status"]})
    fields: Doc = {"decided_at": _now(), "decided_by": user, "note": note}
    if not approve:
        fields["status"] = "rejected"
        store().update(COLLECTION, action_id, fields)
        return {**doc, **fields}
    try:
        command = {**doc["command"], "action_id": action_id, "decided_by": user}
        result = pubsub.publish(pubsub.AGENT_COMMANDS, {"session_id": session_id, "action_id": action_id, "command": command})
        # Firestore stores only plain JSON values (no numpy numbers, tuples or dates); the memory store accepts anything
        fields.update(status="approved", execution={"ok": True, "result": json.loads(json.dumps(result, default=str)), "executed_at": _now()})
    except FVError as e:
        fields.update(status="failed", execution={"ok": False, "error": e.body()["error"], "executed_at": _now()})
    except Exception as e:  # an unexpected failure fails this action only, never a whole plan approval
        log.exception("executing %s failed", action_id)
        error = {"code": "FV-SYS-001", "message": f"{type(e).__name__}: {str(e)[:200]}"}
        fields.update(status="failed", execution={"ok": False, "error": error, "executed_at": _now()})
    store().update(COLLECTION, action_id, fields)
    out = {**doc, **fields}
    if out["status"] == "failed":
        raise FVError(ACTION_EXECUTION_FAILED, detail={"action_id": action_id, "reason": fields["execution"]["error"]})
    mail = outbox.on_approved(session_id, out)
    return {**out, "email": _email_view(mail)}


def _email_view(mail: Doc | None) -> Doc | None:
    return {k: mail.get(k) for k in ("email_id", "status", "reason", "to", "subject")} if mail else None


def decide_plan(session_id: str, plan_id: str, *, approve: bool, user: str) -> Doc:
    results: list[Doc] = []
    failed: list[Doc] = []
    for doc in list_actions(session_id, "pending"):
        if doc["plan_id"] != plan_id:
            continue
        try:
            results.append(decide(session_id, doc["action_id"], approve=approve, user=user))
        except FVError as e:
            failed.append({"action_id": doc["action_id"], "error": e.body()["error"]})
    if not results and not failed:
        raise FVError(ACTION_NOT_FOUND, detail={"plan_id": plan_id})
    approved = [r for r in results if r["status"] == "approved"]
    mail = outbox.on_plan_approved(session_id, plan_id, approved, user) if approve else None
    return {"plan_id": plan_id, "decided": results, "failed": failed, "email": _email_view(mail)}


def approved_commands(session_id: str) -> list[Doc]:
    return [d["command"] | {"action_id": d["action_id"]} for d in list_actions(session_id, "approved")]


def clear(session_id: str) -> None:
    for d in store().where(COLLECTION, "session_id", session_id):
        store().delete(COLLECTION, d["action_id"])
