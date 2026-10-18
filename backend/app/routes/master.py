from typing import Any

from fastapi import APIRouter

from app import materials, plant
from app.session import SessionId
from app.tools.common import TODAY

router = APIRouter(prefix="/api")


@router.get("/master/materials")
def list_materials(group: str | None = None) -> dict[str, Any]:
    return {"materials": materials.list_materials(group or None), "groups": list(plant.MATERIAL_GROUPS)}


@router.get("/master/warehouses")
def list_warehouses() -> dict[str, Any]:
    return {"warehouses": materials.list_warehouses()}


@router.get("/master/colors")
def list_colors() -> dict[str, Any]:
    return {
        "models": materials.model_colors(),
        "skus": {sku: materials.sku_colors(sku) for sku in plant.SKUS},
        "colors": materials.color_catalog(),
    }


@router.get("/master/boms")
def list_boms(sku: str | None = None, status: str | None = None) -> dict[str, Any]:
    return {"boms": materials.list_boms(sku or None, status or None), "statuses": list(plant.BOM_STATUSES), "as_of": TODAY.isoformat()}


@router.get("/master/boms/{bom_id}")
def get_bom(bom_id: str) -> dict[str, Any]:
    return materials.get_bom(bom_id)


@router.get("/inventory")
def inventory(sid: SessionId, material_id: str | None = None, warehouse_id: str | None = None, group: str | None = None) -> dict[str, Any]:
    return materials.inventory(sid, material_id or None, warehouse_id or None, group or None)


@router.get("/inventory/transactions")
def transactions(sid: SessionId, material_id: str | None = None) -> dict[str, Any]:
    return {"transactions": materials.transactions(sid, material_id or None)}
