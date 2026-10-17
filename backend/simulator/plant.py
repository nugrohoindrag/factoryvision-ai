"""Static spec of Vidad, the fictional car-bumper plant (PRD §2A).

Single source of truth for the simulator, the seed generator and the agent tools.
All names are fictional. Numbers follow the patterns of the Vidad data pack, scaled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

# Demo calendar: the plant's "today" is a Monday 07:00 WIB, six weeks before Lebaran.
DEMO_TODAY = date(2027, 2, 1)
DEMO_START = datetime(2027, 2, 1, 7, 0, tzinfo=WIB)
HISTORY_DAYS = 90
LEBARAN = date(2027, 3, 10)

# Shifts (PRD §2: A/B/C). Planned production time excludes a 30-minute break.
SHIFTS = [
    {"shift_id": "A", "start_hour": 7, "end_hour": 15, "planned_minutes": 450},
    {"shift_id": "B", "start_hour": 15, "end_hour": 23, "planned_minutes": 450},
    {"shift_id": "C", "start_hour": 23, "end_hour": 7, "planned_minutes": 450},
]
WORKING_WEEKDAYS = {0, 1, 2, 3, 4, 5}  # Mon..Sat; Sunday off


def shift_of(ts: datetime) -> str:
    h = ts.hour
    if 7 <= h < 15:
        return "A"
    if 15 <= h < 23:
        return "B"
    return "C"


def is_working_day(d: date) -> bool:
    return d.weekday() in WORKING_WEEKDAYS


# --- Machines -----------------------------------------------------------------

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

# Standard machine statuses (PRD §4.0). The cause of a stop travels as a downtime reason.
MACHINE_STATUSES = ("Running", "Idle", "Downtime", "Setup", "Maintenance", "Offline")


# --- Products, colours, molds ---------------------------------------------------

@dataclass(frozen=True)
class Product:
    sku: str
    model: str
    segment: str
    position: str  # Front / Rear
    ideal_cycle_s: float  # per shot, one cavity
    shot_weight_kg: float  # part + runner
    colors: tuple[str, ...]
    price_idr: int  # selling price per painted bumper


# Colour codes are fictional (C01..C20). Plain names are filled after photo review (PRD §12).
COLOR_NAMES: dict[str, str] = {
    "C01": "C01", "C02": "C02", "C04": "C04", "C05": "C05", "C06": "C06", "C07": "C07",
    "C08": "C08", "C09": "C09", "C10": "C10", "C11": "C11", "C12": "C12", "C16": "C16",
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

# One mold per SKU. Arka molds only fit the 3,500 t machines (PRD §2A).
MOLD_COMPAT: dict[str, list[str]] = {
    "FB-ARKA": ["IMM-04", "IMM-05"],
    "RB-ARKA": ["IMM-04", "IMM-05"],
    "FB-BROMO": ["IMM-03", "IMM-06", "IMM-04", "IMM-05"],
    "RB-BROMO": ["IMM-03", "IMM-06", "IMM-04", "IMM-05"],
    "FB-CITRA": ["IMM-01", "IMM-02", "IMM-03", "IMM-06"],
    "RB-CITRA": ["IMM-01", "IMM-02", "IMM-03", "IMM-06"],
}
MOLD_ID = {sku: f"MLD-{sku}" for sku in SKUS}
CHANGEOVER_MINUTES = 90  # "Dandori"

# Mold mounted on each IMM at the start of the demo.
INITIAL_MOLDS: dict[str, str] = {
    "IMM-01": "FB-CITRA",
    "IMM-02": "RB-CITRA",
    "IMM-03": "FB-BROMO",
    "IMM-04": "FB-ARKA",
    "IMM-05": "RB-ARKA",
    "IMM-06": "RB-BROMO",
}


# --- Materials and BOM ------------------------------------------------------------

@dataclass(frozen=True)
class Material:
    material_id: str
    material_name: str
    material_type: str
    unit: str
    unit_cost_idr: int
    safety_stock: float = 0.0


MATERIALS: list[Material] = [
    Material("PP-EPDM-T20", "Resin PP-EPDM talc 20%", "Resin", "kg", 31_500, 12_000),
    Material("MB-BLK", "Black masterbatch", "Resin additive", "kg", 64_000, 400),
    Material("P1", "Primer P1", "Paint", "kg", 118_000, 300),
    Material("B-BASE", "Basecoat (per colour, see bom)", "Paint", "kg", 165_000, 300),
    Material("K1", "Clear 2K K1", "Paint", "kg", 142_000, 300),
    Material("H1", "Hardener H1", "Paint", "kg", 175_000, 80),
    Material("T1", "Thinner T1", "Paint", "kg", 38_000, 400),
    Material("BRK-SET", "Bracket and clip set", "Component", "set", 21_000, 2_000),
    Material("SNS-HLD", "Parking-sensor holder", "Component", "pcs", 6_500, 3_000),
    Material("PKG-RT", "Returnable rack slot", "Packaging", "slot", 0, 0),
]
MATERIAL_BY_ID = {m.material_id: m for m in MATERIALS}

# Paint use per unit in grams follows the Material List pattern (scaled).
PAINT_G_PER_UNIT = {
    "ARKA": {"P1": 330, "B-BASE": 300, "T1": 450, "K1": 260, "H1": 45},
    "BROMO": {"P1": 380, "B-BASE": 340, "T1": 500, "K1": 300, "H1": 52},
    "CITRA": {"P1": 270, "B-BASE": 230, "T1": 380, "K1": 205, "H1": 36},
}


def bom_lines() -> list[dict]:
    """BOM per SKU (per one good painted bumper)."""
    rows = []
    for p in PRODUCTS:
        rows.append({"sku": p.sku, "material_id": "PP-EPDM-T20", "qty_per_unit": round(p.shot_weight_kg * 0.97, 3), "unit": "kg"})
        rows.append({"sku": p.sku, "material_id": "MB-BLK", "qty_per_unit": round(p.shot_weight_kg * 0.03, 3), "unit": "kg"})
        for mat, grams in PAINT_G_PER_UNIT[p.model].items():
            rows.append({"sku": p.sku, "material_id": mat, "qty_per_unit": grams / 1000, "unit": "kg"})
        rows.append({"sku": p.sku, "material_id": "BRK-SET", "qty_per_unit": 1, "unit": "set"})
        if p.position == "Rear":
            rows.append({"sku": p.sku, "material_id": "SNS-HLD", "qty_per_unit": 4, "unit": "pcs"})
    return rows


ROUTING = [
    ("Resin Drying", "DRY-01"), ("Injection Molding", "IMM-*"), ("Trimming", "TRM-01"),
    ("Surface Prep", "PWS-01"), ("Painting", "PNT-01"), ("Painting", "PNT-02"),
    ("Curing", "OVN-01"), ("Assembly", "ASM-*"), ("Final Inspection", "INS-01"),
]


# --- Customers and vendors ---------------------------------------------------------

CUSTOMERS = [
    {"customer_id": "CUS-OEMA", "customer_name": "Nusantara Motor (OEM-A)", "customer_type": "OEM", "penalty_idr_per_day": 85_000_000},
    {"customer_id": "CUS-OEMB", "customer_name": "Garuda Auto (OEM-B)", "customer_type": "OEM", "penalty_idr_per_day": 60_000_000},
    {"customer_id": "CUS-TIER1", "customer_name": "Sinar Komponen Tier-1", "customer_type": "Tier-1", "penalty_idr_per_day": 15_000_000},
    {"customer_id": "CUS-AFTM", "customer_name": "Bengawan Aftermarket", "customer_type": "Distributor", "penalty_idr_per_day": 5_000_000},
    {"customer_id": "CUS-EXP", "customer_name": "Selat Export Trading", "customer_type": "Export", "penalty_idr_per_day": 10_000_000},
]

# Which customers buy which model, with each customer's share of that model's demand.
CUSTOMER_MIX = {
    "ARKA": [("CUS-OEMA", 0.80), ("CUS-TIER1", 0.12), ("CUS-AFTM", 0.08)],
    "BROMO": [("CUS-OEMB", 0.85), ("CUS-EXP", 0.15)],
    "CITRA": [("CUS-OEMA", 0.70), ("CUS-OEMB", 0.20), ("CUS-AFTM", 0.10)],
}

VENDORS = [
    {"vendor_id": "VEN-ANDALAS", "vendor_name": "PT Andalas Polimindo", "category": "Resin", "lead_time_days": 7, "avg_delay_days": 4.0, "unit_price_idr": 31_500},
    {"vendor_id": "VEN-MERAPI", "vendor_name": "PT Merapi Resin Indonesia", "category": "Resin", "lead_time_days": 5, "avg_delay_days": 0.4, "unit_price_idr": 33_400},
    {"vendor_id": "VEN-WARNA", "vendor_name": "PT Warna Prima Coatings", "category": "Paint", "lead_time_days": 6, "avg_delay_days": 0.8, "unit_price_idr": 0},
    {"vendor_id": "VEN-KILAU", "vendor_name": "PT Kilau Kimia", "category": "Paint", "lead_time_days": 8, "avg_delay_days": 1.2, "unit_price_idr": 0},
    {"vendor_id": "VEN-BAJA", "vendor_name": "CV Baja Klip Mandiri", "category": "Component", "lead_time_days": 4, "avg_delay_days": 0.5, "unit_price_idr": 0},
    {"vendor_id": "VEN-RAKIT", "vendor_name": "PT Rakit Kemasan", "category": "Packaging", "lead_time_days": 3, "avg_delay_days": 0.3, "unit_price_idr": 0},
]
VENDOR_BY_ID = {v["vendor_id"]: v for v in VENDORS}
MATERIAL_VENDORS = {
    "PP-EPDM-T20": ["VEN-ANDALAS", "VEN-MERAPI"],
    "MB-BLK": ["VEN-MERAPI"],
    "P1": ["VEN-WARNA"], "B-BASE": ["VEN-WARNA", "VEN-KILAU"], "K1": ["VEN-KILAU"],
    "H1": ["VEN-KILAU"], "T1": ["VEN-WARNA"], "BRK-SET": ["VEN-BAJA"], "SNS-HLD": ["VEN-BAJA"],
    "PKG-RT": ["VEN-RAKIT"],
}


# --- Defects and downtime reasons (PRD §2A) -------------------------------------------

DEFECT_CLASSES = [
    # (defect_code, shop_term, ui_name, ng_group, category, default_disposition)
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

# (reason_code, reason_name, loss_category, standard_category, shop_term)
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


# --- Cost, energy and carbon constants --------------------------------------------------

ELECTRICITY_IDR_PER_KWH = 1_115  # industrial tariff
GRID_KG_CO2_PER_KWH = 0.87  # Java-Bali grid factor
OVEN_KG_CO2_PER_UNIT = 0.92  # gas-fired curing, per bumper
KWH_PER_KG_MOLDED = 0.78  # IMM specific energy
DOWNTIME_IDR_PER_HOUR_IMM = 6_800_000  # lost contribution + labour per IMM hour
OVERTIME_IDR_PER_SHIFT = 9_500_000  # one extra molding shift (crew + utilities)
CONTRIBUTION_MARGIN = 0.22  # share of selling price lost when a unit is not delivered
WIP_TARGET_HOURS = 6  # stock target in hours of customer use


@dataclass
class PlantSpec:
    """Bundled spec for consumers that want one object."""

    machines: list[Machine] = field(default_factory=lambda: MACHINES)
    products: list[Product] = field(default_factory=lambda: PRODUCTS)
    materials: list[Material] = field(default_factory=lambda: MATERIALS)
