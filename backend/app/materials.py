# shortcut: thin stand-in for app.materials (owner: masterdata); the integrator keeps the real module.
from __future__ import annotations

from typing import Any

from app import plant
from app.errors import FVError

UNKNOWN_ENTITY = "FV-DATA-001"


def get_material(material_id: str) -> dict[str, Any]:
    m = plant.MATERIAL_BY_ID.get(material_id)
    if m is None:
        raise FVError(UNKNOWN_ENTITY, f"unknown material {material_id}")
    vendors = plant.MATERIAL_VENDORS.get(material_id, [])
    return {
        "material_id": m.material_id,
        "material_name": m.material_name,
        "material_group": m.material_type,
        "color": None,
        "unit": m.unit,
        "unit_cost_idr": m.unit_cost_idr,
        "safety_stock": m.safety_stock,
        "reorder_point": m.safety_stock,
        "lead_time_days": plant.VENDOR_BY_ID[vendors[0]]["lead_time_days"] if vendors else None,
        "default_vendor_id": vendors[0] if vendors else None,
        "default_warehouse_id": None,
    }
