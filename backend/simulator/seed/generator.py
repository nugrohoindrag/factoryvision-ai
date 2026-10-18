from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app import plant

from simulator.seed.schema import TABLES

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED = 20270201

ARKA_FLAT = 560
RAMP_START = date(2026, 12, 20)
ARKA_END = 835
BASE_DEMAND = {"RB-ARKA": 800, "FB-BROMO": 520, "RB-BROMO": 500, "FB-CITRA": 600, "RB-CITRA": 590}
FEB_PLAN_PER_DAY = {"FB-ARKA": 995, "RB-ARKA": 800, "FB-BROMO": 520, "RB-BROMO": 500, "FB-CITRA": 600, "RB-CITRA": 590}
IMM04_CYCLE = (70.0, 72.6, 79.6)
CYCLE_ALARM_RATIO = 1.20
SHORT_SHOT_EXTRA = 0.06
STOCKOUT_WORKDAYS = 8.5
BAD_LOT = "LOT-PPT20-0918"
BAD_LOT_MULT = {"BUBBLE": 6.0, "GAS_MARK": 6.0}
HUMID_THRESHOLD = 80.0
HUMID_MULT = {"DUST_SEED": 2.6}
LATE_ORDER_COUNT = 3

SHIFTS_PER_MACHINE = {"IMM-04": 3, "IMM-05": 3}
DEFECT_RATE_PCT = {
    "DUST_SEED": 0.55,
    "SEED": 0.25,
    "FIBER_SEED": 0.12,
    "ORANGE_PEEL": 0.35,
    "SAG_RUN": 0.25,
    "THIN_FILM": 0.15,
    "DRY_SPRAY": 0.08,
    "ROBOT_BARE": 0.06,
    "VOID": 0.04,
    "BURR": 0.10,
    "BUBBLE": 0.12,
    "GAS_MARK": 0.08,
    "GALLING": 0.03,
    "SCRATCH": 0.30,
    "CHIP": 0.06,
    "TOOL_MARK": 0.05,
    "PAINT_SPOT": 0.12,
    "DENT": 0.05,
    "MOTTLING": 0.05,
    "HAZE": 0.04,
    "BLISTER": 0.05,
    "POPPING": 0.04,
    "PIMPLE": 0.06,
    "OVERSPRAY": 0.04,
    "OTHER": 0.03,
}
DOWNSTREAM_TAKT_S = {"DRY-01": 18, "TRM-01": 18, "PWS-01": 18, "PNT-01": 18, "PNT-02": 18, "OVN-01": 18, "ASM-01": 55, "ASM-02": 55, "ASM-03": 55, "INS-01": 18}
DOWNSTREAM_REASONS = {
    "painting": ["DOLLY_JAM", "CONVEYOR_JAM", "ESG_PROBLEM", "ROBOT_PROBLEM", "SLUDGE_POOL", "BOOTH_TEMP", "AIR_DROP"],
    "prep": ["CONVEYOR_JAM", "AIR_DROP", "WAITING_OPERATOR"],
    "assembly": ["CHILD_PART", "WAITING_OPERATOR", "PALLET_SHORTAGE"],
    "inspection": ["WAITING_OPERATOR", "CONVEYOR_JAM"],
    "molding": ["DRYER", "AIR_DROP"],
}
IMM_REASONS = ["CHILLER", "EJECTOR", "HOT_RUNNER", "VALVE_GATE", "AIR_DROP", "MOLD_CLEANING", "WAIT_CRANE", "COOLING_LEAK", "NO_MATERIAL", "WAITING_OPERATOR"]
IMM04_WEAR_REASONS = ["HEATER_BAND", "HYDRAULIC_LEAK", "MACHINE_BREAKDOWN", "HEATER_BAND"]


def history_days() -> list[date]:
    return [plant.DEMO_TODAY - timedelta(days=plant.HISTORY_DAYS - i) for i in range(plant.HISTORY_DAYS)]


def fb_arka_level(d: date) -> float:
    if d <= RAMP_START:
        return ARKA_FLAT
    span = (plant.DEMO_TODAY - timedelta(days=1) - RAMP_START).days
    return ARKA_FLAT + (ARKA_END - ARKA_FLAT) * (d - RAMP_START).days / span


def demand_level(sku: str, d: date) -> float:
    if sku == "FB-ARKA":
        return fb_arka_level(d)
    return BASE_DEMAND[sku]


def imm04_cycle(i: int) -> float:
    start, knee, end = IMM04_CYCLE
    if i <= 68:
        return start + (knee - start) * i / 68
    return knee + (end - knee) * (i - 68) / (plant.HISTORY_DAYS - 1 - 68)


def utc(d: date, minute_of_day: float) -> datetime:
    local = datetime.combine(d, time(0, 0), tzinfo=plant.WIB) + timedelta(minutes=minute_of_day)
    return local.astimezone(UTC)


SHIFT_START_MIN = {"A": 7 * 60, "B": 15 * 60, "C": 23 * 60}


def output_rate_last7(production: list[dict[str, Any]], machine_id: str) -> float:
    by_day: dict[date, int] = defaultdict(int)
    for r in production:
        if r["machine_id"] == machine_id:
            by_day[r["production_date"]] += r["output_quantity"]
    days = sorted(by_day)[-7:]
    return sum(by_day[d] for d in days) / len(days)


class Generator:
    def __init__(self, seed: int = DEFAULT_SEED) -> None:
        self.rng = random.Random(seed)
        self.t: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES}
        self.truth: dict[str, Any] = {}

    def noise(self, sd: float) -> float:
        return self.rng.gauss(1.0, sd)

    def master(self) -> None:
        t = self.t
        for m in plant.MACHINES:
            sku = plant.INITIAL_MOLDS.get(m.machine_id)
            alarm = round(plant.PRODUCT_BY_SKU[sku].ideal_cycle_s * CYCLE_ALARM_RATIO, 2) if sku else None
            t["dim_machine"].append(
                {
                    "machine_id": m.machine_id,
                    "machine_name": m.machine_name,
                    "process": m.process,
                    "area": m.area,
                    "tonnage": m.tonnage,
                    "kw_rated": m.kw_rated,
                    "cycle_alarm_s": alarm,
                }
            )
        for p in plant.PRODUCTS:
            t["dim_product"].append(
                {
                    "sku": p.sku,
                    "model": p.model,
                    "segment": p.segment,
                    "position": p.position,
                    "ideal_cycle_s": p.ideal_cycle_s,
                    "shot_weight_kg": p.shot_weight_kg,
                    "price_idr": p.price_idr,
                    "colors": ",".join(p.colors),
                }
            )
            mount = next(k for k, v in plant.INITIAL_MOLDS.items() if v == p.sku)
            t["dim_mold"].append({"mold_id": plant.MOLD_ID[p.sku], "sku": p.sku, "cavity": 1, "current_machine_id": mount})
            for mid in plant.MOLD_COMPAT[p.sku]:
                t["product_mold_compatibility"].append({"sku": p.sku, "mold_id": plant.MOLD_ID[p.sku], "machine_id": mid})
        for mat in plant.MATERIALS:
            t["dim_material"].append(
                {
                    "material_id": mat.material_id,
                    "material_name": mat.material_name,
                    "material_type": mat.material_type,
                    "unit": mat.unit,
                    "unit_cost_idr": mat.unit_cost_idr,
                    "safety_stock": mat.safety_stock,
                }
            )
        t["bom"] = plant.bom_lines()
        t["routing"] = [{"seq": i + 1, "process": proc, "machine_id": mid} for i, (proc, mid) in enumerate(plant.ROUTING)]
        t["dim_customer"] = [dict(c) for c in plant.CUSTOMERS]
        t["dim_vendor"] = [{k: v[k] for k in ("vendor_id", "vendor_name", "category", "lead_time_days", "unit_price_idr")} for v in plant.VENDORS]
        t["dim_shift"] = [dict(s) for s in plant.SHIFTS]
        t["dim_defect"] = [
            dict(zip(("defect_code", "shop_term", "defect_name", "ng_group", "defect_category", "default_disposition"), d, strict=True))
            for d in plant.DEFECT_CLASSES
        ]
        t["dim_downtime_reason"] = [
            dict(zip(("reason_code", "reason_name", "loss_category", "reason_category", "shop_term"), r, strict=True)) for r in plant.DOWNTIME_REASONS
        ]

    def downtime_events(self, days: list[date]) -> dict[tuple[date, str, str], list[dict[str, Any]]]:
        events: dict[tuple[date, str, str], list[dict[str, Any]]] = defaultdict(list)
        seq = 0
        for i, d in enumerate(days):
            if not plant.is_working_day(d):
                continue
            week = i // 7
            for m in plant.MACHINES:
                shifts = ["A", "B", "C"][: SHIFTS_PER_MACHINE.get(m.machine_id, 2 if m.area == "molding" and m.machine_id != "DRY-01" else 3)]
                if m.machine_id == "IMM-04":
                    per_week = 0.7 if week < 9 else (2.0 if week < 11 else 5.5)
                    reasons, dur = IMM04_WEAR_REASONS, (45, 150)
                elif m.process == "Injection Molding":
                    per_week, reasons, dur = 1.0, IMM_REASONS, (20, 90)
                else:
                    per_week, reasons, dur = (1.6 if m.area == "painting" else 0.6), DOWNSTREAM_REASONS[m.area], (15, 70)
                p_shift = per_week / (6 * len(shifts))
                for s in shifts:
                    if self.rng.random() < p_shift:
                        seq += 1
                        minutes = round(self.rng.uniform(*dur))
                        offset = self.rng.uniform(20, 450 - minutes - 5)
                        start = utc(d, SHIFT_START_MIN[s] + offset)
                        events[(d, s, m.machine_id)].append(
                            {
                                "downtime_id": f"DT-{seq:05d}",
                                "production_date": d,
                                "shift_id": s,
                                "machine_id": m.machine_id,
                                "start_ts": start,
                                "end_ts": start + timedelta(minutes=minutes),
                                "duration_minutes": float(minutes),
                                "reason_code": self.rng.choice(reasons),
                                "machine_status": "Downtime",
                            }
                        )
        for evs in events.values():
            self.t["fact_downtime"].extend(evs)
        return events

    def production(self, days: list[date], events: dict[tuple[date, str, str], list[dict[str, Any]]]) -> None:
        for i, d in enumerate(days):
            if not plant.is_working_day(d):
                continue
            for m in plant.MACHINES:
                if m.process == "Injection Molding":
                    sku = plant.INITIAL_MOLDS[m.machine_id]
                    prod = plant.PRODUCT_BY_SKU[sku]
                    n_shifts = SHIFTS_PER_MACHINE.get(m.machine_id, 2)
                else:
                    sku, prod, n_shifts = "MIX", None, 3
                for s in ["A", "B", "C"][:n_shifts]:
                    planned = 450.0
                    down = sum(e["duration_minutes"] for e in events.get((d, s, m.machine_id), []))
                    minor = self.rng.uniform(18, 42)
                    run = max(0.0, planned - down - minor)
                    if prod:
                        if m.machine_id == "IMM-04":
                            cycle = imm04_cycle(i) + self.rng.gauss(0, 0.3)
                            scrap_rate = 0.010 + 0.025 * max(0, i - 60) / 29
                        else:
                            cycle = prod.ideal_cycle_s * (1.06 + self.rng.gauss(0, 0.008))
                            scrap_rate = 0.010
                        ideal = prod.ideal_cycle_s
                        total = int(run * 60 / cycle)
                        scrap = round(total * scrap_rate * self.noise(0.15))
                        reject = round(total * 0.006 * self.noise(0.2))
                        resin = total * prod.shot_weight_kg * 0.97 * (1 + SHORT_SHOT_EXTRA - scrap_rate)
                    else:
                        ideal = DOWNSTREAM_TAKT_S[m.machine_id]
                        cycle = ideal * (1.06 + self.rng.gauss(0, 0.01))
                        total = int(run * 60 / cycle)
                        scrap, reject, resin = 0, round(total * 0.004), 0.0
                    rework = round(reject * 0.6)
                    self.t["fact_production"].append(
                        {
                            "production_date": d,
                            "shift_id": s,
                            "machine_id": m.machine_id,
                            "sku": sku,
                            "resin_lot_id": None,
                            "planned_minutes": planned,
                            "run_minutes": round(run, 1),
                            "downtime_minutes": down,
                            "ideal_cycle_s": ideal,
                            "actual_cycle_s": round(cycle, 2),
                            "total_quantity": total,
                            "output_quantity": total - scrap - reject,
                            "reject_quantity": reject,
                            "scrap_quantity": scrap,
                            "rework_quantity": rework,
                            "resin_kg": round(resin, 1),
                        }
                    )

    def resin(self, days: list[date]) -> None:
        t = self.t
        use_by_day: dict[date, float] = defaultdict(float)
        for r in t["fact_production"]:
            use_by_day[r["production_date"]] += r["resin_kg"]
        work = [d for d in days if plant.is_working_day(d)]
        rate = sum(use_by_day[d] for d in work[-14:]) / 14
        s_end = round(rate * STOCKOUT_WORKDAYS, -2)

        pos: list[dict[str, Any]] = []
        d = days[0] - timedelta(days=6)
        n = 0
        while d <= plant.DEMO_TODAY - timedelta(days=19):
            n += 1
            vendor = "VEN-ANDALAS" if (n % 10) not in (3, 6, 9) else "VEN-MERAPI"
            v = plant.VENDOR_BY_ID[vendor]
            promised = d + timedelta(days=v["lead_time_days"])
            delay = self.rng.choice([3, 4, 4, 5, 4, 6, 4, 3, 5, 5]) if vendor == "VEN-ANDALAS" else self.rng.choice([0, 0, 1, 0, 1])
            pos.append({"vendor_id": vendor, "order_date": d, "promised_date": promised, "received_date": promised + timedelta(days=delay)})
            d += timedelta(days=5)
        total_use = sum(use_by_day[x] for x in days)
        s0_target = 150_000.0
        qty = round((total_use + s_end - s0_target) / len(pos), -2)
        received_in_window = [p for p in pos if p["received_date"] >= days[0]]
        s0 = s_end - len(received_in_window) * qty + total_use

        lots: list[dict[str, Any]] = [{"qty": s0, "po": None}, *[{"qty": qty, "po": p} for p in received_in_window]]
        pending = sorted(lots[1:], key=lambda x: x["po"]["received_date"])
        active_by_day: dict[date, int] = {}
        lot_index = {id(lots[0]): 0}
        for k, lot in enumerate(pending, start=1):
            lot_index[id(lot)] = k
        stock_lots: list[list[float]] = [[0, s0]]
        moves: list[tuple[date, int, str, float, dict[str, Any] | None]] = []
        for day in days:
            for lot in pending:
                if lot["po"]["received_date"] == day:
                    stock_lots.append([lot_index[id(lot)], qty])
                    moves.append((day, lot_index[id(lot)], "Receipt", qty, lot))
            use = use_by_day[day]
            if use and stock_lots:
                active_by_day[day] = int(stock_lots[0][0])
            while use > 1e-6 and stock_lots:
                take = min(use, stock_lots[0][1])
                moves.append((day, int(stock_lots[0][0]), "Issue", -take, None))
                stock_lots[0][1] -= take
                use -= take
                if stock_lots[0][1] <= 1e-6:
                    stock_lots.pop(0)
        last_active = active_by_day[max(active_by_day)]
        bad_no = int(BAD_LOT.rsplit("-", 1)[1])

        def name(k: int) -> str:
            return f"LOT-PPT20-{bad_no - last_active + k:04d}"

        self.lot_by_day = {d: name(k) for d, k in active_by_day.items()}

        po_seq = 0
        for p in pos:
            po_seq += 1
            p["po_id"] = f"PO-{p['order_date']:%y%m}-{po_seq:03d}"
        for p in pos:
            in_window = p in received_in_window
            lot_id = name(1 + received_in_window.index(p)) if in_window else None
            t["fact_purchase_order"].append(
                {
                    "po_id": p["po_id"],
                    "vendor_id": p["vendor_id"],
                    "material_id": "PP-EPDM-T20",
                    "order_qty": qty,
                    "unit_price_idr": plant.VENDOR_BY_ID[p["vendor_id"]]["unit_price_idr"],
                    "order_date": p["order_date"],
                    "promised_date": p["promised_date"],
                    "received_date": p["received_date"],
                    "po_status": "Received",
                    "received_qty": qty,
                    "lot_id": lot_id,
                }
            )
        if not t["fact_purchase_order"][-1]["received_date"] < plant.DEMO_TODAY:
            msg = "no resin PO may be open on demo day"
            raise ValueError(msg)
        bad_po = next(r for r in t["fact_purchase_order"] if r["lot_id"] == BAD_LOT)
        if bad_po["vendor_id"] != "VEN-ANDALAS":
            msg = "the bad lot should come from the late vendor"
            raise ValueError(msg)

        for k, (day, li, mtype, q, po) in enumerate(moves):
            t["fact_inventory_move"].append(
                {
                    "move_id": f"MV-{k + 1:05d}",
                    "move_ts": utc(day, 6 * 60 if mtype == "Receipt" else 12 * 60),
                    "move_date": day,
                    "material_id": "PP-EPDM-T20",
                    "lot_id": name(li),
                    "move_type": mtype,
                    "quantity": round(q, 1),
                    "reference_id": po["po"]["po_id"] if po else "MOLDING",
                }
            )
        stock = s0
        for day in days:
            stock += sum(m["quantity"] for m in t["fact_inventory_move"] if m["move_date"] == day)
            t["fact_material_stock"].append({"snapshot_date": day, "material_id": "PP-EPDM-T20", "on_hand_qty": round(stock, 1)})
        if min(r["on_hand_qty"] for r in t["fact_material_stock"]) <= 0:
            msg = "resin stock must never go non-positive"
            raise ValueError(msg)
        for r in t["fact_production"]:
            if r["resin_kg"]:
                r["resin_lot_id"] = self.lot_by_day[r["production_date"]]
        self.resin_end = stock
        self.resin_rate = rate

        others = [("VEN-WARNA", "P1", 2400), ("VEN-KILAU", "K1", 2000), ("VEN-BAJA", "BRK-SET", 30000), ("VEN-RAKIT", "PKG-RT", 400)]
        for k, (vid, mat, q) in enumerate(others):
            for j in range(3):
                od = days[10 + 25 * j + k]
                v = plant.VENDOR_BY_ID[vid]
                pr = od + timedelta(days=v["lead_time_days"])
                po_seq += 1
                t["fact_purchase_order"].append(
                    {
                        "po_id": f"PO-{od:%y%m}-{po_seq:03d}",
                        "vendor_id": vid,
                        "material_id": mat,
                        "order_qty": float(q),
                        "unit_price_idr": plant.MATERIAL_BY_ID[mat].unit_cost_idr,
                        "order_date": od,
                        "promised_date": pr,
                        "received_date": pr + timedelta(days=self.rng.choice([0, 0, 1, 2]) if vid == "VEN-KILAU" else self.rng.choice([0, 0, 1])),
                        "po_status": "Received",
                        "received_qty": float(q),
                        "lot_id": None,
                    }
                )

    def quality(self, days: list[date]) -> None:
        humidity: dict[tuple[date, str], float] = {}
        for d in days:
            base = self.rng.gauss(79.0, 4.5)
            for s, adj in (("A", -2.0), ("B", 0.0), ("C", 4.0)):
                humidity[(d, s)] = round(min(97.0, max(60.0, base + adj + self.rng.gauss(0, 1.5))), 1)
        for r in self.t["fact_production"]:
            if r["sku"] == "MIX":
                continue
            d, s = r["production_date"], r["shift_id"]
            hum, lot, qty = humidity[(d, s)], r["resin_lot_id"], r["output_quantity"]
            defects = 0
            for code, pct in DEFECT_RATE_PCT.items():
                mult = 1.0
                if lot == BAD_LOT:
                    mult *= BAD_LOT_MULT.get(code, 1.0)
                if hum > HUMID_THRESHOLD:
                    mult *= HUMID_MULT.get(code, 1.0)
                mean = qty * pct / 100 * mult
                n = max(0, round(self.rng.gauss(mean, math.sqrt(mean) if mean > 0 else 0)))
                if n:
                    defects += n
                    self.t["fact_quality"].append(
                        {
                            "inspection_date": d,
                            "shift_id": s,
                            "sku": r["sku"],
                            "resin_lot_id": lot,
                            "humidity_pct": hum,
                            "defect_code": code,
                            "defect_qty": n,
                            "disposition": plant.DEFECT_BY_CODE[code][5],
                        }
                    )
            self.t["fact_inspection"].append(
                {"inspection_date": d, "shift_id": s, "sku": r["sku"], "resin_lot_id": lot, "humidity_pct": hum, "inspected_qty": qty, "ok_qty": qty - defects}
            )

    def orders(self, days: list[date]) -> None:
        t = self.t
        seq: defaultdict[date, int] = defaultdict(int)

        def add_order(cust: str, sku: str, qty: int, req: date, status: str, shipped: int) -> dict[str, Any]:
            seq[req] += 1
            row = {
                "order_id": f"SO-{req:%y%m%d}-{seq[req]:03d}",
                "customer_id": cust,
                "sku": sku,
                "order_qty": qty,
                "order_date": req - timedelta(days=7),
                "requested_delivery_date": req,
                "order_status": status,
                "shipped_qty": shipped,
            }
            t["fact_customer_order"].append(row)
            return row

        def split(sku: str, d: date, level: float) -> list[tuple[str, int]]:
            model = plant.PRODUCT_BY_SKU[sku].model
            out = []
            for cust, share in plant.CUSTOMER_MIX[model]:
                if cust.startswith("CUS-OEM"):
                    out.append((cust, round(level * share * self.noise(0.04))))
                elif cust == "CUS-TIER1" and d.weekday() in (1, 4):
                    out.append((cust, round(level * share * 3 * self.noise(0.05))))
                elif (cust == "CUS-AFTM" and d.weekday() == 3) or (cust == "CUS-EXP" and d.weekday() == 2):
                    out.append((cust, round(level * share * 6 * self.noise(0.05))))
            return out

        ship_seq = 0
        for d in days:
            if not plant.is_working_day(d):
                continue
            for sku in plant.SKUS:
                for cust, qty in split(sku, d, demand_level(sku, d)):
                    row = add_order(cust, sku, qty, d, "Completed", qty)
                    late = sku == "FB-ARKA" and cust == "CUS-OEMA" and (plant.DEMO_TODAY - d).days <= 14 and self.rng.random() < 0.35
                    ship_seq += 1
                    t["fact_shipment"].append(
                        {
                            "shipment_id": f"SHP-{ship_seq:05d}",
                            "order_id": row["order_id"],
                            "customer_id": cust,
                            "sku": sku,
                            "shipped_qty": qty,
                            "ship_date": d + timedelta(days=1 if late else 0),
                            "on_time": not late,
                        }
                    )

        week = [plant.DEMO_TODAY + timedelta(days=k) for k in range(6)]
        self.open_orders: list[dict[str, Any]] = []
        for d in week:
            for sku in plant.SKUS:
                level = fb_arka_level(plant.DEMO_TODAY - timedelta(days=1)) + 30 * ((d - plant.DEMO_TODAY).days + 1) if sku == "FB-ARKA" else BASE_DEMAND[sku]
                for cust, qty in split(sku, d, level):
                    self.open_orders.append(add_order(cust, sku, qty, d, "Planned" if d <= week[1] else "Received", 0))

        by_month: dict[tuple[date, str], int] = defaultdict(int)
        for r in t["fact_customer_order"]:
            if r["requested_delivery_date"] < plant.DEMO_TODAY:
                by_month[(r["requested_delivery_date"].replace(day=1), r["sku"])] += r["order_qty"]
        for (m, sku), q in sorted(by_month.items()):
            if m >= date(2026, 12, 1):
                t["fact_monthly_plan"].append({"plan_month": m, "sku": sku, "plan_qty": round(q * self.noise(0.02), -1), "source": "OEM forecast"})
        feb_days = sum(1 for k in range(28) if plant.is_working_day(date(2027, 2, 1) + timedelta(days=k)))
        for sku, per_day in FEB_PLAN_PER_DAY.items():
            t["fact_monthly_plan"].append({"plan_month": date(2027, 2, 1), "sku": sku, "plan_qty": per_day * feb_days, "source": "OEM forecast"})

    def stock_snapshot(self) -> None:
        t = self.t
        ts = utc(plant.DEMO_TODAY, 7 * 60)
        feb = {r["sku"]: r["plan_qty"] for r in t["fact_monthly_plan"] if r["plan_month"] == date(2027, 2, 1)}
        last_lot = self.lot_by_day[max(self.lot_by_day)]

        rate = output_rate_last7(t["fact_production"], "IMM-04")
        orders = sorted([o for o in self.open_orders if o["sku"] == "FB-ARKA"], key=order_sort_key)
        cum, gaps = 0, []
        for o in orders:
            cum += o["order_qty"]
            wd = sum(1 for k in range((o["requested_delivery_date"] - plant.DEMO_TODAY).days + 1) if plant.is_working_day(plant.DEMO_TODAY + timedelta(days=k)))
            gaps.append(cum - rate * wd)
        ranked = sorted(gaps, reverse=True)
        hi, lo = ranked[LATE_ORDER_COUNT - 1], ranked[LATE_ORDER_COUNT]
        if hi - lo < 20:
            msg = f"cannot isolate {LATE_ORDER_COUNT} late orders: {gaps}"
            raise ValueError(msg)
        fb_arka_stock = math.ceil((hi + lo) / 2)
        if fb_arka_stock < 60:
            msg = f"FB-ARKA stock must stay positive, got {fb_arka_stock}: {gaps}"
            raise ValueError(msg)
        self.truth["late_orders"] = [o["order_id"] for o, g in zip(orders, gaps, strict=True) if g > fb_arka_stock]
        self.truth["fb_arka_output_rate"] = round(rate, 1)

        for p in plant.PRODUCTS:
            hourly = feb[p.sku] / 24 / 22.5
            weights = [1.0 / (k + 1.2) for k in range(len(p.colors))]
            wsum = sum(weights)
            if p.sku == "FB-ARKA":
                total = fb_arka_stock
                parts = (0.05, 0.25, 0.70)
            else:
                total = round(hourly * self.rng.uniform(8.0, 10.0))
                parts = (0.30, 0.30, 0.40)
            for k, c in enumerate(p.colors):
                share = weights[k] / wsum
                qty = total * share
                wip, mez = round(qty * parts[0]), round(qty * parts[1])
                sgl = round(qty) - wip - mez
                if p.sku == "FB-ARKA":
                    wip = (2, 1, 1, 1, 1, 0)[k]
                    sgl = round(qty) - wip - mez
                t["fact_stock_snapshot"].append(
                    {
                        "snapshot_ts": ts,
                        "sku": p.sku,
                        "color": c,
                        "wip_qty": wip,
                        "mezzanine_qty": mez,
                        "sgl_qty": sgl,
                        "hourly_use": round(hourly * share, 2),
                        "target_hours": float(plant.WIP_TARGET_HOURS),
                        "resin_lot_id": last_lot,
                    }
                )

    def energy(self) -> None:
        agg: dict[tuple[date, str], dict[str, float]] = defaultdict(lambda: {"kwh": 0.0, "units": 0.0, "run": 0.0})
        for r in self.t["fact_production"]:
            a = agg[(r["production_date"], r["machine_id"])]
            a["kwh"] += r["resin_kg"] * plant.KWH_PER_KG_MOLDED if r["resin_kg"] else 0
            a["units"] += r["total_quantity"]
            a["run"] += r["run_minutes"]
        for (d, mid), a in sorted(agg.items()):
            m = plant.MACHINE_BY_ID[mid]
            kwh = a["kwh"] or m.kw_rated * a["run"] / 60 * 0.6
            gas = a["units"] * 0.42 if mid == "OVN-01" else 0.0
            co2 = kwh * plant.GRID_KG_CO2_PER_KWH + (a["units"] * plant.OVEN_KG_CO2_PER_UNIT if mid == "OVN-01" else 0)
            self.t["fact_energy"].append({"energy_date": d, "machine_id": mid, "kwh": round(kwh, 1), "gas_m3": round(gas, 1), "co2e_kg": round(co2, 1)})

    def run(self) -> None:
        days = history_days()
        self.master()
        events = self.downtime_events(days)
        self.production(days, events)
        self.resin(days)
        self.quality(days)
        self.orders(days)
        self.stock_snapshot()
        self.energy()
        self.truth.update(
            {
                "demo_today": plant.DEMO_TODAY.isoformat(),
                "bad_lot": BAD_LOT,
                "late_vendor": "PT Andalas Polimindo",
                "bottleneck_machine": "IMM-04",
                "shortage_material": "PP-EPDM-T20",
                "resin_on_hand_kg": round(self.resin_end, 1),
                "resin_rate_kg_per_workday": round(self.resin_rate, 1),
            }
        )

    def write(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        for name, cols in TABLES.items():
            names = [c for c, _ in cols]
            with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=names, extrasaction="raise")
                w.writeheader()
                for row in self.t[name]:
                    w.writerow({k: fmt(row.get(k)) for k in names})
        (out / "_schema.json").write_text(json.dumps({n: [{"name": c, "type": ty} for c, ty in cols] for n, cols in TABLES.items()}, indent=1))


def order_sort_key(o: dict[str, Any]) -> tuple[date, int, str]:
    priority = {"CUS-OEMA": 0, "CUS-OEMB": 0, "CUS-TIER1": 1, "CUS-EXP": 2, "CUS-AFTM": 3}
    return (o["requested_delivery_date"], priority[o["customer_id"]], o["order_id"])


def fmt(v: object) -> object:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "seed"))
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    a = ap.parse_args()
    g = Generator(a.seed)
    g.run()
    g.write(Path(a.out))
    (Path(a.out) / "_seed_facts.json").write_text(json.dumps(g.truth, indent=1))
    sys.stdout.write(str({k: len(v) for k, v in g.t.items()}) + "\n")
    sys.stdout.write(json.dumps(g.truth, indent=1) + "\n")


if __name__ == "__main__":
    main()
