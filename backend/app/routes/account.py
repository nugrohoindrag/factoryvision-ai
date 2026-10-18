from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app import accounts
from app.config import settings
from app.session import SignedIn

router = APIRouter(prefix="/api")


@router.get("/auth/config")
def auth_config() -> dict[str, Any]:
    """Firebase web config for Sign in with Google. These values are public by design; the key is restricted by referrer."""
    enabled = bool(settings.firebase_api_key and settings.project)
    return {
        "enabled": enabled,
        "apiKey": settings.firebase_api_key,
        "authDomain": f"{settings.project}.firebaseapp.com" if settings.project else "",
        "projectId": settings.project,
        "appId": settings.firebase_app_id,
    }


class RegisterIn(BaseModel):
    full_name: str | None = None
    factory_name: str
    industry: str
    role: str
    city: str | None = None
    plant_scale: str | None = None


class DecisionIn(BaseModel):
    note: str | None = None


class TemplateIn(BaseModel):
    template_id: str


@router.get("/account/me")
def me(claims: SignedIn) -> dict[str, Any]:
    return accounts.me(claims)


@router.post("/account/register")
def register(body: RegisterIn, claims: SignedIn) -> dict[str, Any]:
    return accounts.register(claims, body.model_dump())


@router.get("/onboarding/templates")
def templates() -> dict[str, Any]:
    return {"templates": accounts.TEMPLATES}


@router.post("/onboarding/apply-template")
def apply_template(body: TemplateIn, claims: SignedIn) -> dict[str, Any]:
    return accounts.apply_template(claims, body.template_id)


@router.post("/onboarding/steps/{step_id}")
def complete_step(step_id: str, claims: SignedIn) -> dict[str, Any]:
    return accounts.complete_step(claims, step_id)


@router.get("/admin/registrations")
def registrations(claims: SignedIn) -> dict[str, Any]:
    return accounts.list_registrations(claims)


@router.post("/admin/registrations/{uid}/approve")
def approve(uid: str, claims: SignedIn, body: DecisionIn | None = None) -> dict[str, Any]:
    return accounts.decide(claims, uid, approve=True, note=(body or DecisionIn()).note)


@router.post("/admin/registrations/{uid}/reject")
def reject(uid: str, claims: SignedIn, body: DecisionIn | None = None) -> dict[str, Any]:
    return accounts.decide(claims, uid, approve=False, note=(body or DecisionIn()).note)
