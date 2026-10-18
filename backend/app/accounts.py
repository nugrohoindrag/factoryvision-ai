"""Accounts: sign up with Google, wait for an admin's approval, then onboard onto a factory template.

The journey follows the Factory Vision trial flow (register -> console -> onboarding wizard -> first workflow), with
two changes: the account is a verified Google sign-in instead of a password, and an admin approves each
registration before onboarding opens. Guests keep using the demo without an account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import outbox
from app.config import settings
from app.errors import FVError
from app.store import Doc, store

COLLECTION = "accounts"
NOT_REGISTERED = "FV-ACC-001"
NOT_APPROVED = "FV-ACC-002"
ADMIN_ONLY = "FV-AUTH-002"
UNKNOWN_ENTITY = "FV-DATA-001"
INVALID_REQUEST = "FV-REQ-001"

INDUSTRIES = ("automotive", "electronics", "fnb", "packaging", "metal-fabrication", "plastics", "chemical", "general")
ROLES = ("Plant manager", "General manager", "PPIC", "Purchasing", "Production leader", "Maintenance", "Quality control", "Warehouse", "Sales")

TEMPLATES = [
    {
        "template_id": "vidad-bumper",
        "name": "Car bumper plant (injection molding, painting, assembly)",
        "industry": "automotive",
        "summary": "Vidad: 6 injection molding machines (IMM-01..06, bottleneck IMM-04), painting and assembly lines, resin PP-EPDM-T20, "
        "6 bumper SKUs for two OEM customers, vendors, BOMs, molds, 90 days of production history and 7 planted stories.",
        "master_data": ["Customers", "Products and BOMs", "Machines and molds", "Materials and vendors", "Shift calendar", "Downtime and defect reasons"],
    }
]

STEPS = [
    ("factory_profile", "Factory profile", "Your factory name, industry and role from registration.", None),
    ("template", "Apply a factory template", "Load starter master data and 90 days of history from a plant like yours.", None),
    ("master_data", "Review the master data", "Customers, products, BOMs, machines, molds, materials and vendors.", "/master/customers"),
    ("briefing", "Read the Plant Briefing", "Yesterday's OEE, the top losses in Rupiah and the risks the agents found.", "/"),
    (
        "first_workflow",
        "First workflow: material check to PO",
        "Check a plan's materials; the purchasing agent drafts the POs; approve them and see the emails.",
        "/material",
    ),
    ("disruption", "Run the Disruption Response", "The Orchestrator traces one disruption across all seven modules and proposes one plan.", "/"),
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def admins() -> set[str]:
    return {e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()}


def is_admin(claims: dict[str, Any]) -> bool:
    return str(claims.get("email", "")).lower() in admins()


def _steps(done: set[str]) -> list[Doc]:
    return [{"step_id": s, "title": t, "detail": d, "link": link, "done": s in done} for s, t, d, link in STEPS]


def view(doc: Doc, claims: dict[str, Any] | None = None) -> Doc:
    done = set(doc.get("steps_done", []))
    steps = _steps(done)
    return {
        **{k: v for k, v in doc.items() if k != "steps_done"},
        "is_admin": bool(claims and is_admin(claims)),
        "onboarding": {"steps": steps, "done": sum(1 for s in steps if s["done"]), "total": len(steps), "template": doc.get("template_id")},
    }


def get(uid: str) -> Doc | None:
    return store().get(COLLECTION, uid)


def me(claims: dict[str, Any]) -> Doc:
    doc = get(str(claims["sub"]))
    if doc is None:
        return {
            "status": "unregistered",
            "email": claims.get("email"),
            "name": claims.get("name"),
            "is_admin": is_admin(claims),
            "industries": INDUSTRIES,
            "roles": ROLES,
        }
    return view(doc, claims)


def register(claims: dict[str, Any], body: Doc) -> Doc:
    factory = str(body.get("factory_name") or "").strip()
    industry = str(body.get("industry") or "")
    role = str(body.get("role") or "")
    if len(factory) < 2 or industry not in INDUSTRIES or role not in ROLES:
        raise FVError(INVALID_REQUEST, detail={"factory_name": "min 2 characters", "industry": list(INDUSTRIES), "role": list(ROLES)})
    uid = str(claims["sub"])
    existing = get(uid)
    if existing is not None:
        return view(existing, claims)
    admin = is_admin(claims)
    doc: Doc = {
        "uid": uid,
        "email": str(claims["email"]).lower(),
        "name": str(body.get("full_name") or claims.get("name") or claims["email"]),
        "picture": claims.get("picture"),
        "factory_name": factory,
        "industry": industry,
        "city": str(body.get("city") or "").strip() or None,
        "plant_scale": str(body.get("plant_scale") or "").strip()[:64] or None,
        "role": role,
        # admins approve themselves; everyone else waits for one
        "status": "approved" if admin else "pending",
        "created_at": _now(),
        "decided_at": _now() if admin else None,
        "decided_by": "self (admin)" if admin else None,
        "template_id": None,
        "steps_done": ["factory_profile"],
    }
    store().set(COLLECTION, uid, doc)
    if not admin:
        outbox.registration_request(doc)
    return view(doc, claims)


def _require_admin(claims: dict[str, Any]) -> None:
    if not is_admin(claims):
        raise FVError(ADMIN_ONLY, detail={"email": claims.get("email")})


def list_registrations(claims: dict[str, Any]) -> Doc:
    _require_admin(claims)
    docs = sorted(store().where(COLLECTION, "status", "pending"), key=lambda d: str(d["created_at"]))
    decided = [d for s in ("approved", "rejected") for d in store().where(COLLECTION, "status", s)]
    decided.sort(key=lambda d: str(d.get("decided_at") or ""), reverse=True)
    return {"pending": [view(d) for d in docs], "decided": [view(d) for d in decided[:50]]}


def decide(claims: dict[str, Any], uid: str, *, approve: bool, note: str | None = None) -> Doc:
    _require_admin(claims)
    doc = get(uid)
    if doc is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown registration {uid}", {"uid": uid})
    fields = {"status": "approved" if approve else "rejected", "decided_at": _now(), "decided_by": str(claims["email"]), "note": note}
    store().update(COLLECTION, uid, fields)
    doc = {**doc, **fields}
    outbox.registration_decision(doc)
    return view(doc)


def _approved(claims: dict[str, Any]) -> Doc:
    doc = get(str(claims["sub"]))
    if doc is None:
        raise FVError(NOT_REGISTERED)
    if doc["status"] != "approved":
        raise FVError(NOT_APPROVED, detail={"status": doc["status"]})
    return doc


def apply_template(claims: dict[str, Any], template_id: str) -> Doc:
    doc = _approved(claims)
    if template_id not in {t["template_id"] for t in TEMPLATES}:
        raise FVError(INVALID_REQUEST, detail={"template_id": template_id, "valid": [t["template_id"] for t in TEMPLATES]})
    done = sorted({*doc.get("steps_done", []), "template"})
    store().update(COLLECTION, doc["uid"], {"template_id": template_id, "template_applied_at": _now(), "steps_done": done})
    return view({**doc, "template_id": template_id, "steps_done": done}, claims)


def complete_step(claims: dict[str, Any], step_id: str) -> Doc:
    doc = _approved(claims)
    if step_id not in {s for s, *_ in STEPS}:
        raise FVError(INVALID_REQUEST, detail={"step_id": step_id, "valid": [s for s, *_ in STEPS]})
    done = sorted({*doc.get("steps_done", []), step_id})
    store().update(COLLECTION, doc["uid"], {"steps_done": done})
    return view({**doc, "steps_done": done}, claims)


def registered_email(addr: str) -> bool:
    """An approved account's own verified Google address may receive mail (only its own)."""
    return any(d.get("status") in ("approved", "pending", "rejected") for d in store().where(COLLECTION, "email", addr.lower()))
