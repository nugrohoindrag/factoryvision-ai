from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from app import actions, effects, forecasts, photos, plant, pubsub
from app.config import settings
from app.errors import FVError
from app.session import limiter
from app.simulation.engine import MAX_TICKS_PER_CALL, SCENARIOS, State
from app.store import Doc, store

UNKNOWN_SCENARIO = "FV-REQ-003"

COLLECTION = "plants"
COST_BUCKETS = ("live_run", "agent", "vision")
PERSIST_EVERY_MIN = 10
_cache: dict[str, State] = {}
_wall: dict[str, float] = {}
_saved: dict[str, int] = {}
_lock = threading.Lock()


def _rebuild(doc: Doc) -> State:
    s = State.new(doc["session_id"], str(doc.get("scenario", "baseline")), int(doc.get("seed", plant.DEFAULT_SEED)))
    log = sorted(doc.get("log", []), key=lambda e: int(e["at"]))
    k = 0
    while True:
        while k < len(log) and log[k]["at"] == s.minute:
            s.apply(log[k]["command"], log[k].get("action_id"))
            k += 1
        if s.minute >= int(doc.get("minute", 0)):
            break
        s.step()
    s.events.clear()
    return s


def _persist(s: State) -> None:
    store().set(COLLECTION, s.session_id, s.to_doc())
    _saved[s.session_id] = s.minute


def _stale(s: State, doc: Doc | None) -> bool:
    if not doc:
        return False
    return len(doc.get("log", [])) != len(s.log) or int(doc.get("minute", 0)) > s.minute or doc.get("scenario") != s.scenario


def find(session_id: str) -> State | None:
    with _lock:
        s = _cache.get(session_id)
        if s is not None and settings.store == "firestore" and _stale(s, store().get(COLLECTION, session_id)):
            s = None
        if s is None:
            doc = store().get(COLLECTION, session_id)
            if not doc:
                return None
            s = _rebuild(doc)
            _cache[session_id] = s
        return s


def get(session_id: str) -> State:
    s = find(session_id)
    if s is not None:
        return s
    with _lock:
        s = State.new(session_id, str(effects.session_doc(session_id).get("scenario", "baseline")))
        _cache[session_id] = s
    _persist(s)
    return s


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _publish(s: State, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _utc(datetime.now(UTC))
    rows = [
        {
            "event_id": uuid.uuid4().hex,
            "session_id": s.session_id,
            "event_ts": now,
            "sim_ts": _utc(datetime.fromisoformat(e["sim_ts"])),
            "event_type": e["event_type"],
            "subject": e["subject"],
            "payload": json.dumps(e["payload"], default=str),
        }
        for e in events
    ]
    pubsub.publish_batch(pubsub.PLANT_EVENTS, rows)
    return rows


def tick(session_id: str, minutes: int | None = None) -> dict[str, Any]:
    s = get(session_id)
    now = time.monotonic()
    with _lock:
        if minutes is None:
            last = _wall.get(session_id)
            minutes = 1 if last is None else int(now - last)
        minutes = max(0, min(MAX_TICKS_PER_CALL, int(minutes)))
        if minutes:
            _wall[session_id] = now
        for _ in range(minutes):
            s.step()
        events = list(s.events)
        s.events.clear()
    if events:
        _publish(s, events)
    if events or s.minute - _saved.get(session_id, 0) >= PERSIST_EVERY_MIN:
        _persist(s)
    snap = s.snapshot(photos.catalog())
    snap["advanced_minutes"] = minutes
    snap["events"] = events
    return snap


def on_command(message: dict[str, Any]) -> dict[str, Any]:
    s = get(message["session_id"])
    aid = message.get("action_id")
    with _lock:
        if aid and any(e.get("action_id") == aid for e in s.log):
            return {"duplicate": True, "action_id": aid}
        result = s.apply(message["command"], aid)
    _persist(s)
    return result


def reset(session_id: str, scenario: str = "baseline") -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise FVError(UNKNOWN_SCENARIO, detail={"scenario": scenario, "valid": list(SCENARIOS)})
    actions.clear(session_id)
    forecasts.clear(session_id)
    store().delete("observations", session_id)
    for insp in store().where("inspections", "session_id", session_id):
        store().delete("inspections", insp["inspection_id"])
    for doc in store().where("order_documents", "session_id", session_id):
        store().delete("order_documents", doc["doc_id"])
    effects.set_scenario(session_id, scenario)
    limiter.reset(session_id, keep=COST_BUCKETS)
    s = State.new(session_id, scenario)
    with _lock:
        _cache[session_id] = s
        _wall.pop(session_id, None)
    _persist(s)
    return s.snapshot(photos.catalog())


def drop_cache() -> None:
    with _lock:
        _cache.clear()
        _wall.clear()


def _bigquery_sink_only(_row: dict[str, Any]) -> None:
    return None


pubsub.subscribe(pubsub.AGENT_COMMANDS, on_command)
pubsub.subscribe(pubsub.PLANT_EVENTS, _bigquery_sink_only)
