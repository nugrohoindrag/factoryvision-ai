"""Outbox: every approved agent action becomes an email to the people it concerns.

The email is written from the approved proposal (title, rationale, the command's facts, impact), so it only states
numbers the tools produced. Delivery goes through the Gmail API as the configured sender, and only to
plus-addresses of `settings.mail_base` (e.g. owner+vendor-ven-merapi@gmail.com), so a demo never mails a real vendor
or customer. Without the Gmail secret the email stays in the Outbox as `not_sent`; an outbox failure never fails
the approval itself.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

from app import plant
from app.config import settings
from app.errors import FVError
from app.store import Doc, store

log = logging.getLogger(__name__)

UNKNOWN_ENTITY = "FV-DATA-001"
COLLECTION = "outbox"
EXAMPLE_DOMAIN = "vidad.example"
TIMEOUT_S = 15

# who an approved action is written to: (primary recipient roles, cc roles)
RECIPIENTS: dict[str, tuple[list[str], list[str]]] = {
    "purchase_order": (["vendor"], ["purchasing"]),
    "expedite_po": (["vendor"], ["purchasing"]),
    "purchase_requisition": (["purchasing"], ["ppic"]),
    "customer_notice": (["customer"], ["sales"]),
    "ship_date": (["sales"], ["ppic"]),
    "maintenance": (["maintenance"], ["leader-molding", "plant-manager"]),
    "quality_hold": (["qc"], ["warehouse"]),
    "replan": (["ppic"], ["plant-manager"]),
    "production_plan_draft": (["ppic"], []),
    "mold_move": (["ppic"], ["leader-molding"]),
    "extra_shift": (["production-manager"], ["ppic"]),
    "customer_po": (["sales"], ["ppic"]),
    "customer_order": (["sales"], ["ppic"]),
    "order_cancel": (["sales"], ["ppic"]),
}

GREETING = {
    "vendor": "Dear {name} team,",
    "customer": "Dear customer,",
}

IMPACT_LABELS = (("rupiah_idr", "Rp", "{:,.0f}"), ("scrap_kg", "scrap kg", "{:,.0f}"), ("kwh", "kWh", "{:,.0f}"), ("co2e_kg", "CO2e kg", "{:,.0f}"))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def address(alias: str) -> str:
    """A role or partner alias as an email address: a plus-address of the configured base, else a fictional one."""
    base = settings.mail_base.strip()
    if "@" not in base:
        return f"{alias}@{EXAMPLE_DOMAIN}"
    local, domain = base.split("@", 1)
    return f"{local.split('+', 1)[0]}+{alias}@{domain}"


def deliverable(addr: str) -> bool:
    base = settings.mail_base.strip()
    if "@" not in base:
        return False
    local, domain = base.lower().split("@", 1)
    a_local, _, a_domain = addr.lower().partition("@")
    if a_domain == domain and (a_local == local or a_local.startswith(f"{local}+")):
        return True
    # a registered Google account may receive mail about its own registration
    return bool(store().where("accounts", "email", addr.lower()))


def _alias(role: str, args: dict[str, Any]) -> tuple[str, str]:
    """(alias, display name) for a role; vendor and customer resolve to the partner in the action."""
    if role == "vendor":
        vid = str(args.get("vendor_id") or "vendor")
        name = plant.VENDOR_BY_ID.get(vid, {}).get("vendor_name", vid)
        return f"vendor-{vid.lower()}", str(name)
    if role == "customer":
        cid = str(args.get("customer_id") or "oem-a")
        return f"customer-{cid.lower()}", cid
    return role, role.replace("-", " ").title()


def _facts(args: dict[str, Any]) -> list[str]:
    skip = {"message", "lines", "order"}
    out = []
    for k, v in args.items():
        if k in skip or k.startswith("_") or v in (None, "", [], {}):
            continue
        val = ", ".join(map(str, v)) if isinstance(v, list) else f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
        out.append(f"- {k.replace('_', ' ')}: {val}")
    return out


def _impact(impact: dict[str, Any]) -> str:
    parts = [f"{label} {fmt.format(impact[k])}" for k, label, fmt in IMPACT_LABELS if impact.get(k)]
    return ", ".join(parts)


def _po_number(action: Doc) -> str | None:
    result = ((action.get("execution") or {}).get("result")) or {}
    if isinstance(result, dict):
        for key in ("session_po_id", "po_id"):
            if result.get(key):
                return str(result[key])
    return None


def compose(action: Doc) -> Doc:
    kind = action["kind"]
    args = dict(action["command"].get("args") or {})
    to_roles, cc_roles = RECIPIENTS.get(kind, (["plant-manager"], []))
    to = [_alias(r, args) for r in to_roles]
    cc = [_alias(r, args) for r in cc_roles]
    po = _po_number(action)
    subject = f"[Vidad] {action['title']}" + (f" ({po})" if po else "")
    greeting = GREETING.get(to_roles[0], "Hello {name},").format(name=to[0][1])
    agent = str(action.get("origin_agent") or "agent").replace("_", " ")
    if kind == "customer_notice" and args.get("message"):
        body_main = [str(args["message"])]
    else:
        body_main = [str(action["rationale"])]
        facts = _facts(args)
        if po:
            facts.insert(0, f"- purchase order: {po}")
        if facts:
            body_main += ["", "Details:", *facts]
    impact = _impact(action.get("impact") or {})
    lines = [
        greeting,
        "",
        *body_main,
        *(["", f"Expected impact: {impact}."] if impact else []),
        "",
        f"This was proposed by the Factory Vision AI {agent} and approved by {action.get('decided_by') or 'a manager'} "
        f"at {str(action.get('decided_at') or '')[:16].replace('T', ' ')} UTC.",
        "",
        "Regards,",
        "Vidad Plant Operations (Factory Vision AI)",
    ]
    return {
        "to": [address(a) for a, _ in to],
        "cc": [address(a) for a, _ in cc],
        "subject": subject,
        "body": "\n".join(lines),
    }


def compose_plan(plan_id: str, approved: list[Doc], user: str) -> Doc:
    total: dict[str, float] = {}
    for a in approved:
        for k, _, _ in IMPACT_LABELS:
            total[k] = total.get(k, 0) + float((a.get("impact") or {}).get(k) or 0)
    items = [f"{i}. {a['title']} ({str(a.get('origin_agent') or '').replace('_', ' ')})" for i, a in enumerate(approved, 1)]
    impact = _impact(total)
    kind = "purchase orders for a material shortage" if plan_id.startswith("RDY-") else "cross-module action plan"
    lines = [
        "Hello Plant Manager,",
        "",
        f"{user} approved the {kind} {plan_id} with {len(approved)} action(s):",
        "",
        *items,
        *(["", f"Plan impact: {impact}."] if impact else []),
        "",
        "Each action was proposed by a Factory Vision AI agent from BigQuery, OR-Tools and BigQuery ML numbers, and executed "
        "only after this approval. The people each action concerns received their own email.",
        "",
        "Regards,",
        "Factory Vision AI",
    ]
    return {
        "to": [address("plant-manager")],
        "cc": [address("gm")],
        "subject": f"[Vidad] Approved: {plan_id}, {len(approved)} action(s)",
        "body": "\n".join(lines),
    }


def _mime(doc: Doc, sender: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"Factory Vision AI <{sender}>"
    msg["To"] = ", ".join(doc["to"])
    if doc["cc"]:
        msg["Cc"] = ", ".join(doc["cc"])
    msg["Subject"] = doc["subject"]
    msg.set_content(doc["body"])
    return msg


def eml(doc: Doc) -> bytes:
    return bytes(_mime(doc, _oauth().get("sender") or address("agent")))


def _oauth() -> dict[str, Any]:
    try:
        data = json.loads(settings.gmail_oauth) if settings.gmail_oauth.strip() else {}
    except ValueError:
        log.warning("FV_GMAIL_OAUTH is not valid JSON; emails stay in the outbox")
        return {}
    return data if isinstance(data, dict) else {}


def _post(url: str, data: bytes, headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 - fixed https Google endpoints
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode() or "{}"))


def _gmail_send(msg: EmailMessage, oauth: dict[str, Any]) -> str:
    token = _post(
        "https://oauth2.googleapis.com/token",
        urllib.parse.urlencode(
            {"client_id": oauth["client_id"], "client_secret": oauth["client_secret"], "refresh_token": oauth["refresh_token"], "grant_type": "refresh_token"}
        ).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )["access_token"]
    raw = base64.urlsafe_b64encode(bytes(msg)).decode()
    sent = _post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        json.dumps({"raw": raw}).encode(),
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    return str(sent.get("id", ""))


def configured() -> bool:
    oauth = _oauth()
    return "@" in settings.mail_base and all(k in oauth for k in ("client_id", "client_secret", "refresh_token"))


def _sent_count(session_id: str) -> int:
    return sum(1 for d in store().where(COLLECTION, "session_id", session_id) if d.get("status") == "sent")


def deliver(doc: Doc) -> Doc:
    """Try to send one outbox email; record why not when it cannot be sent. Never raises."""
    oauth = _oauth()
    fields: Doc = {}
    recipients = [*doc["to"], *doc["cc"]]
    if not all(k in oauth for k in ("client_id", "client_secret", "refresh_token")):
        fields = {"status": "not_sent", "reason": "Gmail sender is not configured on this server"}
    elif not recipients or not all(deliverable(a) for a in recipients):
        fields = {"status": "not_sent", "reason": "recipient is not a plus-address of the demo inbox"}
    elif _sent_count(doc["session_id"]) >= settings.mail_sends_per_session:
        fields = {"status": "not_sent", "reason": f"email limit of {settings.mail_sends_per_session} per session reached"}
    else:
        try:
            gmail_id = _gmail_send(_mime(doc, str(oauth.get("sender") or settings.mail_base)), oauth)
            fields = {"status": "sent", "sent_at": _now(), "gmail_id": gmail_id, "reason": None}
        except Exception as e:  # noqa: BLE001 - delivery problems must not fail the approval
            log.warning("gmail send failed for %s: %s", doc["email_id"], e)
            fields = {"status": "failed", "reason": f"Gmail: {type(e).__name__}"}
    store().update(COLLECTION, doc["email_id"], fields)
    return {**doc, **fields}


# the role a person picked at sign-up -> the outbox roles whose email they receive
ACCOUNT_ROLES = {
    "Plant manager": {"plant-manager"},
    "General manager": {"gm"},
    "PPIC": {"ppic"},
    "Purchasing": {"purchasing", "vendor"},
    "Production leader": {"leader-molding", "production-manager"},
    "Maintenance": {"maintenance"},
    "Quality control": {"qc"},
    "Warehouse": {"warehouse"},
    "Sales": {"sales", "customer"},
}


def _subscribers(roles: list[str], actor: str | None) -> list[str]:
    """Approved users whose sign-up role matches, but only for work done by a signed-in, approved user:
    a guest's demo actions go to the demo inbox only, never to registered people."""
    accounts = store().where("accounts", "status", "approved")
    if not actor or not any(a.get("email") == actor.lower() for a in accounts):
        return []
    wanted = set(roles)
    return sorted({str(a["email"]) for a in accounts if ACCOUNT_ROLES.get(str(a.get("role")), set()) & wanted})


def _save(
    session_id: str,
    mail: Doc,
    *,
    kind: str,
    action_id: str | None,
    plan_id: str | None,
    agent: str,
    roles: list[str] | None = None,
    actor: str | None = None,
) -> Doc:
    extra = [e for e in _subscribers(roles or [], actor) if e not in mail["to"] and e not in mail["cc"]]
    mail = {**mail, "cc": [*mail["cc"], *extra]}
    doc: Doc = {
        "email_id": f"MAIL-{uuid.uuid4().hex[:10]}",
        "session_id": session_id,
        "kind": kind,
        "action_id": action_id,
        "plan_id": plan_id,
        "origin_agent": agent,
        "created_at": _now(),
        "status": "queued",
        "reason": None,
        **mail,
    }
    store().set(COLLECTION, doc["email_id"], doc)
    return deliver(doc)


def on_approved(session_id: str, action: Doc) -> Doc | None:
    try:
        return _save(
            session_id,
            compose(action),
            kind=action["kind"],
            action_id=action["action_id"],
            plan_id=action.get("plan_id"),
            agent=str(action.get("origin_agent") or ""),
            roles=[r for pair in RECIPIENTS.get(action["kind"], (["plant-manager"], [])) for r in pair],
            actor=action.get("decided_by"),
        )
    except Exception:
        log.exception("outbox: could not write the email for %s", action.get("action_id"))
        return None


def on_plan_approved(session_id: str, plan_id: str, approved: list[Doc], user: str) -> Doc | None:
    if not approved:
        return None
    try:
        return _save(
            session_id,
            compose_plan(plan_id, approved, user),
            kind="plan_summary",
            action_id=None,
            plan_id=plan_id,
            agent="orchestrator",
            roles=["plant-manager", "gm"],
            actor=user,
        )
    except Exception:
        log.exception("outbox: could not write the plan summary for %s", plan_id)
        return None


def _guarded(what: str, fn: Any) -> Doc | None:
    try:
        return fn()  # type: ignore[no-any-return]
    except Exception:
        log.exception("outbox: could not write %s", what)
        return None


SIGNATURE = ["", "Regards,", "Factory Vision AI"]


def shortage_alert(session_id: str, check: Doc, purchasing: Doc | None, actor: str | None = None) -> Doc | None:
    """Information for PPIC and Purchasing when a readiness check finds a shortage; sent without approval."""

    def run() -> Doc:
        rows = check.get("shortages", [])
        plan = check.get("plan_number") or check.get("plan_id")
        lines = [
            "Hello PPIC and Purchasing,",
            "",
            f"The material check of production plan {plan} found {len(rows)} short material(s); readiness {check.get('readiness_pct')}%.",
            "",
            *[
                f"- {r['material_id']}{' (' + r['color'] + ')' if r.get('color') else ''}: need {r.get('required_qty', 0):,.0f} {r.get('unit')} "
                f"by {r.get('required_date')}, available {r.get('available_qty', 0):,.0f}, short {r.get('shortage_qty', 0):,.0f}"
                for r in rows
            ],
        ]
        if purchasing and purchasing.get("proposals"):
            lines += ["", f"The purchasing agent drafted {len(purchasing['proposals'])} PO(s) in group {purchasing['group']}:"]
            lines += [f"- {p['title']}" for p in purchasing["proposals"]]
            lines += ["They wait for approval in the Action Center."]
        mail = {
            "to": [address("ppic")],
            "cc": [address("purchasing")],
            "subject": f"[Vidad] Material shortage for plan {plan}",
            "body": "\n".join([*lines, *SIGNATURE]),
        }
        return _save(
            session_id,
            mail,
            kind="shortage_alert",
            action_id=None,
            plan_id=(purchasing or {}).get("group"),
            agent="material_supply_agent",
            roles=["ppic", "purchasing"],
            actor=actor,
        )

    return _guarded("the shortage alert", run)


def vendor_reply_draft(session_id: str, facts: Doc) -> Doc | None:
    """A reply to a vendor's delay email, kept as a draft: a person sends it from the Outbox."""

    def run() -> Doc:
        vendor = str(facts.get("vendor_name") or "vendor")
        vid = next((v["vendor_id"] for v in plant.VENDORS if v["vendor_name"] == vendor), "vendor")
        new, old, days = facts.get("new_date"), facts.get("original_date"), facts.get("delay_days")
        lines = [
            f"Dear {vendor} team,",
            "",
            f"Thank you for letting us know that the {facts.get('material_id')} delivery moves from {old} to {new} ({days} days later).",
            "This date puts our production plan at risk. Please confirm by return whether a partial delivery before "
            f"{old} is possible, and send the confirmed dispatch date for the balance.",
            "We are also checking an alternative source and will update the open order accordingly.",
            *SIGNATURE[:-1],
            "Vidad Purchasing (drafted by the Factory Vision AI purchasing agent)",
        ]
        doc: Doc = {
            "email_id": f"MAIL-{uuid.uuid4().hex[:10]}",
            "session_id": session_id,
            "kind": "vendor_reply",
            "action_id": None,
            "plan_id": None,
            "origin_agent": "purchasing_agent",
            "created_at": _now(),
            "status": "draft",
            "reason": "Draft: review it and press Send",
            "to": [address(f"vendor-{str(vid).lower()}")],
            "cc": [address("purchasing")],
            "subject": f"RE: {facts.get('material_id')} delivery moved to {new}",
            "body": "\n".join(lines),
        }
        store().set(COLLECTION, doc["email_id"], doc)
        return doc

    return _guarded("the vendor reply", run)


def shift_briefing(session_id: str, brief: Doc, actor: str | None = None) -> Doc | None:
    def run() -> Doc:
        oee = brief.get("oee", {})
        lines = [
            "Good morning,",
            "",
            f"Shift briefing for {brief.get('plant')} on {brief.get('as_of')} (yesterday {brief.get('yesterday')}):",
            "",
            f"OEE: molding {oee.get('molding')}%, plant {oee.get('plant')}%; worst machine {oee.get('worst_machine')} at {oee.get('worst_machine_oee_pct')}%.",
            "",
            "Top losses yesterday:",
            *[f"- {x['title']}: Rp{x['loss_idr']:,.0f} ({x.get('detail', '')})" for x in brief.get("top_losses", [])[:3]],
            "",
            "Risks to act on:",
            *[f"- [{r.get('severity')}] {r['title']}: {r.get('detail', '')}" for r in brief.get("risks", [])],
            "",
            "Open the Action Center for the proposals that wait for your approval.",
            *SIGNATURE,
        ]
        mail = {
            "to": [address("plant-manager")],
            "cc": [address("production-manager")],
            "subject": f"[Vidad] Shift briefing {brief.get('as_of')}",
            "body": "\n".join(lines),
        }
        return _save(
            session_id,
            mail,
            kind="shift_briefing",
            action_id=None,
            plan_id=None,
            agent="orchestrator",
            roles=["plant-manager", "production-manager"],
            actor=actor,
        )

    return _guarded("the shift briefing", run)


def sustainability_report(session_id: str, rep: Doc, actor: str | None = None) -> Doc | None:
    def run() -> Doc:
        cur, ch = rep["current"], rep["change_pct"]

        def line(label: str, key: str, unit: str) -> str:
            delta = ch.get(key)
            trend = "" if delta is None else f" ({'+' if delta > 0 else ''}{delta}% vs the previous {rep['window_days']} days)"
            return f"- {label}: {cur[key]:,} {unit}{trend}"

        ap, pe = rep["decisions"]["approved"], rep["decisions"]["pending"]
        lines = [
            "Hello management,",
            "",
            f"Waste and energy of the Vidad plant, {cur['from']} to {cur['to']}:",
            "",
            line("Molding scrap", "scrap_kg", "kg"),
            f"- Scrap rate: {cur['scrap_rate_pct']}% · resin value lost Rp{cur['scrap_idr']:,}",
            line("Electricity", "kwh", "kWh"),
            line("CO2e", "co2e_kg", "kg"),
            f"- Energy per good bumper: {cur['kwh_per_good_unit']} kWh",
            "",
            f"Approved agent decisions ({ap['count']}) save per month: {ap['scrap_kg']:,} kg scrap, {ap['kwh']:,} kWh, "
            f"{ap['co2e_kg']:,} kg CO2e, Rp{ap['rupiah_idr']:,}.",
            f"Waiting for approval ({pe['count']}): {pe['scrap_kg']:,} kg scrap, {pe['kwh']:,} kWh, {pe['co2e_kg']:,} kg CO2e.",
            "",
            f"Source: {rep['source']}.",
            *SIGNATURE,
        ]
        mail = {
            "to": [address("gm")],
            "cc": [address("plant-manager")],
            "subject": f"[Vidad] Sustainability report {cur['from']} to {cur['to']}",
            "body": "\n".join(lines),
        }
        return _save(session_id, mail, kind="sustainability", action_id=None, plan_id=None, agent="orchestrator", roles=["gm", "plant-manager"], actor=actor)

    return _guarded("the sustainability report", run)


def registration_request(account: Doc) -> Doc | None:
    def run() -> Doc:
        lines = [
            "Hello admin,",
            "",
            f"{account['name']} ({account['email']}) signed up with Google and waits for approval:",
            "",
            f"- Factory: {account['factory_name']}",
            f"- Industry: {account['industry']}",
            f"- Role: {account['role']}",
            f"- City: {account.get('city') or '-'} · Scale: {account.get('plant_scale') or '-'}",
            "",
            "Approve or reject it on the Registrations page of Factory Vision AI.",
            *SIGNATURE,
        ]
        mail = {"to": [address("admin")], "cc": [], "subject": f"[Factory Vision AI] New registration: {account['factory_name']}", "body": "\n".join(lines)}
        return _save(f"account:{account['uid']}", mail, kind="registration", action_id=None, plan_id=None, agent="onboarding")

    return _guarded("the registration request", run)


def registration_decision(account: Doc) -> Doc | None:
    def run() -> Doc:
        approved = account["status"] == "approved"
        lines = [
            f"Hello {account['name']},",
            "",
            (
                f"Your Factory Vision AI account for {account['factory_name']} is approved. Sign in with Google and the onboarding "
                "guide opens: apply the car-bumper plant template, review the master data, then run your first workflow."
                if approved
                else f"Your Factory Vision AI registration for {account['factory_name']} was not approved."
                + (f" Note from the admin: {account['note']}" if account.get("note") else "")
            ),
            *SIGNATURE,
        ]
        subject = "[Factory Vision AI] Your account is approved" if approved else "[Factory Vision AI] Your registration"
        mail = {"to": [account["email"]], "cc": [], "subject": subject, "body": "\n".join(lines)}
        return _save(f"account:{account['uid']}", mail, kind="registration_decision", action_id=None, plan_id=None, agent="onboarding")

    return _guarded("the registration decision", run)


def list_emails(session_id: str) -> list[Doc]:
    return sorted(store().where(COLLECTION, "session_id", session_id), key=lambda d: str(d["created_at"]), reverse=True)


def get(session_id: str, email_id: str) -> Doc:
    doc = store().get(COLLECTION, email_id)
    if not doc or doc.get("session_id") != session_id:
        raise FVError(UNKNOWN_ENTITY, f"unknown email {email_id}", {"email_id": email_id})
    return doc
