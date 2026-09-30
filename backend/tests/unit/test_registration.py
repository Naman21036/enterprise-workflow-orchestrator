import pytest

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.api.v1.endpoints import auth as auth_routes
from backend.app.core.config import settings
from backend.app.db.database import Base, get_db
from backend.app.db.models import BootstrapStateModel, OperatorModel, TenantModel
from backend.app.security.auth import hash_password, verify_password


@pytest.mark.asyncio
async def test_self_service_registration_login_and_rejection_cases(monkeypatch):
    monkeypatch.setattr(settings, "APEX_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "APEX_JWT_SIGNING_KEY", "test-signing-key-" * 3)
    monkeypatch.setattr(settings, "APEX_JWT_ISSUER", "test-issuer")
    monkeypatch.setattr(settings, "APEX_JWT_AUDIENCE", "test-audience")
    auth_routes._registration_attempts.clear()
    auth_routes._login_attempts.clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_db():
        async with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    async with sessions() as db:
        db.add(TenantModel(id="default", name="Default", is_active=True))
        db.add(OperatorModel(
            tenant_id="default", username="admin", full_name="Tenant Admin", email="admin@example.test",
            password_hash=hash_password("bootstrap-password-123"), role="ADMIN", is_active=True,
        ))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        bad_login = await client.post("/api/v1/auth/login", json={"username": "missing", "password": "not-the-password"})
        assert bad_login.status_code == 401
        assert bad_login.json()["detail"] == "Username or password is incorrect"
        wrong_password = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
        assert wrong_password.status_code == 401
        assert wrong_password.json()["detail"] == bad_login.json()["detail"]
        unauthenticated = await client.get("/api/v1/auth/operators")
        assert unauthenticated.status_code == 401
        payload = {
            "full_name": "New User", "username": "New.User", "email": "NEW.USER@example.test",
            "password": "a-strong-password-123", "confirm_password": "a-strong-password-123",
        }
        forbidden_role = await client.post("/api/v1/auth/register", json={**payload, "role": "ADMIN"})
        assert forbidden_role.status_code == 422
        forbidden_tenant = await client.post("/api/v1/auth/register", json={**payload, "tenant_id": "other-tenant"})
        assert forbidden_tenant.status_code == 422
        legacy_invitation = await client.post("/api/v1/auth/register", json={**payload, "invitation_token": "x" * 32})
        assert legacy_invitation.status_code == 422
        missing_fields = await client.post("/api/v1/auth/register", json={"username": "incomplete"})
        assert missing_fields.status_code == 422
        invalid_email = await client.post("/api/v1/auth/register", json={**payload, "email": "bad-email"})
        assert invalid_email.status_code == 422
        weak_password = await client.post("/api/v1/auth/register", json={**payload, "password": "short", "confirm_password": "short"})
        assert weak_password.status_code == 422
        mismatch = await client.post("/api/v1/auth/register", json={**payload, "confirm_password": "different-password"})
        assert mismatch.status_code == 422

        registered = await client.post("/api/v1/auth/register", json=payload)
        assert registered.status_code == 201
        assert registered.json()["role"] == "OPERATOR"
        assert registered.json()["tenant_id"] == "default"
        async with sessions() as db:
            operator = await db.scalar(select(OperatorModel).where(OperatorModel.username == "new.user"))
            assert operator is not None
            assert operator.full_name == "New User"
            assert operator.email == "new.user@example.test"
            assert operator.role == "OPERATOR"
            assert operator.tenant_id == "default"
            assert operator.password_hash != payload["password"]
            assert verify_password(payload["password"], operator.password_hash)

        email_login = await client.post("/api/v1/auth/login", json={"username": "new.user@example.test", "password": payload["password"]})
        assert email_login.status_code == 200
        current = await client.get("/api/v1/auth/me")
        assert current.status_code == 200
        assert current.json()["username"] == "new.user"
        assert current.json()["role"] == "OPERATOR"
        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert (await client.get("/api/v1/auth/me")).status_code == 401

        duplicate_email_payload = {**payload, "username": "another-user"}
        duplicate_email = await client.post("/api/v1/auth/register", json=duplicate_email_payload)
        assert duplicate_email.status_code == 400
        assert duplicate_email.json()["detail"] == registered_generic_message()

        duplicate_username_payload = {
            **payload, "email": "different.user@example.test",
        }
        duplicate_username = await client.post("/api/v1/auth/register", json=duplicate_username_payload)
        assert duplicate_username.status_code == 400
        assert duplicate_username.json()["detail"] == registered_generic_message()

        invalid_payload = {**payload, "username": "another-person", "email": "another@example.test"}
        auth_routes._registration_attempts.clear()
        for index in range(auth_routes._REGISTRATION_ATTEMPT_LIMIT):
            accepted = await client.post("/api/v1/auth/register", json={
                **invalid_payload,
                "username": f"another-person-{index}",
                "email": f"another-{index}@example.test",
            })
            assert accepted.status_code == 201
        registration_limited = await client.post("/api/v1/auth/register", json=invalid_payload)
        assert registration_limited.status_code == 429

        auth_routes._login_attempts.clear()
        for _ in range(auth_routes._LOGIN_ATTEMPT_LIMIT):
            limited_login = await client.post("/api/v1/auth/login", json={"username": "unknown", "password": "wrong"})
            assert limited_login.status_code == 401
        assert (await client.post("/api/v1/auth/login", json={"username": "unknown", "password": "wrong"})).status_code == 429

    await engine.dispose()


def registered_generic_message():
    return "Registration could not be completed. Check your details or contact your administrator."


@pytest.mark.asyncio
async def test_bootstrap_admin_is_one_time_and_idempotent(monkeypatch):
    monkeypatch.setattr(settings, "APEX_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "APEX_JWT_SIGNING_KEY", "test-signing-key-" * 3)
    monkeypatch.setattr(settings, "APEX_BOOTSTRAP_ADMIN_USERNAME", "Initial.Admin")
    monkeypatch.setattr(settings, "APEX_BOOTSTRAP_ADMIN_FULL_NAME", "Initial Admin")
    monkeypatch.setattr(settings, "APEX_BOOTSTRAP_ADMIN_EMAIL", "INITIAL.ADMIN@example.test")
    monkeypatch.setattr(settings, "APEX_BOOTSTRAP_ADMIN_PASSWORD_HASH", hash_password("initial-admin-password-123"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(TenantModel(id="default", name="Default", is_active=True))
        await db.commit()
        await auth_routes.ensure_bootstrap_operator(db)
        await auth_routes.ensure_bootstrap_operator(db)
        monkeypatch.setattr(settings, "APEX_BOOTSTRAP_ADMIN_USERNAME", "attacker-reset")
        monkeypatch.setattr(settings, "APEX_BOOTSTRAP_ADMIN_PASSWORD_HASH", hash_password("attacker-password-123"))
        await auth_routes.ensure_bootstrap_operator(db)
        operators = list((await db.execute(select(OperatorModel))).scalars())
        assert len(operators) == 1
        assert operators[0].username == "initial.admin"
        assert operators[0].email == "initial.admin@example.test"
        assert operators[0].role == "ADMIN"
        assert verify_password("initial-admin-password-123", operators[0].password_hash)
        assert await db.get(BootstrapStateModel, "initial_admin") is not None
        await db.delete(operators[0])
        await db.commit()
        with pytest.raises(RuntimeError, match="already been used"):
            await auth_routes.ensure_bootstrap_operator(db)
    await engine.dispose()
