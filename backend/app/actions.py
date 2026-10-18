from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app import pubsub
from app.errors import FVError
from app.store import store

COLLECTION = "actions"
STATUSES = ("pending", "approved", "rejected", "failed")
IMPACT_KEYS = ("rupiah_idr", "scrap_kg", "kwh", "co2e_kg")

KINDS = {
    "maintenance": ("mes", "start_maintenance"),
    "mold_move": ("planning", "move_mold"),
    "extra_shift": ("planning", "add_shift"),
    "purchase_order": ("purchasing", "place_po"),
    "expedite_po": ("purchasing", "expedite_po"),
    "quality_hold": ("quality", "hold_lot"),
    "ship_date": ("delivery", "revise_ship_date"),
    "customer_notice": ("delivery", "notify_customer"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def propose(session_id: str, kind: str, title: str, origin_agent: str, rationale: str, args: dict,
            evidence: list[dict] | None = None, impact: dict | None = None, plan_id: str | None = None,
            action_id: str | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown action kind {kind}")
    module, command = KINDS[kind]
    doc = {
        "action_id": action_id or f"ACT-{uuid.uuid4().hex[:10]}",
        "session_id": session_id, "plan_id": plan_id, "kind": kind, "module": module, "title": title,
        "origin_agent": origin_agent, "rationale": rationale, "evidence": evidence or [],
        "impact": {k: (impact or {}).get(k) for k in IMPACT_KEYS},
        "command": {"name": command, "args": args},
        "status": "pending", "created_at": _now(), "decided_at": None, "decided_by": None, "execution": None,
    }
    store().set(COLLECTION, doc["action_id"], doc)
    return doc


def get(session_id: str, action_id: str) -> dict:
    doc = store().get(COLLECTION, action_id)
    if not doc or doc["session_id"] != session_id:
        raise FVError("FV-ACT-001", detail={"action_id": action_id})
    return doc


def list_actions(session_id: str, status: str | None = None) -> list[dict]:
    docs = store().where(COLLECTION, "session_id", session_id)
    if status:
        docs = [d for d in docs if d["status"] == status]
    return sorted(docs, key=lambda d: d["created_at"])


def decide(session_id: str, action_id: str, approve: bool, user: str, note: str | None = None) -> dict:
    doc = get(session_id, action_id)
    if doc["status"] != "pending":
        raise FVError("FV-ACT-002", detail={"action_id": action_id, "status": doc["status"]})
    fields = {"decided_at": _now(), "decided_by": user, "note": note}
    if not approve:
        fields["status"] = "rejected"
        store().update(COLLECTION, action_id, fields)
        return {**doc, **fields}
    try:
        result = pubsub.publish(pubsub.AGENT_COMMANDS, {"session_id": session_id, "action_id": action_id, "command": doc["command"]})
        fields.update(status="approved", execution={"ok": True, "result": result, "executed_at": _now()})
    except FVError as e:
        fields.update(status="failed", execution={"ok": False, "error": e.body()["error"], "executed_at": _now()})
    store().update(COLLECTION, action_id, fields)
    out = {**doc, **fields}
    if out["status"] == "failed":
        raise FVError("FV-ACT-003", detail={"action_id": action_id, "reason": fields["execution"]["error"]})
    return out


def decide_plan(session_id: str, plan_id: str, approve: bool, user: str) -> dict:
    results, failed = [], []
    for doc in list_actions(session_id, "pending"):
        if doc["plan_id"] != plan_id:
            continue
        try:
            results.append(decide(session_id, doc["action_id"], approve, user))
        except FVError as e:
            failed.append({"action_id": doc["action_id"], "error": e.body()["error"]})
    if not results and not failed:
        raise FVError("FV-ACT-001", detail={"plan_id": plan_id})
    return {"plan_id": plan_id, "decided": results, "failed": failed}


def approved_commands(session_id: str) -> list[dict]:
    return [d["command"] | {"action_id": d["action_id"]} for d in list_actions(session_id, "approved")]


def clear(session_id: str) -> None:
    for d in store().where(COLLECTION, "session_id", session_id):
        store().delete(COLLECTION, d["action_id"])
