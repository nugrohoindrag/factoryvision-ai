from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app import mrp
from app.agents import material
from app.session import SessionId

router = APIRouter(prefix="/api")


class ProposePoIn(BaseModel):
    material_id: str


@router.post("/mrp/runs/{run_id}/propose-po")
def propose_po(run_id: str, body: ProposePoIn, sid: SessionId) -> dict[str, Any]:
    doc, _created = material.propose_po(sid, mrp.get_run(sid, run_id), body.material_id)
    return doc
