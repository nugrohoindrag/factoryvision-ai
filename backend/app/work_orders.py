"""Work Orders and the Production Board (module 3): running and dispatching the work orders of confirmed plans.

Work orders are born in the plan wizard (`app.plans`, collection `plan_work_orders`) and the wizard owns them while
the plan is open. Once the plan is confirmed they belong to the shop floor: released, started, completed or
cancelled through one state machine, rescheduled and moved between machines from the board, split across machines,
and their output reported here (the stand-in for the operator terminal).

The board is a projection, not a store: every bar is a work order or an approved maintenance window, and conflicts
are computed on read, because a stored conflict is a claim about a schedule that has since changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app import capacity, effects, plans, plant
from app.errors import FVError
from app.store import store
from app.tools import material
from app.tools.common import TODAY, to_date

INVALID_REQUEST = "FV-REQ-001"
CLASH = "FV-WO-001"
NOT_SPLITTABLE = "FV-WO-002"
NOT_DISPATCHABLE = "FV-WO-003"

STATUSES = (*plans.WO_STATUSES, "Cancelled")
FINAL = ("Completed", "Cancelled")
EXECUTING = ("Confirmed", "In Execution")  # plan statuses in which work orders run
EDITABLE = tuple(s for s in plans.STATUSES if s not in plans.LOCKED)  # the wizard owns the work orders
# Draft -> Confirmed is the wizard's (confirm work orders); everything after it happens on the floor.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "Draft": (),
    "Confirmed": ("Released", "In Production", "Cancelled"),
    "Released": ("In Production", "Confirmed", "Cancelled"),
    "In Production": ("Completed", "Cancelled"),
    "Completed": (),
    "Cancelled": (),
}
ACTION_LABEL = {
    "Released": "Release to shop floor",
    "Confirmed": "Recall from shop floor",
    "In Production": "Start",
    "Completed": "Complete",
    "Cancelled": "Cancel",
}
SPLITTABLE = ("Confirmed", "Released", "In Production")
AT_RISK_POINTS = 0.10  # ten points behind the clock is when a supervisor can still act; further behind it is late
MAX_PRIORITY = plans.MAX_PRIORITY
VIEWS = ("machine", "process", "shift", "day")
BOARD_MACHINES = (*plant.IMM_IDS, *plans.PAINT_MACHINES, *plans.ASSEMBLY_MACHINES)
PROCESSES = ("Molding", "Painting", "Assembly")
DAY_START_HOUR = 7


@dataclass
class Floor:
    """What the shop floor looks like now: the plant clock, approved maintenance, and the twin's machine states."""

    now: datetime = plant.DEMO_START
    maintenance: list[str] = field(default_factory=list)
    machine_status: dict[str, str] = field(default_factory=dict)
    overrides: effects.Overrides | None = None
    strict_sequence: bool = False  # the capacity setting: a process starts only once every earlier one is completed

    @classmethod
    def of(cls, o: effects.Overrides, *, now: datetime = plant.DEMO_START, machine_status: dict[str, str] | None = None) -> Floor:
        return cls(now=now, maintenance=list(o.maintenance), machine_status=machine_status or {}, overrides=o, strict_sequence=o.strict_process_sequence)

    def maintenance_window(self, mid: str) -> tuple[datetime, datetime] | None:
        """An approved maintenance job blocks the first two hours of today, as the scheduler books it."""
        if mid not in self.maintenance:
            return None
        start = datetime(TODAY.year, TODAY.month, TODAY.day, DAY_START_HOUR, tzinfo=plant.WIB)
        return start, start + timedelta(minutes=capacity.MAINTENANCE_DOWNTIME_MIN)

    def under_maintenance(self, mid: str | None) -> bool:
        if not mid:
            return False
        win = self.maintenance_window(mid)
        return self.machine_status.get(mid) == "Maintenance" or (win is not None and win[0] <= self.now < win[1])

    def down(self, mid: str | None) -> bool:
        return bool(mid) and self.machine_status.get(str(mid)) == "Downtime"


def _now() -> str:
    return plans.now_iso()


def _fields(errors: list[dict[str, str]], headline: str | None = None) -> FVError:
    return FVError(INVALID_REQUEST, headline or errors[0]["message"], {"fields": errors})


def _dt(v: object) -> datetime | None:
    return plans.parse_dt(str(v)) if v else None


# --- storage --------------------------------------------------------------------------------------------------------


def _norm(w: dict[str, Any]) -> dict[str, Any]:
    for k, v in (("output_qty", 0), ("reject_qty", 0), ("scrap_qty", 0), ("actual_start", None), ("actual_end", None), ("cancel_reason", None)):
        w.setdefault(k, v)
    for k in ("history", "children"):
        w.setdefault(k, [])
    w.setdefault("parent_wo_id", None)
    return w


def session_wos(sid: str) -> dict[str, dict[str, Any]]:
    return {w["wo_id"]: _norm(w) for w in store().where(plans.WOS, "session_id", sid)}


def _save(sid: str, w: dict[str, Any]) -> None:
    plans.save_work_order(sid, w)


def _get(by_id: dict[str, dict[str, Any]], wo_id: str) -> dict[str, Any]:
    w = by_id.get(wo_id)
    if w is None:
        raise FVError(plans.WO_NOT_FOUND, f"Work order {wo_id} does not exist in this session.", {"wo_id": wo_id})
    return w


def _plans_by_id(sid: str) -> dict[str, dict[str, Any]]:
    return {p["plan_id"]: p for p in store().where(plans.PLANS, "session_id", sid)}


def _log(w: dict[str, Any], user: str, action: str, detail: str, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None) -> None:
    w["history"].append({"at": _now(), "by": user, "action": action, "detail": detail, "before": before, "after": after})


def _refresh_plan(sid: str, plan_id: str) -> None:
    p = plans.load_plan(sid, plan_id)
    new = plans.derived_status(p, plans.plan_work_orders(sid, plan_id))
    if new != p["status"]:
        p["status"] = new
        plans.save_plan(p)


# --- the process chain ----------------------------------------------------------------------------------------------


def effective(by_id: dict[str, dict[str, Any]], wo_id: str | None) -> list[dict[str, Any]]:
    """A work order, or its parts once it was split: the parts are what actually run and hand over."""
    w = by_id.get(wo_id or "")
    if w is None:
        return []
    return [by_id[c] for c in w["children"] if c in by_id] or [w]


def processed(w: dict[str, Any]) -> int:
    return int(w["output_qty"]) + int(w["reject_qty"]) + int(w["scrap_qty"])


def available_qty(by_id: dict[str, dict[str, Any]], w: dict[str, Any]) -> int | None:
    """What the previous process has handed over (its good output) and this one has not yet taken in; None at the first."""
    preds = effective(by_id, w["predecessor_wo_id"])
    if not preds:
        return None
    mine = processed(w) if not w["parent_wo_id"] else sum(processed(by_id[c]) for c in by_id[w["parent_wo_id"]]["children"] if c in by_id)
    return max(0, sum(int(p["output_qty"]) for p in preds) - mine)


def successors(by_id: dict[str, dict[str, Any]], w: dict[str, Any]) -> list[dict[str, Any]]:
    head = w["parent_wo_id"] or w["wo_id"]
    return [x for x in by_id.values() if x["predecessor_wo_id"] == head and not x["parent_wo_id"]]


def _roll_up(by_id: dict[str, dict[str, Any]], parent: dict[str, Any]) -> None:
    """A split parent holds no production of its own: its counters and status are its parts'."""
    kids = [by_id[c] for c in parent["children"] if c in by_id]
    if not kids:
        return
    for k in ("output_qty", "reject_qty", "scrap_qty"):
        parent[k] = sum(int(c[k]) for c in kids)
    st = {c["status"] for c in kids}
    if st <= set(FINAL):
        parent["status"] = "Completed" if "Completed" in st else "Cancelled"
    elif st & {"In Production", "Completed"}:
        parent["status"] = "In Production"
    elif "Released" in st:
        parent["status"] = "Released"
    else:
        parent["status"] = "Confirmed"
    starts = [c["actual_start"] for c in kids if c["actual_start"]]
    parent["actual_start"] = min(starts) if starts else None
    parent["actual_end"] = max(c["actual_end"] for c in kids if c["actual_end"]) if st <= set(FINAL) and any(c["actual_end"] for c in kids) else None


# --- schedule clashes -----------------------------------------------------------------------------------------------


def _window(w: dict[str, Any]) -> tuple[datetime, datetime] | None:
    s, e = _dt(w.get("planned_start")), _dt(w.get("planned_end"))
    return (s, e) if s and e else None


def _overlap(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _on_board(w: dict[str, Any]) -> bool:
    return not w["children"] and w["status"] != "Cancelled" and _window(w) is not None


def clashes(by_id: dict[str, dict[str, Any]], w: dict[str, Any], machine: str | None, win: tuple[datetime, datetime]) -> list[dict[str, Any]]:
    """Other live work orders booked on the machine in the window (a split parent and its parts never clash with each other)."""
    if not machine:
        return []
    family = {w["wo_id"], w["parent_wo_id"], *w["children"]}
    out = []
    for o in by_id.values():
        if o["wo_id"] in family or o["status"] in FINAL or not _on_board(o) or o.get("machine_id") != machine:
            continue
        ow = _window(o)
        if ow and _overlap(win, ow):
            out.append(o)
    return out


def _clash_text(o: dict[str, Any]) -> str:
    return f"{o['wo_id']} ({o['plan_number']}) {o['planned_start'][:16].replace('T', ' ')} to {o['planned_end'][:16].replace('T', ' ')}"


# --- state machine --------------------------------------------------------------------------------------------------


def evaluate(by_id: dict[str, dict[str, Any]], plan: dict[str, Any], w: dict[str, Any], to: str, floor: Floor, reason: str = "") -> list[str]:
    """Every unmet condition for moving w to `to`; empty when the move is allowed."""
    cur = w["status"]
    if w["children"]:
        return [f"{w['wo_id']} was split into {', '.join(w['children'])}; act on its parts."]
    if to == cur:
        return [f"The work order is already {cur}."]
    if plan["status"] not in EXECUTING and cur not in FINAL:
        if plan["status"] in EDITABLE:
            return [
                f"Production plan {plan['plan_number']} is still {plan['status']}: change its work orders in the plan wizard; they move on the floor once the plan is confirmed."
            ]
        return [f"Production plan {plan['plan_number']} is {plan['status']}, so its work orders no longer change."]
    if to not in TRANSITIONS.get(cur, ()):
        allowed = ", ".join(TRANSITIONS.get(cur, ())) or ("nothing: it is final" if cur in FINAL else "Confirmed, in the plan wizard")
        return [f"{cur} -> {to} is not a step of the work order lifecycle. From {cur} it can go to {allowed}."]
    reasons: list[str] = []
    if to == "Cancelled":
        if len(reason.strip()) < 3:
            reasons.append("Give a cancellation reason of at least 3 characters.")
        return reasons
    if to == "Confirmed":  # recall from the floor: backward moves need nothing
        return reasons
    mid = w.get("machine_id")
    if to == "Released" or (to == "In Production" and cur == "Confirmed"):
        reasons.extend(plans.wo_checklist(w, []))
        win = _window(w)
        if win:
            reasons.extend(f"Open conflict must be resolved: {mid} is also booked by {_clash_text(o)}." for o in clashes(by_id, w, mid, win))
    if to == "In Production":
        if floor.under_maintenance(mid):
            reasons.append(f"{mid} is under maintenance and takes no work until it is back.")
        elif floor.down(mid):
            reasons.append(f"{mid} is down ({floor.machine_status.get(str(mid))}); start once it runs again.")
        busy = [
            o["wo_id"]
            for o in by_id.values()
            if o["wo_id"] != w["wo_id"] and o["status"] == "In Production" and not o["children"] and o.get("machine_id") == mid
        ]
        if busy:
            reasons.append(f"{mid} is already running {', '.join(busy)}; complete it first.")
        if floor.strict_sequence:
            earlier = sorted(
                o["wo_id"]
                for o in by_id.values()
                if o["plan_id"] == w["plan_id"]
                and o["line_id"] == w["line_id"]
                and o["routing_step"] < w["routing_step"]
                and not o["children"]
                and o["status"] != "Completed"
            )
            if earlier:
                reasons.append(f"Strict process sequence is on: the earlier process ({', '.join(earlier)}) is not completed.")
        # Strict off allows any order; what a process can take in is still capped when output is reported.
    if to == "Completed":
        if processed(w) <= 0:
            reasons.append("No output reported yet. Report what was produced before completing.")
        if floor.down(mid):
            reasons.append(f"{mid} has an open downtime; close it before completing the work order.")
    return reasons


def transitions_for(by_id: dict[str, dict[str, Any]], plan: dict[str, Any], w: dict[str, Any], floor: Floor) -> list[dict[str, Any]]:
    out = []
    for to in TRANSITIONS.get(w["status"], ()):
        why = evaluate(by_id, plan, w, to, floor, reason="xxx")  # the reason is asked for when cancelling
        out.append({"to": to, "label": ACTION_LABEL[to], "allowed": not why, "reasons": why})
    return out


def transition(sid: str, wo_id: str, to: str, *, reason: str = "", user: str, floor: Floor) -> dict[str, Any]:
    by_id = session_wos(sid)
    w = _get(by_id, wo_id)
    plan = plans.load_plan(sid, w["plan_id"])
    if to not in STATUSES:
        raise FVError(plans.WO_TRANSITION, f"{to} is not a work order status.", {"valid": list(STATUSES)})
    why = evaluate(by_id, plan, w, to, floor, reason)
    if why:
        raise FVError(
            plans.WO_TRANSITION, f"Work order {wo_id} cannot move from {w['status']} to {to}: {' '.join(why)}", {"from": w["status"], "to": to, "reasons": why}
        )
    before = w["status"]
    w["status"] = to
    stamp = floor.now.isoformat(timespec="minutes")
    if to == "In Production":
        w["actual_start"] = w["actual_start"] or stamp
    elif to == "Completed":
        w["actual_end"] = stamp
    elif to == "Cancelled":
        w["cancel_reason"] = reason.strip()
    _log(w, user, ACTION_LABEL[to], reason.strip() if to == "Cancelled" else f"{before} -> {to}", {"status": before}, {"status": to})
    _save(sid, w)
    if w["parent_wo_id"]:
        parent = by_id[w["parent_wo_id"]]
        _roll_up(by_id, parent)
        _save(sid, parent)
    _refresh_plan(sid, w["plan_id"])
    return detail(sid, wo_id, floor)


# --- output ---------------------------------------------------------------------------------------------------------


def report_output(sid: str, wo_id: str, *, output_qty: int, reject_qty: int, scrap_qty: int, user: str, floor: Floor) -> dict[str, Any]:
    """Good, reject and scrap pieces of a running work order; a later process cannot take in more than the one before handed over."""
    by_id = session_wos(sid)
    w = _get(by_id, wo_id)
    if w["children"]:
        raise FVError(NOT_DISPATCHABLE, f"{wo_id} was split into {', '.join(w['children'])}; report output on its parts.")
    if w["status"] != "In Production":
        raise FVError(plans.WO_TRANSITION, f"Output is reported only while a work order is In Production; {wo_id} is {w['status']}.", {"status": w["status"]})
    q = {"output_qty": output_qty, "reject_qty": reject_qty, "scrap_qty": scrap_qty}
    errs = [{"field": k, "message": "Must be 0 or more."} for k, v in q.items() if v < 0]
    if not errs and sum(q.values()) <= 0:
        errs.append({"field": "output_qty", "message": "Report at least one piece (good, reject or scrap)."})
    avail = available_qty(by_id, w)
    if not errs and avail is not None and sum(q.values()) > avail:
        preds = ", ".join(p["wo_id"] for p in effective(by_id, w["predecessor_wo_id"]))
        errs.append(
            {"field": "output_qty", "message": f"Only {avail:,} pcs have been handed over by {preds} and not yet processed here; {sum(q.values()):,} is more."}
        )
    if errs:
        raise _fields(errs)
    before = {k: w[k] for k in q}
    for k, v in q.items():
        w[k] = int(w[k]) + v
    _log(w, user, "Output reported", f"+{output_qty:,} good, +{reject_qty:,} reject, +{scrap_qty:,} scrap", before, {k: w[k] for k in q})
    _save(sid, w)
    if w["parent_wo_id"]:
        parent = by_id[w["parent_wo_id"]]
        _roll_up(by_id, parent)
        _save(sid, parent)
    return detail(sid, wo_id, floor)


# --- split ----------------------------------------------------------------------------------------------------------


def _suffix(k: int) -> str:
    out = ""
    while True:
        out = chr(ord("A") + k % 26) + out
        k = k // 26 - 1
        if k < 0:
            return out


def split(sid: str, wo_id: str, parts: list[dict[str, Any]], *, user: str, floor: Floor) -> dict[str, Any]:
    """Divide a work order across machines to finish sooner. The parts sum exactly to the parent, which keeps no production of its own."""
    by_id = session_wos(sid)
    w = _get(by_id, wo_id)
    plan = plans.load_plan(sid, w["plan_id"])
    if plan["status"] not in EXECUTING:
        raise FVError(NOT_SPLITTABLE, f"Production plan {plan['plan_number']} is {plan['status']}; split work orders once the plan is confirmed.")
    if w["children"]:
        raise FVError(NOT_SPLITTABLE, f"{wo_id} was already split into {', '.join(w['children'])}. Split one of its parts if it needs dividing again.")
    if w["parent_wo_id"]:
        raise FVError(NOT_SPLITTABLE, f"{wo_id} is already a part of {w['parent_wo_id']}; a part is not split again.")
    if w["status"] not in SPLITTABLE:
        raise FVError(NOT_SPLITTABLE, f"A {w['status']} work order cannot be split; only {', '.join(SPLITTABLE)} can.", {"status": w["status"]})
    if processed(w) > 0:
        raise FVError(
            NOT_SPLITTABLE,
            f"{wo_id} has already reported production ({w['output_qty']:,} good, {w['reject_qty']:,} reject, {w['scrap_qty']:,} scrap). "
            "A split parent may not hold production of its own, or the parts' output would be counted twice.",
        )
    win = _window(w)
    if win is None:
        raise FVError(NOT_SPLITTABLE, f"{wo_id} has no schedule to divide.")
    errs: list[dict[str, str]] = []
    if len(parts) < 2:
        errs.append({"field": "parts", "message": "A split makes at least 2 parts."})
    for k, part in enumerate(parts):
        if int(part.get("qty") or 0) <= 0:
            errs.append({"field": f"parts[{k}].qty", "message": "Must be more than zero."})
        mid = part.get("machine_id") or w["machine_id"]
        if mid not in w["compatible_machines"]:
            errs.append(
                {
                    "field": f"parts[{k}].machine_id",
                    "message": f"{mid} cannot run {w['process'].lower()} for {w['sku']}; use {', '.join(w['compatible_machines'])}.",
                }
            )
    total = sum(int(p.get("qty") or 0) for p in parts)
    if not errs and total != int(w["qty"]):
        errs.append(
            {"field": "parts", "message": f"The parts add up to {total:,}, not the {int(w['qty']):,} of {wo_id}: a split divides the work order exactly."}
        )
    if errs:
        raise _fields(errs)
    span = win[1] - win[0]
    free: dict[str, datetime] = {}
    kids = []
    status = w["status"] if w["status"] in ("Confirmed", "Released") else "Released"
    for k, part in enumerate(parts):
        mid = str(part.get("machine_id") or w["machine_id"])
        key = str(w["mold_id"]) if w["process"] == "Molding" and w.get("mold_id") else mid  # one mold runs on one machine at a time
        start = max(win[0], free.get(key, win[0]))
        end = start + span * (int(part["qty"]) / int(w["qty"]))
        free[key] = end
        kid = {
            **{k2: v for k2, v in w.items() if k2 not in ("history", "children")},
            "wo_id": f"{wo_id}-{_suffix(k)}",
            "qty": int(part["qty"]),
            "machine_id": mid,
            "planned_start": start.isoformat(timespec="minutes"),
            "planned_end": end.isoformat(timespec="minutes"),
            "shift": plant.shift_of(start),
            "status": status,
            "confirmed": True,
            "parent_wo_id": wo_id,
            "children": [],
            "history": [],
            "actual_start": None,
            "actual_end": None,
            "late_minutes": 0,
            "created_at": _now(),
        }
        if kid["wo_id"] in by_id:
            raise FVError(NOT_SPLITTABLE, f"{kid['wo_id']} already exists.")
        _log(kid, user, "Split", f"Part {_suffix(k)} of {wo_id}: {int(part['qty']):,} pcs on {mid}")
        kids.append(kid)
    for kid in kids:
        _save(sid, kid)
        by_id[kid["wo_id"]] = kid
    w["children"] = [k["wo_id"] for k in kids]
    _log(w, user, "Split", ", ".join(f"{k['wo_id']} {k['qty']:,} pcs on {k['machine_id']}" for k in kids))
    _save(sid, w)
    return detail(sid, wo_id, floor)


# --- dispatch (board) -----------------------------------------------------------------------------------------------

DISPATCH_ACTIONS = ("RESCHEDULE", "REASSIGN_MACHINE", "REPRIORITISE")


def _dispatchable(plan: dict[str, Any], w: dict[str, Any]) -> None:
    if plan["status"] not in EXECUTING:
        raise FVError(
            NOT_DISPATCHABLE,
            f"{w['wo_id']} belongs to {plan['plan_number']}, which is {plan['status']}. "
            + ("Change its schedule in the plan wizard." if plan["status"] in EDITABLE else "Its work orders no longer change."),
            {"plan_id": plan["plan_id"], "plan_status": plan["status"]},
        )
    if w["children"]:
        raise FVError(NOT_DISPATCHABLE, f"{w['wo_id']} was split into {', '.join(w['children'])}; dispatch its parts.")
    if w["status"] in FINAL:
        raise FVError(NOT_DISPATCHABLE, f"{w['wo_id']} is {w['status']} and no longer changes.")


def _assert_free(by_id: dict[str, dict[str, Any]], w: dict[str, Any], machine: str | None, win: tuple[datetime, datetime], floor: Floor) -> None:
    mwin = floor.maintenance_window(machine) if machine else None
    if mwin and _overlap(win, mwin):
        raise FVError(
            CLASH,
            f"{machine} is booked for maintenance {mwin[0]:%Y-%m-%d %H:%M} to {mwin[1]:%H:%M}; a machine under maintenance takes no new work in that window.",
            {"machine_id": machine},
        )
    hit = clashes(by_id, w, machine, win)
    if hit:
        raise FVError(
            CLASH, f"The schedule clashes on {machine} with {_clash_text(hit[0])}.", {"machine_id": machine, "work_orders": [o["wo_id"] for o in hit]}
        )


def dispatch(sid: str, body: dict[str, Any], *, user: str, floor: Floor) -> dict[str, Any]:
    """The dispatcher's changes from the board: reschedule, move to another machine, reprioritise. Each one is logged."""
    action = str(body.get("action") or "")
    if action not in DISPATCH_ACTIONS:
        raise _fields(
            [{"field": "action", "message": f"Use one of {', '.join(DISPATCH_ACTIONS)}; release, start, complete and cancel go through the work order status."}]
        )
    by_id = session_wos(sid)
    w = _get(by_id, str(body.get("wo_id") or ""))
    plan = plans.load_plan(sid, w["plan_id"])
    _dispatchable(plan, w)
    before = {k: w.get(k) for k in ("planned_start", "planned_end", "machine_id", "priority", "shift")}
    reason = str(body.get("reason") or "").strip()
    if action == "RESCHEDULE":
        s, e = _dt(body.get("planned_start")), _dt(body.get("planned_end"))
        errs = []
        if s is None:
            errs.append({"field": "planned_start", "message": "A new start is required."})
        if e is None:
            errs.append({"field": "planned_end", "message": "A new end is required."})
        elif s and e <= s:
            errs.append({"field": "planned_end", "message": "Planned end must be after planned start."})
        if errs or s is None or e is None:
            raise _fields(errs)
        _assert_free(by_id, w, w.get("machine_id"), (s, e), floor)
        w.update(planned_start=s.isoformat(timespec="minutes"), planned_end=e.isoformat(timespec="minutes"), shift=plant.shift_of(s))
        detail_text = f"{before['planned_start'] or '-'} -> {w['planned_start']}"
    elif action == "REASSIGN_MACHINE":
        mid = str(body.get("machine_id") or "")
        if mid not in w["compatible_machines"]:
            raise _fields(
                [
                    {
                        "field": "machine_id",
                        "message": f"{mid or 'That machine'} cannot run {w['process'].lower()} for {w['sku']}; use {', '.join(w['compatible_machines'])}.",
                    }
                ]
            )
        if floor.under_maintenance(mid):
            raise FVError(CLASH, f"{mid} is under maintenance and takes no new work.", {"machine_id": mid})
        win = _window(w)
        if win:
            _assert_free(by_id, w, mid, win, floor)
        w["machine_id"] = mid
        detail_text = f"{before['machine_id'] or '-'} -> {mid}"
    else:
        try:
            prio = int(body.get("priority"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            prio = 0
        if not 1 <= prio <= MAX_PRIORITY:
            raise _fields([{"field": "priority", "message": f"Priority must be between 1 and {MAX_PRIORITY}."}])
        w["priority"] = prio
        detail_text = f"{before['priority']} -> {prio}"
    after = {k: w.get(k) for k in before}
    _log(w, user, action.replace("_", " ").title(), f"{detail_text}{f' ({reason})' if reason else ''}", before, after)
    _save(sid, w)
    return detail(sid, w["wo_id"], floor)


# --- views ----------------------------------------------------------------------------------------------------------


def board_status(w: dict[str, Any], floor: Floor) -> str:
    """What a bar looks like at a glance. Delayed and At Risk are the two the clock decides."""
    st = w["status"]
    if st in FINAL:
        return str(st)
    if floor.under_maintenance(w.get("machine_id")):
        return "Maintenance"
    win = _window(w)
    if win is None:
        return "Unscheduled"
    if st == "In Production":
        if floor.now > win[1]:
            return "Delayed"
        elapsed = (floor.now - win[0]) / max(win[1] - win[0], timedelta(minutes=1))
        progress = int(w["output_qty"]) / int(w["qty"]) if w["qty"] else 0.0
        return "At Risk" if elapsed - progress > AT_RISK_POINTS else "Running"
    if floor.now > win[1]:
        return "Delayed"
    return "Scheduled" if st == "Draft" else str(st)


def row(w: dict[str, Any], by_id: dict[str, dict[str, Any]], plan: dict[str, Any] | None, floor: Floor) -> dict[str, Any]:
    qty = int(w["qty"])
    return {
        **{k: v for k, v in w.items() if k not in ("history", "session_id")},
        "product_name": capacity.product_name(w["sku"]),
        "plan_status": plan["status"] if plan else None,
        "executing": bool(plan and plan["status"] in EXECUTING),
        "progress_pct": round(100 * int(w["output_qty"]) / qty, 1) if qty else 0.0,
        "board_status": board_status(w, floor),
        "available_qty": available_qty(by_id, w),
    }


def list_work_orders(
    sid: str, floor: Floor, *, status: str | None = None, process: str | None = None, plan_id: str | None = None, q: str | None = None
) -> dict[str, Any]:
    by_id = session_wos(sid)
    pl = _plans_by_id(sid)
    rows = [row(w, by_id, pl.get(w["plan_id"]), floor) for w in by_id.values()]
    rows.sort(key=lambda r: (r["planned_start"] or "9999", r["routing_step"], r["wo_id"]))
    total = len(rows)
    counts = {s: sum(1 for r in rows if r["status"] == s and not r["children"]) for s in STATUSES}
    if status:
        rows = [r for r in rows if r["status"] == status]
    if process:
        rows = [r for r in rows if r["process"] == process]
    if plan_id:
        rows = [r for r in rows if r["plan_id"] == plan_id]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r["wo_id"].lower() or needle in r["sku"].lower() or needle in str(r.get("machine_id") or "").lower()]
    return {
        "as_of": TODAY.isoformat(),
        "now": floor.now.isoformat(timespec="minutes"),
        "work_orders": rows,
        "total": total,
        "counts": counts,
        "statuses": list(STATUSES),
        "processes": list(PROCESSES),
        "plans": sorted(
            ({"plan_id": p["plan_id"], "plan_number": p["plan_number"], "status": p["status"]} for p in pl.values()), key=lambda p: p["plan_number"]
        ),
        "source": "work orders of this session's production plans (plan_work_orders); output reported here",
    }


def detail(sid: str, wo_id: str, floor: Floor) -> dict[str, Any]:
    by_id = session_wos(sid)
    w = _get(by_id, wo_id)
    plan = plans.load_plan(sid, w["plan_id"])
    preds = effective(by_id, w["predecessor_wo_id"])
    demand = [
        {k: d[k] for k in ("order_number", "customer_name", "sku", "qty", "requested_delivery_date")} for d in plan["demand"] if d["line_id"] == w["line_id"]
    ]
    can_split = plan["status"] in EXECUTING and not w["children"] and not w["parent_wo_id"] and w["status"] in SPLITTABLE and processed(w) == 0
    return {
        **row(w, by_id, plan, floor),
        "plan_number": plan["plan_number"],
        "history": list(reversed(w["history"])),
        "predecessors": [{k: p[k] for k in ("wo_id", "process", "status", "output_qty")} for p in preds],
        "successors": [{k: s[k] for k in ("wo_id", "process", "status")} for s in successors(by_id, w)],
        "children_detail": [row(by_id[c], by_id, plan, floor) for c in w["children"] if c in by_id],
        "demand": demand,
        "transitions": transitions_for(by_id, plan, w, floor),
        "can_split": can_split,
        "dispatchable": plan["status"] in EXECUTING and not w["children"] and w["status"] not in FINAL,
        "max_priority": MAX_PRIORITY,
    }


# --- board ----------------------------------------------------------------------------------------------------------


def _day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, DAY_START_HOUR, tzinfo=plant.WIB)


def _stack(items: list[dict[str, Any]]) -> int:
    """Give overlapping bars in one lane their own row, so none hides another; returns the number of rows."""
    ends: list[datetime] = []
    for it in sorted(items, key=lambda i: i["planned_start"]):
        s = _dt(it["planned_start"]) or datetime.min.replace(tzinfo=plant.WIB)
        k = next((r for r, e in enumerate(ends) if e <= s), None)
        if k is None:
            ends.append(datetime.min.replace(tzinfo=plant.WIB))
            k = len(ends) - 1
        ends[k] = _dt(it["planned_end"]) or s
        it["row"] = k
    return max(1, len(ends))


def _resin_stockout(floor: Floor) -> date | None:
    o = floor.overrides
    receipts = [r for r in (o.extra_receipts if o else []) if r.get("material_id", "PP-EPDM-T20") == "PP-EPDM-T20"]
    try:
        proj = material.stock_projection(demand_change_pct=(o.demand_change_pct if o else None) or None, extra_receipts=receipts or None)
    except FVError:
        return None
    return to_date(proj["stockout_date"]) if proj.get("stockout_date") else None


def board(
    sid: str, floor: Floor, *, view: str = "machine", day: str | None = None, days: int = 1, process: str | None = None, sku: str | None = None
) -> dict[str, Any]:
    if view not in VIEWS:
        raise _fields([{"field": "view", "message": f"Use one of {', '.join(VIEWS)}."}])
    if days not in (1, 3, 7):
        raise _fields([{"field": "days", "message": "Show 1, 3 or 7 days."}])
    try:
        anchor = to_date(day) if day else TODAY
    except ValueError:
        raise _fields([{"field": "date", "message": "Not a date (YYYY-MM-DD)."}]) from None
    ws = _day_start(anchor)
    we = ws + timedelta(days=days)
    by_id = session_wos(sid)
    pl = _plans_by_id(sid)
    items: list[dict[str, Any]] = []
    for w in by_id.values():
        if not _on_board(w) or (process and w["process"] != process) or (sku and w["sku"] != sku):
            continue
        win = _window(w)
        if not win or not _overlap(win, (ws, we)):
            continue
        p = pl.get(w["plan_id"])
        items.append(
            {
                "id": w["wo_id"],
                "kind": "WORK_ORDER",
                "label": w["wo_id"],
                "wo_id": w["wo_id"],
                "plan_id": w["plan_id"],
                "plan_number": w["plan_number"],
                "plan_status": p["status"] if p else None,
                "executing": bool(p and p["status"] in EXECUTING),
                "sku": w["sku"],
                "product_name": capacity.product_name(w["sku"]),
                "process": w["process"],
                "machine_id": w.get("machine_id"),
                "mold_id": w.get("mold_id"),
                "shift": w.get("shift"),
                "planned_start": w["planned_start"],
                "planned_end": w["planned_end"],
                "actual_start": w["actual_start"],
                "actual_end": w["actual_end"],
                "qty": int(w["qty"]),
                "output_qty": int(w["output_qty"]),
                "progress_pct": round(100 * int(w["output_qty"]) / int(w["qty"]), 1) if w["qty"] else 0.0,
                "priority": w.get("priority"),
                "status": w["status"],
                "board_status": board_status(w, floor),
                "late_minutes": int(w.get("late_minutes") or 0),
                "material_status": None,
                "conflicts": [],
            }
        )
    for mid in floor.maintenance:
        mwin = floor.maintenance_window(mid)
        if mwin and _overlap(mwin, (ws, we)) and (not process or (mid in plant.IMM_IDS and process == "Molding")):
            items.append(
                {
                    "id": f"MNT-{mid}",
                    "kind": "MAINTENANCE",
                    "label": f"Maintenance · {mid}",
                    "machine_id": mid,
                    "process": "Molding" if mid in plant.IMM_IDS else None,
                    "planned_start": mwin[0].isoformat(timespec="minutes"),
                    "planned_end": mwin[1].isoformat(timespec="minutes"),
                    "status": "Maintenance",
                    "board_status": "Maintenance",
                    "shift": plant.shift_of(mwin[0]),
                    "conflicts": [],
                }
            )
    conflicts = _conflicts(items, by_id, floor)
    return {
        "view": view,
        "window_start": ws.isoformat(timespec="minutes"),
        "window_end": we.isoformat(timespec="minutes"),
        "now": floor.now.isoformat(timespec="minutes"),
        "days": days,
        "lanes": _lanes(view, items),
        "conflicts": conflicts,
        "summary": {
            "work_orders": sum(1 for i in items if i["kind"] == "WORK_ORDER"),
            "running": sum(1 for i in items if i["board_status"] in ("Running", "At Risk")),
            "late_or_at_risk": sum(1 for i in items if i["board_status"] in ("Delayed", "At Risk")),
            "blocking": sum(1 for c in conflicts if c["blocking"]),
            "warnings": sum(1 for c in conflicts if not c["blocking"]),
        },
        "generated_at": _now(),
        "source": "work orders of this session's production plans; maintenance approved in the Action Center; plant clock from the digital twin",
    }


def _conflicts(items: list[dict[str, Any]], by_id: dict[str, dict[str, Any]], floor: Floor) -> list[dict[str, Any]]:
    """Machine, mold and maintenance clashes are blocking (execution is physically impossible); delivery, sequence and material risks are warnings."""
    out: list[dict[str, Any]] = []

    def record(kind: str, message: str, ids: list[str], *, blocking: bool) -> None:
        c = {"type": kind, "blocking": blocking, "message": message, "item_ids": ids}
        out.append(c)
        for it in items:
            if it["id"] in ids:
                it["conflicts"].append(c)

    def block(kind: str, message: str, ids: list[str]) -> None:
        record(kind, message, ids, blocking=True)

    def warn(kind: str, message: str, ids: list[str]) -> None:
        record(kind, message, ids, blocking=False)

    def win(it: dict[str, Any]) -> tuple[datetime, datetime]:
        s, e = _dt(it["planned_start"]), _dt(it["planned_end"])
        if s is None or e is None:  # board items always carry a window; an unparsable one occupies nothing
            return datetime.min.replace(tzinfo=plant.WIB), datetime.min.replace(tzinfo=plant.WIB)
        return s, e

    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            if not _overlap(win(a), win(b)):
                continue
            if a.get("machine_id") and a.get("machine_id") == b.get("machine_id"):
                if "MAINTENANCE" in (a["kind"], b["kind"]):
                    wo = b if a["kind"] == "MAINTENANCE" else a
                    block("MAINTENANCE_CONFLICT", f"{wo['label']} is scheduled on {wo['machine_id']} during its maintenance.", [a["id"], b["id"]])
                else:
                    block(
                        "MACHINE_CONFLICT",
                        f"{a['label']} ({a['plan_number']}) and {b['label']} ({b['plan_number']}) are booked on {a['machine_id']} at the same time.",
                        [a["id"], b["id"]],
                    )
            elif a.get("mold_id") and a.get("mold_id") == b.get("mold_id") and a["kind"] == b["kind"] == "WORK_ORDER":
                block(
                    "MOLD_CONFLICT",
                    f"{a['label']} and {b['label']} need mold {a['mold_id']} at the same time on {a['machine_id']} and {b['machine_id']}.",
                    [a["id"], b["id"]],
                )
    stockout = _resin_stockout(floor) if any(it.get("process") == "Molding" and it["kind"] == "WORK_ORDER" for it in items) else None
    for it in items:
        if it["kind"] != "WORK_ORDER":
            continue
        if it["board_status"] == "Delayed":
            warn("DELIVERY_RISK", f"{it['label']} is past its planned end and not completed.", [it["id"]])
        elif it["late_minutes"] > 0 and it["status"] not in FINAL:
            warn("DELIVERY_RISK", f"{it['label']} ends {it['late_minutes']:,} min after its delivery date allows (scheduler).", [it["id"]])
        w = by_id[it["wo_id"]]
        for p in effective(by_id, w["predecessor_wo_id"]):
            ps, ss = _dt(p.get("planned_start")), _dt(it["planned_start"])
            if ps and ss and ss < ps and p["status"] not in FINAL:
                warn("SEQUENCE_CONFLICT", f"{it['label']} ({it['process']}) starts before {p['wo_id']} ({p['process']}) that feeds it.", [it["id"]])
        if it["process"] == "Molding":
            start_day = (win(it)[0] - timedelta(hours=DAY_START_HOUR)).date()
            it["material_status"] = "Shortage" if stockout and start_day >= stockout else "Ready"
            if stockout and start_day >= stockout and it["status"] not in FINAL:
                warn("MATERIAL_SHORTAGE", f"Resin PP-EPDM-T20 is projected to run out on {stockout.isoformat()}, before {it['label']} molds.", [it["id"]])
    out.sort(key=lambda c: (not c["blocking"], c["type"]))
    return out


def _lanes(view: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(it: dict[str, Any]) -> tuple[str, str, str | None]:
        if view == "process":
            p = it.get("process") or "Other"
            return p, p, None
        if view == "shift":
            sh = it.get("shift") or "-"
            spec = next((s for s in plant.SHIFTS if s["shift_id"] == sh), None)
            return sh, f"Shift {sh}", f"{spec['start_hour']:02d}:00 to {spec['end_hour']:02d}:00" if spec else None
        if view == "day":
            s = _dt(it["planned_start"])
            d = (s - timedelta(hours=DAY_START_HOUR)).date().isoformat() if s else "-"
            return d, d, None
        mid = it.get("machine_id") or "-"
        m = plant.MACHINE_BY_ID.get(mid)
        return mid, mid, m.machine_name if m else None

    lanes: dict[str, dict[str, Any]] = {}
    if view == "machine":  # an idle machine is the most useful lane on a dispatching board
        for mid in BOARD_MACHINES:
            lanes[mid] = {"id": mid, "name": mid, "subtitle": plant.MACHINE_BY_ID[mid].machine_name, "items": []}
    elif view == "process":
        for p in PROCESSES:
            lanes[p] = {"id": p, "name": p, "subtitle": None, "items": []}
    elif view == "shift":
        for s in plant.SHIFTS:
            lanes[str(s["shift_id"])] = {
                "id": s["shift_id"],
                "name": f"Shift {s['shift_id']}",
                "subtitle": f"{s['start_hour']:02d}:00 to {s['end_hour']:02d}:00",
                "items": [],
            }
    for it in items:
        lid, name, sub = key(it)
        lanes.setdefault(lid, {"id": lid, "name": name, "subtitle": sub, "items": []})["items"].append(it)
    out = list(lanes.values())
    if view == "day":
        out.sort(key=lambda ln: ln["id"])
    for ln in out:
        ln["items"].sort(key=lambda i: i["planned_start"])
        ln["rows"] = _stack(ln["items"])
    return out
