from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.database import get_db
from backend.app.db.models import BootstrapStateModel, OperatorModel, RegistrationInviteModel, TenantModel
from backend.app.security.audit import append_audit
from backend.app.security.auth import Principal, authorize, get_current_principal, hash_password, issue_access_token, valid_password_hash, verify_password
from backend.app.observability import record_auth_event

router = APIRouter()
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_lock = asyncio.Lock()
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_ATTEMPT_LIMIT = 10
_registration_attempts: dict[str, deque[float]] = defaultdict(deque)
_REGISTRATION_WINDOW_SECONDS = 900
_REGISTRATION_ATTEMPT_LIMIT = 5


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(..., min_length=1, max_length=160)
    password: str = Field(..., min_length=1, max_length=1024)


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(..., min_length=1, max_length=160)
    username: str = Field(..., min_length=3, max_length=160, pattern=r"^[A-Za-z0-9_.@-]+$")
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=12, max_length=1024)
    confirm_password: str = Field(..., min_length=12, max_length=1024)

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("Enter a valid full name")
        return value

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+", value):
            raise ValueError("Enter a valid email address")
        return value

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        if not self.password.strip():
            raise ValueError("Password cannot contain only whitespace")
        return self


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=3, max_length=320)
    role: str = Field(..., pattern=r"^(OPERATOR|VIEWER)$")
    expires_in_minutes: int = Field(1440, ge=1, le=10080)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+", value):
            raise ValueError("Enter a valid email address")
        return value


class OperatorCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(..., min_length=3, max_length=160, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: str = Field(..., min_length=12, max_length=1024)
    role: str = Field(..., pattern=r"^(ADMIN|OPERATOR|VIEWER)$")
    tenant_id: str = Field(..., min_length=1, max_length=80)


class TenantCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(..., min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(..., min_length=2, max_length=160)
    admin_username: str = Field(..., min_length=3, max_length=160, pattern=r"^[A-Za-z0-9_.@-]+$")
    admin_password: str = Field(..., min_length=12, max_length=1024)


class OperatorUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str | None = Field(None, pattern=r"^(ADMIN|OPERATOR|VIEWER)$")
    password: str | None = Field(None, min_length=12, max_length=1024)


async def ensure_bootstrap_operator(db: AsyncSession) -> None:
    """Create the first admin only from an environment-provided password hash."""
    if not settings.APEX_AUTH_ENABLED:
        return
    if len(settings.APEX_JWT_SIGNING_KEY.encode("utf-8")) < 32:
        raise RuntimeError("APEX_JWT_SIGNING_KEY must contain at least 32 characters while authentication is enabled")
    existing_operator_id = await db.scalar(select(OperatorModel.id).limit(1))
    bootstrap_state = await db.get(BootstrapStateModel, "initial_admin")
    if existing_operator_id:
        if not bootstrap_state:
            db.add(BootstrapStateModel(key="initial_admin", operator_id=existing_operator_id))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if not await db.get(BootstrapStateModel, "initial_admin"):
                    raise
        return
    if bootstrap_state:
        raise RuntimeError("Initial admin bootstrap has already been used; refusing to recreate an account")
    username = settings.APEX_BOOTSTRAP_ADMIN_USERNAME.strip()
    password_hash_value = settings.APEX_BOOTSTRAP_ADMIN_PASSWORD_HASH
    if not username or not valid_password_hash(password_hash_value):
        raise RuntimeError("Set APEX_BOOTSTRAP_ADMIN_USERNAME and a valid PBKDF2 APEX_BOOTSTRAP_ADMIN_PASSWORD_HASH before starting authenticated mode")
    tenant = await db.get(TenantModel, "default")
    if not tenant:
        tenant = TenantModel(id="default", name="Default tenant", is_active=True)
        db.add(tenant)
    operator = OperatorModel(
        tenant_id="default",
        username=username.lower(),
        full_name=settings.APEX_BOOTSTRAP_ADMIN_FULL_NAME.strip() or None,
        email=settings.APEX_BOOTSTRAP_ADMIN_EMAIL.strip().lower() or None,
        password_hash=password_hash_value,
        role="ADMIN",
        is_active=True,
    )
    db.add(operator)
    try:
        await db.flush()
        db.add(BootstrapStateModel(key="initial_admin", operator_id=operator.id))
        await append_audit(
            db,
            event_type="ADMIN_BOOTSTRAPPED",
            tenant_id="default",
            actor_id=operator.id,
            payload={},
            commit=False,
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if not await db.get(BootstrapStateModel, "initial_admin"):
            raise


async def _allow_login_attempt(key: str) -> bool:
    now = time.monotonic()
    async with _login_lock:
        events = _login_attempts[key]
        while events and now - events[0] > _LOGIN_WINDOW_SECONDS:
            events.popleft()
        if len(events) >= _LOGIN_ATTEMPT_LIMIT:
            return False
        return True


async def _record_failed_login(key: str) -> None:
    now = time.monotonic()
    async with _login_lock:
        events = _login_attempts[key]
        while events and now - events[0] > _LOGIN_WINDOW_SECONDS:
            events.popleft()
        events.append(now)


async def _consume_registration_attempt(key: str) -> bool:
    now = time.monotonic()
    async with _login_lock:
        events = _registration_attempts[key]
        while events and now - events[0] > _REGISTRATION_WINDOW_SECONDS:
            events.popleft()
        if len(events) >= _REGISTRATION_ATTEMPT_LIMIT:
            return False
        events.append(now)
        return True


def _registration_unavailable() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail="Registration could not be completed. Check your details or contact your administrator.",
    )


@router.post("/auth/login")
async def login(req: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    if not settings.APEX_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Authentication is disabled in local development mode")
    remote = request.client.host if request.client else "unknown"
    if not await _allow_login_attempt(remote):
        record_auth_event("rate_limited")
        await append_audit(db, event_type="AUTH_RATE_LIMITED", payload={"remote_address": remote})
        raise HTTPException(status_code=429, detail="Too many login attempts; retry later")
    identifier = req.username.strip().lower()
    operator = await db.scalar(select(OperatorModel).where(func.lower(OperatorModel.username) == identifier))
    if operator is None:
        operator = await db.scalar(select(OperatorModel).where(func.lower(OperatorModel.email) == identifier))
    if not operator or not operator.is_active or not verify_password(req.password, operator.password_hash):
        record_auth_event("login_failed")
        await _record_failed_login(remote)
        await append_audit(db, event_type="AUTH_LOGIN_FAILED", payload={"remote_address": remote})
        raise HTTPException(status_code=401, detail="Username or password is incorrect")
    token, expires_at = issue_access_token(operator)
    record_auth_event("login_succeeded")
    response.set_cookie(
        "apex_session", token,
        httponly=True,
        secure=settings.APEX_AUTH_COOKIE_SECURE,
        samesite="strict",
        max_age=max(5, settings.APEX_JWT_TTL_MINUTES) * 60,
        path="/",
    )
    await append_audit(db, event_type="AUTH_LOGIN_SUCCEEDED", tenant_id=operator.tenant_id, actor_id=operator.id, payload={})
    return {"access_token": token, "token_type": "bearer", "expires_at": expires_at, "role": operator.role, "tenant_id": operator.tenant_id}


@router.post("/auth/register", status_code=201)
async def register(req: RegistrationRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Create a standard operator in the server-selected default tenant."""
    if not settings.APEX_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Registration is unavailable in local bypass mode")
    remote = request.client.host if request.client else "unknown"
    if not await _consume_registration_attempt(remote):
        record_auth_event("registration_rate_limited")
        await append_audit(db, event_type="AUTH_REGISTRATION_RATE_LIMITED", payload={"remote_address": remote})
        raise HTTPException(status_code=429, detail="Too many registration attempts; retry later")

    tenant = await db.get(TenantModel, "default")
    if not tenant or not tenant.is_active:
        await append_audit(db, event_type="AUTH_REGISTRATION_REJECTED", payload={"remote_address": remote})
        raise _registration_unavailable()

    operator = OperatorModel(
        tenant_id=tenant.id,
        username=req.username,
        full_name=req.full_name,
        email=req.email,
        password_hash=hash_password(req.password),
        role="OPERATOR",
        is_active=True,
    )
    db.add(operator)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        await append_audit(db, event_type="AUTH_REGISTRATION_REJECTED", payload={"remote_address": remote})
        raise _registration_unavailable()
    await append_audit(
        db,
        event_type="AUTH_REGISTRATION_SUCCEEDED",
        tenant_id=tenant.id,
        actor_id=operator.id,
        payload={"role": operator.role},
        commit=False,
    )
    await db.commit()
    record_auth_event("registration_succeeded")
    return {"operator_id": operator.id, "username": operator.username, "role": operator.role, "tenant_id": operator.tenant_id}


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("apex_session", path="/", httponly=True, secure=settings.APEX_AUTH_COOKIE_SECURE, samesite="strict")
    return {"status": "SIGNED_OUT"}


@router.get("/auth/me")
async def who_am_i(principal: Principal = Depends(get_current_principal)):
    return {"operator_id": principal.operator_id, "username": principal.username, "role": principal.role, "tenant_id": principal.tenant_id}


@router.get("/auth/operators")
async def list_operators(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "operators:manage")
    result = await db.execute(select(OperatorModel).where(OperatorModel.tenant_id == principal.tenant_id).order_by(OperatorModel.username))
    return [{"operator_id": op.id, "username": op.username, "full_name": op.full_name, "email": op.email, "role": op.role, "tenant_id": op.tenant_id, "is_active": op.is_active} for op in result.scalars()]


@router.post("/auth/invitations", status_code=201)
async def create_registration_invitation(
    req: InvitationCreateRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "operators:manage")
    if principal.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Only an administrator can invite operators")
    tenant = await db.get(TenantModel, principal.tenant_id)
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=404, detail="Tenant is unavailable")
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=req.expires_in_minutes)
    invitation = RegistrationInviteModel(
        tenant_id=principal.tenant_id,
        email=req.email,
        role=req.role,
        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        created_by=principal.operator_id,
        expires_at=expires_at,
    )
    db.add(invitation)
    await db.flush()
    await append_audit(
        db,
        event_type="REGISTRATION_INVITATION_CREATED",
        tenant_id=principal.tenant_id,
        actor_id=principal.operator_id,
        payload={"invitation_id": invitation.id, "role": req.role, "expires_at": expires_at.isoformat()},
        commit=False,
    )
    await db.commit()
    return {
        "invitation_id": invitation.id,
        "invitation_token": token,
        "email": req.email,
        "role": req.role,
        "tenant_id": principal.tenant_id,
        "expires_at": expires_at.isoformat(),
        "registration_path": "/?register=1",
    }


@router.post("/auth/tenants", status_code=201)
async def create_tenant(
    req: TenantCreateRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "operators:manage")
    if principal.role != "ADMIN" or principal.tenant_id != "default":
        raise HTTPException(status_code=403, detail="Only a default-tenant administrator can provision tenants")
    tenant = TenantModel(id=req.tenant_id, name=req.name, is_active=True)
    db.add(tenant)
    try:
        await db.flush()
        db.add(OperatorModel(
            tenant_id=req.tenant_id,
            username=req.admin_username,
            password_hash=hash_password(req.admin_password),
            role="ADMIN",
            is_active=True,
        ))
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Tenant ID is already assigned") from exc
    await append_audit(db, event_type="TENANT_CREATED", tenant_id=principal.tenant_id, actor_id=principal.operator_id, payload={"new_tenant_id": req.tenant_id, "name": req.name}, commit=False)
    await db.commit()
    return {"tenant_id": tenant.id, "name": tenant.name, "is_active": tenant.is_active}


@router.get("/auth/tenants")
async def list_tenants(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "operators:manage")
    if principal.role != "ADMIN" or principal.tenant_id != "default":
        raise HTTPException(status_code=403, detail="Only a default-tenant administrator can inspect tenants")
    result = await db.execute(select(TenantModel).order_by(TenantModel.id))
    return [{"tenant_id": tenant.id, "name": tenant.name, "is_active": tenant.is_active} for tenant in result.scalars()]


@router.patch("/auth/tenants/{tenant_id}/disable")
async def disable_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "operators:manage")
    if principal.role != "ADMIN" or principal.tenant_id != "default":
        raise HTTPException(status_code=403, detail="Only a default-tenant administrator can disable tenants")
    if tenant_id == "default":
        raise HTTPException(status_code=409, detail="The default tenant cannot be disabled")
    tenant = await db.get(TenantModel, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    tenant.is_active = False
    await append_audit(db, event_type="TENANT_DISABLED", tenant_id="default", actor_id=principal.operator_id, payload={"tenant_id": tenant.id}, commit=False)
    await db.commit()
    return {"tenant_id": tenant.id, "is_active": tenant.is_active}


@router.patch("/auth/operators/{operator_id}")
async def update_operator(
    operator_id: str,
    req: OperatorUpdateRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "operators:manage")
    if principal.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Only an administrator can update operators")
    if req.role is None and req.password is None:
        raise HTTPException(status_code=422, detail="Provide a new role or password")
    operator = await db.get(OperatorModel, operator_id)
    if not operator or operator.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Operator not found")
    if operator.id == principal.operator_id and req.role not in (None, "ADMIN"):
        raise HTTPException(status_code=409, detail="Administrators cannot demote their active account")
    if req.role is not None:
        operator.role = req.role
    if req.password is not None:
        operator.password_hash = hash_password(req.password)
    operator.updated_at = datetime.now(timezone.utc)
    await append_audit(db, event_type="OPERATOR_UPDATED", tenant_id=operator.tenant_id, actor_id=principal.operator_id, payload={"operator_id": operator.id, "role": operator.role, "password_changed": req.password is not None}, commit=False)
    await db.commit()
    return {"operator_id": operator.id, "username": operator.username, "role": operator.role, "tenant_id": operator.tenant_id}


@router.post("/auth/operators", status_code=201)
async def create_operator(
    req: OperatorCreateRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "operators:manage")
    if principal.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Only an administrator can create operators")
    if req.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Tenant does not exist or is outside the administrator scope")
    tenant = await db.get(TenantModel, req.tenant_id)
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=404, detail="Tenant does not exist or is inactive")
    operator = OperatorModel(
        tenant_id=req.tenant_id,
        username=req.username,
        password_hash=hash_password(req.password),
        role=req.role,
        is_active=True,
    )
    db.add(operator)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Username is already assigned") from exc
    await append_audit(db, event_type="OPERATOR_CREATED", tenant_id=req.tenant_id, actor_id=principal.operator_id, payload={"operator_id": operator.id, "role": req.role}, commit=False)
    await db.commit()
    return {"operator_id": operator.id, "username": operator.username, "role": operator.role, "tenant_id": operator.tenant_id}


@router.patch("/auth/operators/{operator_id}/disable")
async def disable_operator(
    operator_id: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    authorize(principal, "operators:manage")
    operator = await db.get(OperatorModel, operator_id)
    if not operator or operator.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="Operator not found")
    if operator.id == principal.operator_id:
        raise HTTPException(status_code=409, detail="Administrators cannot disable their active account")
    operator.is_active = False
    operator.updated_at = datetime.now(timezone.utc)
    await append_audit(db, event_type="OPERATOR_DISABLED", tenant_id=operator.tenant_id, actor_id=principal.operator_id, payload={}, commit=False)
    await db.commit()
    return {"operator_id": operator.id, "is_active": operator.is_active}
