"""Minimal HS256 JWT handling with database-backed operator/tenant claims."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.database import get_db
from backend.app.db.models import OperatorModel, TenantModel


ROLES = {"ADMIN", "OPERATOR", "VIEWER"}
ROLE_PERMISSIONS = {
    "VIEWER": {"runs:read", "recordings:read", "handoff:read", "audit:read", "capabilities:read", "safety:read"},
    "OPERATOR": {"runs:read", "runs:create", "runs:resume", "runs:cancel", "recordings:read", "handoff:read", "handoff:act", "audit:read", "capabilities:read", "safety:read"},
    "ADMIN": {"*"},
}


@dataclass(frozen=True)
class Principal:
    operator_id: str
    username: str
    role: str
    tenant_id: str


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signing_key() -> bytes:
    key = settings.APEX_JWT_SIGNING_KEY.encode("utf-8")
    if settings.APEX_AUTH_ENABLED and len(key) < 32:
        raise HTTPException(status_code=503, detail="Authentication signing key is not configured")
    return key


def issue_access_token(operator: OperatorModel, *, now: int | None = None) -> tuple[str, int]:
    issued_at = now if now is not None else int(time.time())
    expires_at = issued_at + max(5, settings.APEX_JWT_TTL_MINUTES) * 60
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": operator.id,
        "iss": settings.APEX_JWT_ISSUER,
        "aud": settings.APEX_JWT_AUDIENCE,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": secrets.token_urlsafe(18),
    }
    body = f"{_b64encode(json.dumps(header, separators=(',', ':')).encode())}.{_b64encode(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = _b64encode(hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}", expires_at


def decode_access_token(token: str, *, now: int | None = None) -> dict[str, Any]:
    try:
        header_part, payload_part, signature_part = token.split(".")
        header = json.loads(_b64decode(header_part))
        payload = json.loads(_b64decode(payload_part))
        if not isinstance(header, dict) or header != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("unsupported JWT header")
        body = f"{header_part}.{payload_part}"
        expected = hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature_part)):
            raise ValueError("invalid signature")
        current = now if now is not None else int(time.time())
        if payload.get("iss") != settings.APEX_JWT_ISSUER:
            raise ValueError("invalid issuer")
        audience = payload.get("aud")
        if audience != settings.APEX_JWT_AUDIENCE and not (isinstance(audience, list) and settings.APEX_JWT_AUDIENCE in audience):
            raise ValueError("invalid audience")
        if not isinstance(payload.get("sub"), str) or not payload["sub"]:
            raise ValueError("missing subject")
        if not isinstance(payload.get("iat"), int) or payload["iat"] > current + 30:
            raise ValueError("invalid issue time")
        if not isinstance(payload.get("nbf"), int) or payload["nbf"] > current + 30:
            raise ValueError("token not active")
        if not isinstance(payload.get("exp"), int) or payload["exp"] <= current:
            raise ValueError("token expired")
        if not isinstance(payload.get("jti"), str) or len(payload["jti"]) < 16:
            raise ValueError("missing token identifier")
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Bearer token is invalid or expired", headers={"WWW-Authenticate": "Bearer"}) from exc


def hash_password(password: str, *, iterations: int = 310_000) -> str:
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("Password must be between 12 and 1024 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        if rounds < 100_000 or rounds > 2_000_000:
            return False
        calculated = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64decode(salt_text), rounds)
        return hmac.compare_digest(calculated, _b64decode(digest_text))
    except (TypeError, ValueError, OverflowError):
        return False


def valid_password_hash(encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = encoded.split("$", 3)
        rounds = int(rounds_text)
        return algorithm == "pbkdf2_sha256" and 100_000 <= rounds <= 2_000_000 and len(_b64decode(salt_text)) == 16 and len(_b64decode(digest_text)) == 32
    except (TypeError, ValueError, OverflowError):
        return False


async def get_current_principal(
    authorization: str | None = Header(default=None),
    apex_session: str | None = Cookie(default=None, alias="apex_session"),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    if not settings.APEX_AUTH_ENABLED:
        return Principal("local-development", "local-development", "ADMIN", "default")
    token = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else apex_session
    if not token:
        raise HTTPException(status_code=401, detail="Bearer authentication is required", headers={"WWW-Authenticate": "Bearer"})
    claims = decode_access_token(token)
    result = await db.execute(
        select(OperatorModel, TenantModel)
        .join(TenantModel, TenantModel.id == OperatorModel.tenant_id)
        .where(OperatorModel.id == claims["sub"])
    )
    identity = result.one_or_none()
    operator, tenant = identity if identity else (None, None)
    if not operator or not operator.is_active or not tenant or not tenant.is_active:
        raise HTTPException(status_code=401, detail="Operator account is disabled or unavailable", headers={"WWW-Authenticate": "Bearer"})
    return Principal(operator.id, operator.username, operator.role, operator.tenant_id)


def authorize(principal: Principal, permission: str) -> None:
    permissions = ROLE_PERMISSIONS.get(principal.role, set())
    if "*" not in permissions and permission not in permissions:
        raise HTTPException(status_code=403, detail="The authenticated role cannot perform this operation")
