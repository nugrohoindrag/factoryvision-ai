from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

import numpy as np

from app import plant

RESIN_START_KG = 138_400.0
MINUTES_PER_DAY = 3 * 450
SPEED_LOSS_PCT = 85.0
WIP_LOW = 10
IMM04_START_CYCLE = 79.3
IMM04_WEAR_S_PER_MIN = 0.336 / MINUTES_PER_DAY
IMM04_FAST_WEAR_S_PER_MIN = 0.09
IMM04_BREAKDOWN_MIN = 150
MAX_TICKS_PER_CALL = 60
RECEIPT_LEAD_MIN = 30
SCENARIOS = ("baseline", "disruption_response", "demand_spike", "vendor_late", "defect_spike")
FAST_WEAR = {"disruption_response"}
NORMAL_FACTOR = 1.03
DOWNTIME_P = 0.0008
SHORT_STOPS = {
    "molding": ["CHILLER", "EJECTOR", "AIR_DROP", "MOLD_CLEANING"],
    "painting": ["DOLLY_JAM", "CONVEYOR_JAM", "ROBOT_PROBLEM"],
    "prep": ["CONVEYOR_JAM", "AIR_DROP"],
    "assembly": ["CHILD_PART", "WAITING_OPERATOR"],
    "inspection": ["WAITING_OPERATOR"],
}
WRONG_COLOR_EVERY = 11


@dataclass
class Machine:
    machine_id: str
    area: str
    status: str = "Running"
    reason_code: str | None = None
    timer: int = 0
    cycle_s: float | None = None
    ideal_cycle_s: float | None = None
    mix: dict[str, float] = field(default_factory=dict)
    progress: float = 0.0
    shots: int = 0
    wear: bool = False
    pending_mix: dict[str, float] | None = None

    def performance_pct(self) -> float | None:
        if not self.cycle_s or not self.ideal_cycle_s:
            return None
        return 100 * self.ideal_cycle_s / self.cycle_s


@dataclass
class State:
    session_id: str
    seed: int = plant.DEFAULT_SEED
    scenario: str = "baseline"
    minute: int = 0
    machines: dict[str, Machine] = field(default_factory=dict)
    wip: dict[str, int] = field(default_factory=dict)
    warehouse: dict[str, list[list[Any]]] = field(default_factory=dict)
    pull: dict[str, float] = field(default_factory=dict)
    resin_kg: float = RESIN_START_KG
    active_lot: str = plant.BAD_LOT
    next_lot: int = int(plant.BAD_LOT.rsplit("-", 1)[1]) + 1
    receipts: list[dict[str, Any]] = field(default_factory=list)
    holds: list[str] = field(default_factory=list)
    output: dict[str, int] = field(default_factory=dict)
    starved: dict[str, int] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def new(cls, session_id: str, scenario: str = "baseline", seed: int = plant.DEFAULT_SEED) -> State:
        s = cls(session_id=session_id, seed=seed, scenario=scenario if scenario in SCENARIOS else "baseline")
        for m in plant.MACHINES:
            mm = Machine(m.machine_id, m.area)
            if m.machine_id in plant.INITIAL_MOLDS:
                sku = plant.INITIAL_MOLDS[m.machine_id]
                mm.mix = {sku: 1.0}
                mm.ideal_cycle_s = plant.PRODUCT_BY_SKU[sku].ideal_cycle_s
                mm.cycle_s = IMM04_START_CYCLE if m.machine_id == "IMM-04" else round(mm.ideal_cycle_s * 1.058, 1)
                mm.wear = m.machine_id == "IMM-04"
            s.machines[m.machine_id] = mm
        for sku in plant.SKUS:
            per_min = plant.FEB_PLAN_PER_DAY[sku] / MINUTES_PER_DAY
            s.wip[sku] = 6 if sku == "FB-ARKA" else min(36, round(per_min * 45))
            s.warehouse[sku] = [[plant.BAD_LOT, round(per_min * (120 if sku == "FB-ARKA" else 240))]]
            s.pull[sku] = 0.0
            s.output[sku] = 0
            s.starved[sku] = 0
        return s

    def rng(self, salt: int = 0) -> np.random.Generator:
        return np.random.default_rng(self.seed * 1_000_003 + self.minute * 97 + salt)

    def emit(self, event_type: str, subject: str, **payload: object) -> None:
        self.events.append({"event_type": event_type, "subject": subject, "sim_minute": self.minute, "sim_ts": sim_ts(self.minute), "payload": payload})

    def set_status(self, m: Machine, status: str, reason: str | None = None, timer: int = 0) -> None:
        if m.status != status or m.reason_code != reason:
            self.emit("machine.status", m.machine_id, status=status, reason_code=reason, previous=m.status)
        m.status, m.reason_code, m.timer = status, reason, timer

    def demand_per_min(self, sku: str) -> float:
        return plant.FEB_PLAN_PER_DAY[sku] / MINUTES_PER_DAY

    def step(self) -> None:
        self.minute += 1
        rng = self.rng()
        self._receipts()
        for m in self.machines.values():
            self._machine(m, rng)
        self._paint_and_ship()

    def _receipts(self) -> None:
        for r in [r for r in self.receipts if r["at"] == self.minute]:
            self.resin_kg += r["qty"]
            self.emit("resin.received", "PP-EPDM-T20", qty_kg=r["qty"], lot=r["lot"], po_id=r["po_id"])
            if self.active_lot in self.holds or self.resin_kg > 0:
                self.active_lot = r["lot"]

    def _machine(self, m: Machine, rng: np.random.Generator) -> None:
        if m.timer > 0:
            m.timer -= 1
            if m.timer == 0:
                if m.status == "Maintenance":
                    m.cycle_s = round(m.ideal_cycle_s * NORMAL_FACTOR, 1) if m.ideal_cycle_s else None
                    m.wear = False
                if m.pending_mix is not None:
                    m.mix, m.pending_mix = m.pending_mix, None
                self.set_status(m, "Running")
            return
        if m.status != "Running":
            return
        is_imm = m.machine_id in plant.MACHINE_BY_ID and plant.MACHINE_BY_ID[m.machine_id].process == "Injection Molding"
        if m.wear and m.cycle_s is not None:
            m.cycle_s = round(m.cycle_s + (IMM04_FAST_WEAR_S_PER_MIN if self.scenario in FAST_WEAR else IMM04_WEAR_S_PER_MIN), 3)
            if self.scenario in FAST_WEAR and self.minute >= IMM04_BREAKDOWN_MIN:
                self.set_status(m, "Downtime", "HEATER_BAND", timer=10_000)
                return
        elif rng.random() < DOWNTIME_P:
            reasons = SHORT_STOPS[m.area]
            self.set_status(m, "Downtime", reasons[int(rng.integers(len(reasons)))], timer=int(rng.integers(8, 21)))
            return
        if not is_imm or m.cycle_s is None:
            return
        if self.resin_kg <= 0:
            self.set_status(m, "Downtime", "NO_MATERIAL", timer=30)
            return
        m.progress += 60.0 / m.cycle_s
        while m.progress >= 1.0:
            m.progress -= 1.0
            m.shots += 1
            sku = _pick(m.mix, m.shots)
            self.resin_kg -= plant.PRODUCT_BY_SKU[sku].shot_weight_kg
            if rng.random() < 0.97:
                self.wip[sku] += 1
                self.output[sku] += 1

    def _paint_and_ship(self) -> None:
        for sku in plant.SKUS:
            self.pull[sku] += self.demand_per_min(sku)
            take = int(self.pull[sku])
            if take <= 0:
                continue
            self.pull[sku] -= take
            painted = min(take, self.wip[sku])
            before = self.wip[sku]
            self.wip[sku] -= painted
            if before >= WIP_LOW > self.wip[sku]:
                self.emit("wip.low", sku, units=self.wip[sku])
            if painted < take:
                self.starved[sku] += 1
            if painted:
                racks = self.warehouse[sku]
                if racks and racks[-1][0] == self.active_lot:
                    racks[-1][1] += painted
                else:
                    racks.append([self.active_lot, painted])
            need = take
            for rack in self.warehouse[sku]:
                if need <= 0:
                    break
                if rack[0] in self.holds:
                    continue
                q = min(need, rack[1])
                rack[1] -= q
                need -= q
            self.warehouse[sku] = [r for r in self.warehouse[sku] if r[1] > 0]

    def apply(self, command: dict[str, Any], action_id: str | None = None) -> dict[str, Any]:
        from app.simulation.commands import execute

        result = execute(self, command)
        self.log.append({"at": self.minute, "command": command, "action_id": action_id})
        self.emit("command.applied", command["name"], args=command.get("args", {}), action_id=action_id, result=result)
        return result

    def snapshot(self, photos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        machines: list[dict[str, Any]] = []
        for m in self.machines.values():
            raw = m.performance_pct()
            perf = round(raw, 1) if raw is not None else None
            machines.append(
                {
                    "machine_id": m.machine_id,
                    "area": m.area,
                    "status": m.status,
                    "reason_code": m.reason_code,
                    "andon": andon(m.status, perf),
                    "cycle_s": round(m.cycle_s, 1) if m.cycle_s else None,
                    "ideal_cycle_s": m.ideal_cycle_s,
                    "performance_pct": perf,
                    "mix": m.mix,
                    "timer_min": m.timer if m.timer < 10_000 else None,
                }
            )
        warehouse = {sku: [{"lot": lot, "units": u, "on_hold": lot in self.holds} for lot, u in racks] for sku, racks in self.warehouse.items()}
        return {
            "session_id": self.session_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "sim_minute": self.minute,
            "sim_ts": sim_ts(self.minute),
            "machines": machines,
            "wip": dict(self.wip),
            "warehouse": warehouse,
            "resin": {
                "on_hand_kg": round(self.resin_kg),
                "active_lot": self.active_lot,
                "incoming": [{"at_minute": r["at"], "qty_kg": r["qty"], "lot": r["lot"], "po_id": r["po_id"]} for r in self.receipts if r["at"] > self.minute],
            },
            "holds": list(self.holds),
            "output_today": dict(self.output),
            "painting_starved_min": dict(self.starved),
            "conveyor": conveyor(self.seed, self.minute, self.scenario, photos or []),
            "state_hash": self.state_hash(),
            "camera_hashes": self.camera_hashes(),
        }

    def camera_hashes(self) -> dict[str, str]:
        def bucket(n: int) -> int:
            return next(k for k, top in enumerate((5, 10, 20, 40, 10**9)) if n < top)

        views = {
            "CAM-Molding": {m.machine_id: andon(m.status, m.performance_pct()) for m in self.machines.values() if m.machine_id.startswith("IMM")},
            "CAM-WIP": {k: bucket(v) for k, v in self.wip.items()},
            "CAM-Warehouse": {k: [any(lot in self.holds for lot, _ in r), bucket(sum(u for _, u in r) // 10)] for k, r in self.warehouse.items()},
        }
        return {cam: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()[:16] for cam, v in views.items()}

    def state_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.camera_hashes(), sort_keys=True).encode()).hexdigest()[:16]

    def to_doc(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "seed": self.seed, "scenario": self.scenario, "minute": self.minute, "log": self.log}

    def full(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("events")
        return d


def _pick(mix: dict[str, Any], n: int) -> str:
    if len(mix) == 1:
        return next(iter(mix))
    u = (n * 0.6180339887498949) % 1.0
    acc = 0.0
    for sku, share in sorted(mix.items()):
        acc += share
        if u < acc:
            return sku
    return max(mix)


def andon(status: str, perf: float | None) -> str:
    if status == "Running":
        return "yellow" if perf is not None and perf < SPEED_LOSS_PCT else "green"
    return {"Setup": "yellow", "Downtime": "red", "Maintenance": "blue"}.get(status, "grey")


def sim_ts(minute: int) -> str:
    return (plant.DEMO_START + timedelta(minutes=minute)).isoformat(timespec="minutes")


def conveyor(seed: int, minute: int, scenario: str, photos: list[dict[str, Any]], n: int = 6) -> list[dict[str, Any]]:
    defects = [p for p in photos if p.get("defect_code")]
    first = minute // 2
    items: list[dict[str, Any]] = []
    for k in range(first, first + n):
        r = np.random.default_rng(seed * 31 + k)
        if defects and scenario == "defect_spike" and k % 3 == 0:
            ph = defects[int(r.integers(len(defects)))]
            sku, color, photo_id, defect = ph["sku"], ph["color"], ph["photo_id"], ph["defect_code"]
        elif photos:
            ph = photos[int(r.integers(len(photos)))]
            sku, color, photo_id, defect = ph["sku"], ph["color"], ph["photo_id"], ph.get("defect_code")
        else:
            sku = plant.SKUS[int(r.integers(len(plant.SKUS)))]
            color, photo_id, defect = plant.PRODUCT_BY_SKU[sku].colors[0], None, None
        expected = color
        if k % WRONG_COLOR_EVERY == 5:
            choices = [c for c in plant.PRODUCT_BY_SKU[sku].colors if c != color]
            expected = choices[int(r.integers(len(choices)))]
        items.append(
            {
                "seq": k,
                "photo_id": photo_id,
                "sku": sku,
                "color": color,
                "expected_color": expected,
                "defect_code": defect,
                "position": round((minute % 2) / 2 + (k - first), 2),
            }
        )
    return items
