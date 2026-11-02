"""REST API. Each route: validate input (Pydantic) -> check permission -> call services."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import alerts, db, services
from ..deps import current_user, require
from ..security import issue_token, permissions_for

router = APIRouter(prefix="/api")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except services.ServiceError as e:
        raise HTTPException(e.status, e.message)


def actor(u: dict) -> str:
    return f"user:{u['username']}"


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/auth/login")
def login(body: LoginIn):
    u = _call(services.authenticate, body.username, body.password, time.time())
    return {"access_token": issue_token(u["id"], u["username"], u["role"]), "token_type": "bearer",
            "user": {**u, "permissions": permissions_for(u["role"])}}


@router.get("/auth/me")
def me(u: dict = Depends(current_user)):
    return {**u, "permissions": permissions_for(u["role"])}


# ------------------------------------------------------------------ users (admin)
class UserIn(BaseModel):
    username: str
    password: str
    role: str


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = None


@router.get("/users")
def users(u: dict = Depends(require("users.manage"))):
    return services.list_users()


@router.post("/users", status_code=201)
def create_user(body: UserIn, u: dict = Depends(require("users.manage"))):
    return _call(services.create_user, body.username, body.password, body.role, actor(u), time.time())


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, u: dict = Depends(require("users.manage"))):
    return _call(services.update_user, user_id, actor(u), time.time(), body.role, body.active, body.password)


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard")
def dashboard(u: dict = Depends(require("dashboard.view"))):
    return services.dashboard(time.time())


@router.get("/fridges/{fridge_id}/history")
def history(fridge_id: str, minutes: int = 60, u: dict = Depends(require("dashboard.view"))):
    return _call(services.fridge_history, fridge_id, minutes, time.time())


@router.get("/fridges")
def fridges(u: dict = Depends(require("inventory.view"))):
    with db.read() as conn:
        return db.rows(conn.execute("SELECT id, name, capacity_units, state, online FROM fridges ORDER BY id"))


# ------------------------------------------------------------------ inventory
class BatchIn(BaseModel):
    vaccine: str
    lot_no: str
    quantity: int
    expiry_date: str
    fridge_id: str
    notes: str = ""


class BatchPatch(BaseModel):
    vaccine: str | None = None
    lot_no: str | None = None
    quantity: int | None = None
    expiry_date: str | None = None
    notes: str | None = None


class MoveIn(BaseModel):
    to_fridge: str
    reason: str = "manual move"


class ReasonIn(BaseModel):
    reason: str = ""


@router.get("/batches")
def batches(include_inactive: bool = False, u: dict = Depends(require("inventory.view"))):
    return services.list_batches(time.time(), include_inactive)


@router.post("/batches", status_code=201)
def create_batch(body: BatchIn, u: dict = Depends(require("inventory.edit"))):
    return _call(services.create_batch, body.model_dump(), actor(u), time.time())


@router.patch("/batches/{batch_id}")
def patch_batch(batch_id: int, body: BatchPatch, u: dict = Depends(require("inventory.edit"))):
    return _call(services.update_batch, batch_id, body.model_dump(exclude_none=True), actor(u), time.time())


@router.post("/batches/{batch_id}/move")
def move_batch(batch_id: int, body: MoveIn, u: dict = Depends(require("batch.move"))):
    return _call(services.move_batch, batch_id, body.to_fridge, actor(u), time.time(), body.reason[:120])


@router.post("/batches/{batch_id}/remove")
def remove_batch(batch_id: int, body: ReasonIn, u: dict = Depends(require("inventory.edit"))):
    return _call(services.deactivate_batch, batch_id, actor(u), time.time(), body.reason[:120])


@router.get("/batches/{batch_id}/qr.svg")
def batch_qr(batch_id: int, u: dict = Depends(require("inventory.view"))):
    with db.read() as conn:
        b = db.one(conn.execute("SELECT public_id FROM batches WHERE id=?", (batch_id,)))
    if not b:
        raise HTTPException(404, "Batch not found")
    return Response(services.qr_svg(b["public_id"]), media_type="image/svg+xml")


# ------------------------------------------------------------------ public (no login)
@router.get("/public/batches/{public_id}")
def public_batch(public_id: str):
    return _call(services.public_batch, public_id, time.time())


# ------------------------------------------------------------------ logs and integrity
@router.get("/audit")
def audit_log(limit: int = 200, prefix: str = "", u: dict = Depends(require("logs.view"))):
    return services.audit_list(limit, prefix)


@router.get("/security-events")
def security_events(limit: int = 200, u: dict = Depends(require("security.view"))):
    return services.security_events(limit)


@router.post("/integrity/verify")
def verify(u: dict = Depends(require("logs.verify"))):
    return services.verify_integrity(actor(u), time.time())


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
def list_alerts(limit: int = 100, u: dict = Depends(require("dashboard.view"))):
    return services.list_alerts(limit)


@router.post("/alerts/{alert_id}/ack")
def ack(alert_id: int, u: dict = Depends(require("alert.ack"))):
    return _call(services.ack_alert, alert_id, actor(u), time.time())


@router.post("/alerts/test")
def test_alert(u: dict = Depends(require("alert.test"))):
    with db.tx() as conn:
        aid = alerts.raise_alert(conn, time.time(), None, "test", "info",
                                 f"Test alert sent by {u['username']} - channels are working")
    return {"ok": True, "alert_id": aid, "channels": alerts.channels_configured()}


# ------------------------------------------------------------------ AI + relocation
@router.get("/ai")
def ai(u: dict = Depends(require("ai.view"))):
    return services.ai_overview()


@router.get("/relocations")
def relocations(u: dict = Depends(require("dashboard.view"))):
    return services.list_relocations()


@router.post("/relocations/{reloc_id}/confirm")
def confirm(reloc_id: int, u: dict = Depends(require("relocation.confirm"))):
    return _call(services.confirm_relocation, reloc_id, actor(u), time.time())


@router.post("/relocations/{reloc_id}/dismiss")
def dismiss(reloc_id: int, body: ReasonIn, u: dict = Depends(require("relocation.confirm"))):
    return _call(services.dismiss_relocation, reloc_id, actor(u), time.time(), body.reason[:200])


# ------------------------------------------------------------------ admin device actions
@router.post("/fridges/{fridge_id}/reset-discard-led")
def reset_led(fridge_id: str, request: Request, u: dict = Depends(require("device.reset_led"))):
    return _call(services.reset_discard_led, fridge_id, actor(u), time.time(), request.app.state.ingestor)


@router.post("/fridges/{fridge_id}/recalibrate-health")
def recalibrate(fridge_id: str, u: dict = Depends(require("device.recalibrate"))):
    return _call(services.recalibrate_health, fridge_id, actor(u), time.time())
