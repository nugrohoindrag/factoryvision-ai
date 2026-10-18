from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

WIB = timezone(timedelta(hours=7))

DEMO_TODAY = date(2027, 2, 1)
DEMO_START = datetime(2027, 2, 1, 7, 0, tzinfo=WIB)
HISTORY_DAYS = 90
LEBARAN = date(2027, 3, 10)

SHIFTS = [
    {"shift_id": "A", "start_hour": 7, "end_hour": 15, "planned_minutes": 450},
    {"shift_id": "B", "start_hour": 15, "end_hour": 23, "planned_minutes": 450},
    {"shift_id": "C", "start_hour": 23, "end_hour": 7, "planned_minutes": 450},
]
WORKING_WEEKDAYS = {0, 1, 2, 3, 4, 5}


def shift_of(ts: datetime) -> str:
    h = ts.hour
    if 7 <= h < 15:
        return "A"
    if 15 <= h < 23:
        return "B"
    return "C"


def is_working_day(d: date) -> bool:
    return d.weekday() in WORKING_WEEKDAYS


@dataclass(frozen=True)
class Machine:
    machine_id: str
    machine_name: str
    process: str
    area: str
    tonnage: int | None = None
    kw_rated: float = 0.0


MACHINES: list[Machine] = [
    Machine("DRY-01", "Resin dryer and blender", "Resin Drying", "molding", kw_rated=45),
    Machine("IMM-01", "Injection molding 2,500 t #1", "Injection Molding", "molding", 2500, 260),
    Machine("IMM-02", "Injection molding 2,500 t #2", "Injection Molding", "molding", 2500, 260),
    Machine("IMM-03", "Injection molding 3,000 t #3", "Injection Molding", "molding", 3000, 300),
    Machine("IMM-04", "Injection molding 3,500 t #4", "Injection Molding", "molding", 3500, 340),
    Machine("IMM-05", "Injection molding 3,500 t #5", "Injection Molding", "molding", 3500, 340),
    Machine("IMM-06", "Injection molding 3,000 t #6", "Injection Molding", "molding", 3000, 300),
    Machine("TRM-01", "Trimming cell", "Trimming", "prep", kw_rated=20),
    Machine("PWS-01", "Power wash and flame treatment", "Surface Prep", "prep", kw_rated=90),
    Machine("PNT-01", "Primer booth", "Painting", "painting", kw_rated=120),
    Machine("PNT-02", "Basecoat and clearcoat booth", "Painting", "painting", kw_rated=160),
    Machine("OVN-01", "Paint curing oven (gas)", "Curing", "painting", kw_rated=60),
    Machine("ASM-01", "Assembly cell #1", "Assembly", "assembly", kw_rated=15),
    Machine("ASM-02", "Assembly cell #2", "Assembly", "assembly", kw_rated=15),
    Machine("ASM-03", "Assembly cell #3", "Assembly", "assembly", kw_rated=15),
    Machine("INS-01", "Final inspection", "Final Inspection", "inspection", kw_rated=8),
]
MACHINE_BY_ID = {m.machine_id: m for m in MACHINES}
IMM_IDS = [m.machine_id for m in MACHINES if m.process == "Injection Molding"]

MACHINE_STATUSES = ("Running", "Idle", "Downtime", "Setup", "Maintenance", "Offline")


@dataclass(frozen=True)
class Product:
    sku: str
    model: str
    segment: str
    position: str
    ideal_cycle_s: float
    shot_weight_kg: float
    colors: tuple[str, ...]
    price_idr: int


COLOR_NAMES: dict[str, str] = {
    "C01": "C01",
    "C02": "C02",
    "C04": "C04",
    "C05": "C05",
    "C06": "C06",
    "C07": "C07",
    "C08": "C08",
    "C09": "C09",
    "C10": "C10",
    "C11": "C11",
    "C12": "C12",
    "C16": "C16",
    "C17": "C17",
}

ARKA_COLORS = ("C02", "C04", "C07", "C08", "C09", "C05")
BROMO_COLORS = ("C01", "C02", "C12", "C11", "C04", "C06", "C08", "C10")
CITRA_COLORS = ("C01", "C06", "C17", "C16")

PRODUCTS: list[Product] = [
    Product("FB-ARKA", "ARKA", "MPV", "Front", 68.0, 3.55, ARKA_COLORS, 1_450_000),
    Product("RB-ARKA", "ARKA", "MPV", "Rear", 64.0, 3.20, ARKA_COLORS, 1_320_000),
    Product("FB-BROMO", "BROMO", "SUV", "Front", 72.0, 3.90, BROMO_COLORS, 1_620_000),
    Product("RB-BROMO", "BROMO", "SUV", "Rear", 66.0, 3.40, BROMO_COLORS, 1_480_000),
    Product("FB-CITRA", "CITRA", "Hatchback", "Front", 58.0, 2.70, CITRA_COLORS, 1_180_000),
    Product("RB-CITRA", "CITRA", "Hatchback", "Rear", 55.0, 2.45, CITRA_COLORS, 1_090_000),
]
PRODUCT_BY_SKU = {p.sku: p for p in PRODUCTS}
SKUS = [p.sku for p in PRODUCTS]

MOLD_COMPAT: dict[str, list[str]] = {
    "FB-ARKA": ["IMM-04", "IMM-05"],
    "RB-ARKA": ["IMM-04", "IMM-05"],
    "FB-BROMO": ["IMM-03", "IMM-06", "IMM-04", "IMM-05"],
    "RB-BROMO": ["IMM-03", "IMM-06", "IMM-04", "IMM-05"],
    "FB-CITRA": ["IMM-01", "IMM-02", "IMM-03", "IMM-06"],
    "RB-CITRA": ["IMM-01", "IMM-02", "IMM-03", "IMM-06"],
}
MOLD_ID = {sku: f"MLD-{sku}" for sku in SKUS}
CHANGEOVER_MINUTES = 90

INITIAL_MOLDS: dict[str, str] = {
    "IMM-01": "FB-CITRA",
    "IMM-02": "RB-CITRA",
    "IMM-03": "FB-BROMO",
    "IMM-04": "FB-ARKA",
    "IMM-05": "RB-ARKA",
    "IMM-06": "RB-BROMO",
}


FEB_PLAN_PER_DAY = {"FB-ARKA": 995, "RB-ARKA": 800, "FB-BROMO": 520, "RB-BROMO": 500, "FB-CITRA": 600, "RB-CITRA": 590}


@dataclass(frozen=True)
class Warehouse:
    warehouse_id: str
    warehouse_name: str
    warehouse_type: str


WAREHOUSES: list[Warehouse] = [
    Warehouse("WH-RM", "Resin silo and additive store", "Resin"),
    Warehouse("WH-PNT", "Paint store and mixing room", "Paint"),
    Warehouse("WH-CMP", "Components and packaging store", "Component"),
]
WAREHOUSE_BY_ID = {w.warehouse_id: w for w in WAREHOUSES}

MATERIAL_GROUPS = ("Resin", "Masterbatch", "Primer", "Basecoat", "Clear", "Hardener", "Thinner", "Component", "Packaging")


@dataclass(frozen=True)
class Material:
    material_id: str
    material_name: str
    material_type: str
    unit: str
    unit_cost_idr: int
    safety_stock: float = 0.0
    material_group: str = "Resin"
    color: str | None = None
    reorder_point: float = 0.0
    lead_time_days: int = 5
    default_vendor_id: str = "VEN-ANDALAS"
    default_warehouse_id: str = "WH-RM"


MODEL_COLORS: dict[str, tuple[str, ...]] = {"ARKA": ARKA_COLORS, "BROMO": BROMO_COLORS, "CITRA": CITRA_COLORS}
BASECOAT_COLORS: tuple[str, ...] = tuple(sorted({c for cs in MODEL_COLORS.values() for c in cs}))

COLOR_FAMILY: dict[str, str] = {
    "C01": "Solid",
    "C02": "Metallic",
    "C04": "Solid",
    "C05": "Pearl",
    "C06": "Metallic",
    "C07": "Metallic",
    "C08": "Solid",
    "C09": "Pearl",
    "C10": "Metallic",
    "C11": "Pearl",
    "C12": "Solid",
    "C16": "Metallic",
    "C17": "Solid",
}
FAMILY_THINNER = {"Solid": "T1", "Metallic": "T2", "Pearl": "T3"}
CLEAR_THINNER = "T4"
MODEL_PAINT_SYSTEM: dict[str, dict[str, str]] = {
    "ARKA": {"primer": "P1", "clear": "K1", "hardener": "H1"},
    "BROMO": {"primer": "P1", "clear": "K1", "hardener": "H1"},
    "CITRA": {"primer": "P2", "clear": "K2", "hardener": "H2"},
}


def _mat(
    mid: str, name: str, mtype: str, unit: str, cost: int, safety: float, group: str, rop: float, lead: int, vendor: str, wh: str, color: str | None = None
) -> Material:
    return Material(mid, name, mtype, unit, cost, safety, group, color, rop, lead, vendor, wh)


MATERIALS: list[Material] = [
    _mat("PP-EPDM-T20", "Resin PP-EPDM talc 20%", "Resin", "kg", 31_500, 12_000, "Resin", 15_000, 7, "VEN-ANDALAS", "WH-RM"),
    _mat("MB-BLK", "Black masterbatch", "Resin additive", "kg", 64_000, 400, "Masterbatch", 600, 5, "VEN-MERAPI", "WH-RM"),
    _mat("P1", "Primer P1", "Paint", "kg", 118_000, 300, "Primer", 500, 6, "VEN-WARNA", "WH-PNT"),
    _mat("P2", "Primer P2 (conductive)", "Paint", "kg", 126_000, 150, "Primer", 250, 6, "VEN-WARNA", "WH-PNT"),
    *(
        _mat(f"B-{c}", f"Basecoat {c} ({COLOR_FAMILY[c].lower()})", "Paint", "kg", 165_000, 60, "Basecoat", 100, 6, "VEN-WARNA", "WH-PNT", color=c)
        for c in BASECOAT_COLORS
    ),
    _mat("K1", "Clear 2K K1", "Paint", "kg", 142_000, 300, "Clear", 450, 8, "VEN-KILAU", "WH-PNT"),
    _mat("K2", "Clear 2K K2", "Paint", "kg", 149_000, 120, "Clear", 200, 8, "VEN-KILAU", "WH-PNT"),
    _mat("H1", "Hardener H1 (for K1)", "Paint", "kg", 175_000, 80, "Hardener", 120, 8, "VEN-KILAU", "WH-PNT"),
    _mat("H2", "Hardener H2 (for K2)", "Paint", "kg", 181_000, 30, "Hardener", 50, 8, "VEN-KILAU", "WH-PNT"),
    _mat("T1", "Thinner T1 (solid basecoat)", "Paint", "kg", 38_000, 250, "Thinner", 400, 6, "VEN-WARNA", "WH-PNT"),
    _mat("T2", "Thinner T2 (metallic basecoat)", "Paint", "kg", 41_000, 250, "Thinner", 400, 6, "VEN-WARNA", "WH-PNT"),
    _mat("T3", "Thinner T3 (pearl basecoat)", "Paint", "kg", 44_000, 150, "Thinner", 250, 6, "VEN-WARNA", "WH-PNT"),
    _mat("T4", "Thinner T4 (clearcoat)", "Paint", "kg", 39_000, 200, "Thinner", 300, 8, "VEN-KILAU", "WH-PNT"),
    *(
        _mat(f"BRK-{m}-{pos[0]}", f"Bracket and clip set {m} {pos.lower()}", "Component", "set", cost, 600, "Component", 1_200, 4, "VEN-BAJA", "WH-CMP")
        for m, cost in (("ARKA", 21_000), ("BROMO", 23_500), ("CITRA", 18_500))
        for pos in ("Front", "Rear")
    ),
    _mat("SNS-HLD", "Parking-sensor holder", "Component", "pcs", 6_500, 3_000, "Component", 4_500, 4, "VEN-BAJA", "WH-CMP"),
    *(_mat(f"PKG-RT-{m}", f"Returnable rack slot {m}", "Packaging", "slot", 0, 0, "Packaging", 150, 3, "VEN-RAKIT", "WH-CMP") for m in MODEL_COLORS),
]
MATERIAL_BY_ID = {m.material_id: m for m in MATERIALS}

PAINT_G_PER_UNIT = {
    "ARKA": {"P1": 330, "B-BASE": 300, "T1": 450, "K1": 260, "H1": 45},
    "BROMO": {"P1": 380, "B-BASE": 340, "T1": 500, "K1": 300, "H1": 52},
    "CITRA": {"P1": 270, "B-BASE": 230, "T1": 380, "K1": 205, "H1": 36},
}
CLEAR_THINNER_SHARE = 0.4
PAINT_SCRAP_PCT = 30.0
MOLDING_SCRAP_PCT = 2.0
BOM_PROCESSES = ("Molding", "Painting", "Assembly", "Packing")
BOM_STATUSES = ("Draft", "Active", "Inactive")


def model_colors() -> dict[str, list[str]]:
    return {m: list(cs) for m, cs in MODEL_COLORS.items()}


def sku_colors(sku: str) -> list[str]:
    p = PRODUCT_BY_SKU.get(sku)
    return list(p.colors) if p else []


@dataclass(frozen=True)
class BomHeaderSpec:
    bom_id: str
    sku: str
    version: str
    status: str
    effective_date: date


@dataclass(frozen=True)
class BomItemSpec:
    bom_id: str
    line_no: int
    material_id: str
    qty_per_unit: float
    unit: str
    scrap_pct: float
    process: str
    color: str | None = None


def _sku_items(p: Product, *, legacy_thinner: bool = False) -> list[tuple[str, float, str, float, str, str | None]]:
    g = PAINT_G_PER_UNIT[p.model]
    paint = MODEL_PAINT_SYSTEM[p.model]
    rows: list[tuple[str, float, str, float, str, str | None]] = [
        ("PP-EPDM-T20", round(p.shot_weight_kg * 0.97, 3), "kg", MOLDING_SCRAP_PCT, "Molding", None),
        ("MB-BLK", round(p.shot_weight_kg * 0.03, 3), "kg", MOLDING_SCRAP_PCT, "Molding", None),
        (paint["primer"], g["P1"] / 1000, "kg", PAINT_SCRAP_PCT, "Painting", None),
    ]
    for c in p.colors:
        rows.append((f"B-{c}", g["B-BASE"] / 1000, "kg", PAINT_SCRAP_PCT, "Painting", c))
        if not legacy_thinner:
            rows.append((FAMILY_THINNER[COLOR_FAMILY[c]], g["T1"] / 1000, "kg", 0.0, "Painting", c))
    rows += [
        (paint["clear"], g["K1"] / 1000, "kg", PAINT_SCRAP_PCT, "Painting", None),
        (paint["hardener"], g["H1"] / 1000, "kg", 0.0, "Painting", None),
    ]
    if not legacy_thinner:
        rows.append((CLEAR_THINNER, round(g["K1"] * CLEAR_THINNER_SHARE) / 1000, "kg", 0.0, "Painting", None))
    rows.append((f"BRK-{p.model}-{p.position[0]}", 1.0, "set", 0.0, "Assembly", None))
    if p.position == "Rear":
        rows.append(("SNS-HLD", 4.0, "pcs", 0.0, "Assembly", None))
    rows.append((f"PKG-RT-{p.model}", 1.0, "slot", 0.0, "Packing", None))
    return rows


def _build_bom_specs() -> tuple[list[BomHeaderSpec], list[BomItemSpec]]:
    headers: list[BomHeaderSpec] = []
    items: list[BomItemSpec] = []

    def add(p: Product, version: str, status: str, effective: date, *, legacy_thinner: bool = False) -> None:
        bid = f"BOM-2027-{len(headers) + 1:03d}"
        headers.append(BomHeaderSpec(bid, p.sku, version, status, effective))
        for n, (mid, q, unit, scrap, proc, color) in enumerate(_sku_items(p, legacy_thinner=legacy_thinner), start=1):
            items.append(BomItemSpec(bid, n, mid, q, unit, scrap, proc, color))

    add(PRODUCT_BY_SKU["FB-ARKA"], "v1.0", "Inactive", date(2027, 1, 4), legacy_thinner=True)
    for p in PRODUCTS:
        add(p, "v2.0" if p.sku == "FB-ARKA" else "v1.0", "Active", date(2027, 1, 18))
    return headers, items


BOM_HEADERS, BOM_ITEMS = _build_bom_specs()


def color_weights(sku: str) -> list[float]:
    w = [1.0 / (k + 1.2) for k in range(len(PRODUCT_BY_SKU[sku].colors))]
    return [x / sum(w) for x in w]


def planned_daily_use() -> dict[str, float]:
    active = {h.bom_id: h.sku for h in BOM_HEADERS if h.status == "Active"}
    out: dict[str, float] = {}
    for it in BOM_ITEMS:
        sku = active.get(it.bom_id)
        if sku is None:
            continue
        p = PRODUCT_BY_SKU[sku]
        share = color_weights(sku)[p.colors.index(it.color)] if it.color else 1.0
        out[it.material_id] = out.get(it.material_id, 0.0) + FEB_PLAN_PER_DAY[sku] * share * it.qty_per_unit * (1 + it.scrap_pct / 100)
    return out


SAFETY_DAYS = 2


def _nice(x: float) -> float:
    return float(max(10, round(x, -1)))


def _with_thresholds(mats: list[Material]) -> list[Material]:
    use = planned_daily_use()
    out = []
    for m in mats:
        if m.material_id == "PP-EPDM-T20":
            out.append(m)
            continue
        safety = _nice(use[m.material_id] * SAFETY_DAYS)
        out.append(replace(m, safety_stock=safety, reorder_point=_nice(safety + use[m.material_id] * m.lead_time_days)))
    return out


MATERIALS[:] = _with_thresholds(MATERIALS)
MATERIAL_BY_ID.update({m.material_id: m for m in MATERIALS})


def bom_problems() -> list[str]:
    out: list[str] = []
    for p in PRODUCTS:
        active = [h for h in BOM_HEADERS if h.sku == p.sku and h.status == "Active"]
        if len(active) != 1:
            out.append(f"{p.sku}: {len(active)} active BOMs")
    headers = {h.bom_id for h in BOM_HEADERS}
    for it in BOM_ITEMS:
        if it.bom_id not in headers:
            out.append(f"{it.bom_id}: item without header")
        if it.material_id not in MATERIAL_BY_ID:
            out.append(f"{it.bom_id}: unknown material {it.material_id}")
        if it.qty_per_unit <= 0 or it.scrap_pct < 0 or it.process not in BOM_PROCESSES:
            out.append(f"{it.bom_id} line {it.line_no}: invalid quantity, scrap or process")
        if it.color is not None and it.color not in BASECOAT_COLORS:
            out.append(f"{it.bom_id} line {it.line_no}: unknown colour {it.color}")
    return out


def bom_lines() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    active = {h.bom_id: h.sku for h in BOM_HEADERS if h.status == "Active"}
    for p in PRODUCTS:
        per: dict[str, float] = {}
        units: dict[str, str] = {}
        for it in BOM_ITEMS:
            if active.get(it.bom_id) != p.sku:
                continue
            share = 1 / len(p.colors) if it.color else 1.0
            per[it.material_id] = per.get(it.material_id, 0.0) + it.qty_per_unit * share
            units[it.material_id] = it.unit
        for mid, q in per.items():
            rows.append({"sku": p.sku, "material_id": mid, "qty_per_unit": round(q, 4) if MATERIAL_BY_ID[mid].unit == "kg" else q, "unit": units[mid]})
    return rows


ROUTING = [
    ("Resin Drying", "DRY-01"),
    ("Injection Molding", "IMM-*"),
    ("Trimming", "TRM-01"),
    ("Surface Prep", "PWS-01"),
    ("Painting", "PNT-01"),
    ("Painting", "PNT-02"),
    ("Curing", "OVN-01"),
    ("Assembly", "ASM-*"),
    ("Final Inspection", "INS-01"),
]


CUSTOMERS: list[dict[str, Any]] = [
    {
        "customer_id": "CUS-OEMA",
        "customer_name": "Nusantara Motor (OEM-A)",
        "customer_type": "OEM",
        "penalty_idr_per_day": 85_000_000,
        "pic_name": "Supplier call-off desk",
        "pic_contact": "calloff@nusantara-motor.example",
        "delivery_address": "Nusantara Motor Plant 2, Karawang",
        "dock_number": "D-07",
    },
    {
        "customer_id": "CUS-OEMB",
        "customer_name": "Garuda Auto (OEM-B)",
        "customer_type": "OEM",
        "penalty_idr_per_day": 60_000_000,
        "pic_name": "Inbound logistics",
        "pic_contact": "inbound@garuda-auto.example",
        "delivery_address": "Garuda Auto Assembly, Cikarang",
        "dock_number": "B-03",
    },
    {
        "customer_id": "CUS-TIER1",
        "customer_name": "Sinar Komponen Tier-1",
        "customer_type": "Tier-1",
        "penalty_idr_per_day": 15_000_000,
        "pic_name": "Purchasing",
        "pic_contact": "purchasing@sinar-komponen.example",
        "delivery_address": "Sinar Komponen, Bekasi",
        "dock_number": "R-1",
    },
    {
        "customer_id": "CUS-AFTM",
        "customer_name": "Bengawan Aftermarket",
        "customer_type": "Distributor",
        "penalty_idr_per_day": 5_000_000,
        "pic_name": "Order admin",
        "pic_contact": "order@bengawan-parts.example",
        "delivery_address": "Bengawan central warehouse, Solo",
        "dock_number": None,
    },
    {
        "customer_id": "CUS-EXP",
        "customer_name": "Selat Export Trading",
        "customer_type": "Export",
        "penalty_idr_per_day": 10_000_000,
        "pic_name": "Export desk",
        "pic_contact": "export@selat-trading.example",
        "delivery_address": "Container yard, Tanjung Priok",
        "dock_number": None,
    },
]

# How each customer sends orders, and the reference it puts on them (strftime on the delivery date).
ORDER_CHANNELS = ("PO_DOCUMENT", "KANBAN_CARD", "EMAIL", "MANUAL")
CUSTOMER_ORDER_SOURCE = {
    "CUS-OEMA": ("KANBAN_CARD", "KB-OEMA-%y%m%d"),
    "CUS-OEMB": ("KANBAN_CARD", "KB-OEMB-%y%m%d"),
    "CUS-TIER1": ("PO_DOCUMENT", "SK-PO-%y%m%d"),
    "CUS-AFTM": ("EMAIL", "BA-EM-%y%m%d"),
    "CUS-EXP": ("PO_DOCUMENT", "SET-PO-%y%m%d"),
}

CUSTOMER_MIX = {
    "ARKA": [("CUS-OEMA", 0.80), ("CUS-TIER1", 0.12), ("CUS-AFTM", 0.08)],
    "BROMO": [("CUS-OEMB", 0.85), ("CUS-EXP", 0.15)],
    "CITRA": [("CUS-OEMA", 0.70), ("CUS-OEMB", 0.20), ("CUS-AFTM", 0.10)],
}

VENDORS: list[dict[str, Any]] = [
    {
        "vendor_id": "VEN-ANDALAS",
        "vendor_name": "PT Andalas Polimindo",
        "category": "Resin",
        "lead_time_days": 7,
        "avg_delay_days": 4.0,
        "unit_price_idr": 31_500,
    },
    {
        "vendor_id": "VEN-MERAPI",
        "vendor_name": "PT Merapi Resin Indonesia",
        "category": "Resin",
        "lead_time_days": 5,
        "avg_delay_days": 0.4,
        "unit_price_idr": 33_400,
    },
    {"vendor_id": "VEN-WARNA", "vendor_name": "PT Warna Prima Coatings", "category": "Paint", "lead_time_days": 6, "avg_delay_days": 0.8, "unit_price_idr": 0},
    {"vendor_id": "VEN-KILAU", "vendor_name": "PT Kilau Kimia", "category": "Paint", "lead_time_days": 8, "avg_delay_days": 1.2, "unit_price_idr": 0},
    {"vendor_id": "VEN-BAJA", "vendor_name": "CV Baja Klip Mandiri", "category": "Component", "lead_time_days": 4, "avg_delay_days": 0.5, "unit_price_idr": 0},
    {"vendor_id": "VEN-RAKIT", "vendor_name": "PT Rakit Kemasan", "category": "Packaging", "lead_time_days": 3, "avg_delay_days": 0.3, "unit_price_idr": 0},
]
VENDOR_BY_ID: dict[str, dict[str, Any]] = {v["vendor_id"]: v for v in VENDORS}
MATERIAL_VENDORS: dict[str, list[str]] = {
    "PP-EPDM-T20": ["VEN-ANDALAS", "VEN-MERAPI"],
    **{m.material_id: [m.default_vendor_id] for m in MATERIALS if m.material_id != "PP-EPDM-T20"},
}


DEFECT_CLASSES = [
    ("FIBER_SEED", "Yarn Seed", "Fiber seed", "Butsu", "Appearance", "Touch Up"),
    ("SEED", "Seed", "Seed", "Butsu", "Appearance", "Touch Up"),
    ("DUST_SEED", "Dust Seed", "Dust seed", "Butsu", "Appearance", "Touch Up"),
    ("THIN_FILM", "Tipis", "Thin film", "Aplikasi", "Appearance", "Repair"),
    ("DRY_SPRAY", "Kering", "Dry spray", "Aplikasi", "Appearance", "Repair"),
    ("ORANGE_PEEL", "OP", "Orange peel", "Aplikasi", "Appearance", "Repair"),
    ("SAG_RUN", "Sagging", "Sag/run", "Aplikasi", "Appearance", "Repair"),
    ("ROBOT_BARE", "BW", "Robot bare spot", "Robot", "Appearance", "Repair"),
    ("VOID", "Boido", "Void", "Robot", "Appearance", "Repair"),
    ("BURR", "Burry", "Burr/flash", "Resin", "Material", "QI"),
    ("BUBBLE", "Bubble/waving", "Bubble/waviness", "Resin", "Material", "Scrap"),
    ("GAS_MARK", "Flek Gas", "Gas mark", "Resin", "Material", "Scrap"),
    ("GALLING", "Galler", "Galling", "Resin", "Material", "Scrap"),
    ("SCRATCH", "Scratch", "Scratch", "Handling", "Appearance", "Touch Up"),
    ("CHIP", "Gompal", "Chip", "Handling", "Appearance", "Repair"),
    ("TOOL_MARK", "TM", "Tool mark", "Handling", "Appearance", "Touch Up"),
    ("PAINT_SPOT", "Flek Paint", "Paint spot", "Other", "Appearance", "Touch Up"),
    ("DENT", "Dink", "Dent", "Other", "Appearance", "Repair"),
    ("MOTTLING", "Belang", "Mottling", "Other", "Appearance", "Repair"),
    ("HAZE", "Buram", "Haze", "Other", "Appearance", "Repair"),
    ("BLISTER", "Blister", "Blister", "Other", "Material", "Scrap"),
    ("POPPING", "Popping", "Popping", "Other", "Material", "Repair"),
    ("PIMPLE", "Bruntus", "Pimple", "Other", "Appearance", "Touch Up"),
    ("OVERSPRAY", "OS", "Overspray", "Other", "Appearance", "Touch Up"),
    ("OTHER", "Other", "Other", "Other", "Other", "QI"),
]
DEFECT_BY_CODE = {d[0]: d for d in DEFECT_CLASSES}
DISPOSITIONS = ("QI", "Touch Up", "Repair", "Scrap")

DOWNTIME_REASONS = [
    ("MACHINE_BREAKDOWN", "Machine Breakdown", "Utility", "Machine", "Mesin"),
    ("PLANNED_MAINTENANCE", "Planned Maintenance", "Utility", "Machine", "PM"),
    ("NO_MATERIAL", "No Material", "Logistics", "Material", "Material TA"),
    ("WAITING_OPERATOR", "Waiting Operator", "Production", "People", "MP Pengganti"),
    ("CHANGEOVER", "Changeover", "Production", "Process", "Dandori"),
    ("HEATER_BAND", "Heater band failure", "Utility", "Machine", "Temp Nozzle Down"),
    ("HYDRAULIC_LEAK", "Hydraulic leak", "Utility", "Machine", "Hydraulic bocor"),
    ("CHILLER", "Chiller trip", "Utility", "Machine", "Chiller"),
    ("DRYER", "Dryer fault", "Utility", "Machine", "Dryer"),
    ("AIR_DROP", "Compressed-air drop", "Utility", "Machine", "Angin Drop"),
    ("EJECTOR", "Mold ejector problem", "Mold", "Machine", "Ejector"),
    ("HOT_RUNNER", "Hot runner (mold)", "Mold", "Machine", "Hot runner mold"),
    ("VALVE_GATE", "Valve gate", "Mold", "Machine", "Valve gate"),
    ("MOLD_CLEANING", "Mold cleaning", "Mold", "Process", "Cleaning Mold"),
    ("COOLING_LEAK", "Cooling hole leak", "Mold", "Machine", "Hole Cooling Bocor"),
    ("WAIT_CRANE", "Waiting for crane", "Production", "Process", "Tunggu Crane"),
    ("PAINT_LINE_STOP", "Painting line stop", "Production", "Process", "Painting Line Stop"),
    ("CUSTOMER_LINE_STOP", "Customer line stop (OEM-A)", "Delivery", "Planning", "Line Stop Customer"),
    ("PALLET_SHORTAGE", "Returnable rack shortage", "Delivery", "Planning", "Pallet TA"),
    ("CHILD_PART", "Child part shortage", "Logistics", "Material", "Child Part TA"),
    ("DOLLY_JAM", "Dolly jam", "Utility", "Machine", "Dolly macet"),
    ("CONVEYOR_JAM", "Conveyor jam", "Utility", "Machine", "Conveyor macet"),
    ("ESG_PROBLEM", "Electrostatic gun problem", "Utility", "Machine", "ESG problem"),
    ("ROBOT_PROBLEM", "Paint robot problem", "Utility", "Machine", "Robot problem"),
    ("SLUDGE_POOL", "Sludge pool", "Utility", "Machine", "Sludgepool"),
    ("BOOTH_TEMP", "Booth temperature high", "Utility", "Machine", "Temp. booth panas"),
]
REASON_BY_CODE = {r[0]: r for r in DOWNTIME_REASONS}


ELECTRICITY_IDR_PER_KWH = 1_115
GRID_KG_CO2_PER_KWH = 0.87
OVEN_KG_CO2_PER_UNIT = 0.92
KWH_PER_KG_MOLDED = 0.78
DOWNTIME_IDR_PER_HOUR_IMM = 6_800_000
OVERTIME_IDR_PER_SHIFT = 9_500_000
CONTRIBUTION_MARGIN = 0.22
WIP_TARGET_HOURS = 6


@dataclass
class PlantSpec:
    machines: list[Machine] = field(default_factory=lambda: MACHINES)
    products: list[Product] = field(default_factory=lambda: PRODUCTS)
    materials: list[Material] = field(default_factory=lambda: MATERIALS)


DEFAULT_SEED = 20270201
BAD_LOT = "LOT-PPT20-0918"
