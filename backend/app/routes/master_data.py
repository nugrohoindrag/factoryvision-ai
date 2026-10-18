"""Master data: the seed's dimension tables behind the Master Data menu, editable per session."""

from typing import Any

from fastapi import APIRouter, Request

from app import masterdata
from app.session import SessionId, user_label

router = APIRouter(prefix="/api/master-data")


@router.get("")
def list_master(sid: SessionId) -> dict[str, Any]:
    return masterdata.catalog(sid)


@router.get("/{entity}")
def get_master(entity: str, sid: SessionId) -> dict[str, Any]:
    return masterdata.detail(sid, entity)


@router.post("/{entity}")
def create_row(entity: str, body: dict[str, Any], request: Request, sid: SessionId) -> dict[str, Any]:
    return masterdata.create(sid, entity, body, user_label(request))


@router.put("/{entity}/{row_key}")
def update_row(entity: str, row_key: str, body: dict[str, Any], request: Request, sid: SessionId) -> dict[str, Any]:
    return masterdata.update(sid, entity, row_key, body, user_label(request))


@router.delete("/{entity}/{row_key}")
def delete_row(entity: str, row_key: str, request: Request, sid: SessionId) -> dict[str, Any]:
    return masterdata.delete(sid, entity, row_key, user_label(request))


@router.post("/{entity}/{row_key}/revert")
def revert_row(entity: str, row_key: str, sid: SessionId) -> dict[str, Any]:
    return masterdata.revert(sid, entity, row_key)
