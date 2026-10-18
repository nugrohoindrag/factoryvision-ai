from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone

from app import actions, effects, photos, pubsub
from app.config import settings
from app.session import limiter
from app.store import store
from simulator.engine import MAX_TICKS_PER_CALL, SCENARIOS, State

COLLECTION = "plants"
COST_BUCKETS = ("live_run", "agent")
PERSIST_EVERY_MIN = 10
_cache: dict[str, State] = {}
_wall: dict[str, float] = {}
_saved: dict[str, int] = {}
_lock = threading.Lock()


def _rebuild(doc: dict) -> State:
    s = State.new(doc["session_id"], doc.get("scenario", "baseline"), doc.get("seed"))
    log = sorted(doc.get("log", []), key=lambda e: e["at"])
    k = 0
    while True:
        while k < len(log) and log[k]["at"] == s.minute:
            s.apply(log[k]["command"], log[k].get("action_id"))
            k += 1
        if s.minute >= doc.get("minute", 0):
            break
        s.step()
    s.events.clear()
    return s


def _persist(s: State) -> None:
    store().set(COLLECTION, s.session_id, s.to_doc())
    _saved[s.session_id] = s.minute


def get(session_id: str, create: bool = True) -> State:
    with _lock:
        s = _cache.get(session_id)
        if s is not None and settings.store == "firestore":
            doc = store().get(COLLECTION, session_id)
            if doc and (len(doc.get("log", [])) != len(s.log) or doc.get("minute", 0) > s.minute or doc.get("scenario") != s.scenario):
                s = None
        if s is None:
            doc = store().get(COLLECTION, session_id)
            if doc:
                s = _rebuild(doc)
            elif create:
                s = State.new(session_id, effects.session_doc(session_id).get("scenario", "baseline"))
                _persist(s)
            else:
                return None
            _cache[session_id] = s
        return s


def _publish(s: State, events: list[dict]) -> list[dict]:
    rows = []
    utc = lambda dt: dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    now = utc(datetime.now(timezone.utc))
    for e in events:
        rows.append({"event_id": uuid.uuid4().hex, "session_id": s.session_id, "event_ts": now, "sim_ts": utc(datetime.fromisoformat(e["sim_ts"])),
                     "event_type": e["event_type"], "subject": e["subject"], "payload": json.dumps(e["payload"], default=str)})
    pubsub.publish_batch(pubsub.PLANT_EVENTS, rows)
    return rows


def tick(session_id: str, minutes: int | None = None) -> dict:
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


def on_command(message: dict) -> dict:
    s = get(message["session_id"])
    aid = message.get("action_id")
    with _lock:
        if aid and any(e.get("action_id") == aid for e in s.log):
            return {"duplicate": True, "action_id": aid}
        result = s.apply(message["command"], aid)
    _persist(s)
    return result


def reset(session_id: str, scenario: str = "baseline") -> dict:
    from app.errors import FVError

    if scenario not in SCENARIOS:
        raise FVError("FV-REQ-003", detail={"scenario": scenario, "valid": list(SCENARIOS)})
    actions.clear(session_id)
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


pubsub.subscribe(pubsub.AGENT_COMMANDS, on_command)
pubsub.subscribe(pubsub.PLANT_EVENTS, lambda row: None)
