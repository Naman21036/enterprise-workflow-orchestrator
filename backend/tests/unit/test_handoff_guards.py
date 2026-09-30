from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.app.api.v1.endpoints.handoff import OperatorActionRequest, resume_run_after_handoff, submit_operator_action
from backend.app.escalation.manager import session_manager
from backend.app.security.auth import Principal


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return []


class FakeDB:
    def __init__(self, run=None, record=None):
        self.run = run
        self.record = record
        self.commits = 0

    async def get(self, model, run_id):
        return self.run

    async def execute(self, statement):
        return ScalarResult(self.record)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    def add(self, _item):
        pass


class FakeSurface:
    class Page:
        @staticmethod
        def is_closed():
            return False

    page = Page()

    @staticmethod
    async def current_url():
        return "http://localhost:3001/member/1002"


def clear_session(run_id):
    session_manager._active_surfaces.pop(run_id, None)
    session_manager._handoff_states.pop(run_id, None)
    session_manager._run_locks.pop(run_id, None)


@pytest.mark.asyncio
async def test_operator_action_rejects_nonexistent_run():
    run_id = "missing-run"
    clear_session(run_id)
    with pytest.raises(HTTPException) as error:
        await submit_operator_action(run_id, OperatorActionRequest(action_type="click", params={"selector": "#search-btn"}), FakeDB(), principal=Principal("op", "operator", "ADMIN", "default"))
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_operator_action_requires_live_handoff_and_rejects_unsafe_selector(monkeypatch):
    run_id = "blocked-run"
    clear_session(run_id)
    run = SimpleNamespace(status="BLOCKED", tenant_id="default")
    record = SimpleNamespace(status="AWAITING_HUMAN", state="AWAITING_OPERATOR", tenant_id="default", version=1, expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)+__import__("datetime").timedelta(minutes=30), details_json={}, id="session-123", checkpoint_id="cp-123", operator_actions_json=[])
    db = FakeDB(run, record)
    session_manager._active_surfaces[run_id] = FakeSurface()
    session_manager._handoff_states[run_id] = {"status": "AWAITING_HUMAN", "operator_actions": []}
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(session_manager, "execute_operator_action", execute)
    try:
        with pytest.raises(HTTPException) as error:
            await submit_operator_action(run_id, OperatorActionRequest(action_type="click", action_id="unsafe-123", params={"selector": "#transfer-btn"}), db, principal=Principal("op", "operator", "ADMIN", "default"))
        assert error.value.status_code == 403
        execute.assert_not_awaited()
        assert db.commits == 1  # The denied policy decision is durably audited.
    finally:
        clear_session(run_id)


@pytest.mark.asyncio
async def test_repeated_action_id_returns_saved_result_without_reexecution(monkeypatch):
    run_id = "blocked-duplicate"
    clear_session(run_id)
    run = SimpleNamespace(status="BLOCKED", tenant_id="default")
    record = SimpleNamespace(status="AWAITING_HUMAN", state="AWAITING_OPERATOR", tenant_id="default", version=1, expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)+__import__("datetime").timedelta(minutes=30), details_json={}, id="session-123", checkpoint_id="cp-123", operator_actions_json=[{"action_id": "request-123", "status": "SUCCESS", "screenshot_path": "evidence/escalations/after.png"}])
    db = FakeDB(run, record)
    session_manager._active_surfaces[run_id] = FakeSurface()
    session_manager._handoff_states[run_id] = {"status": "AWAITING_HUMAN", "operator_actions": []}
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(session_manager, "execute_operator_action", execute)
    try:
        result = await submit_operator_action(
            run_id,
            OperatorActionRequest(action_type="click", action_id="request-123", params={"selector": "#search-btn"}),
            db,
            principal=Principal("op", "operator", "ADMIN", "default"),
        )
        assert result == {"success": True, "duplicate": True, "in_progress": False, "screenshot_path": "evidence/escalations/after.png"}
        execute.assert_not_awaited()
        assert db.commits == 0
    finally:
        clear_session(run_id)


@pytest.mark.asyncio
async def test_resume_rejects_run_without_active_handoff():
    run_id = "already-finished"
    clear_session(run_id)
    run = SimpleNamespace(status="SUCCESS", tenant_id="default", execution_mode="Deterministic Replay")
    record = SimpleNamespace(status="RESUMED", checkpoint_id="cp-123")
    with pytest.raises(HTTPException) as error:
        await resume_run_after_handoff(run_id, db=FakeDB(run, record), principal=Principal("op", "operator", "ADMIN", "default"))
    assert error.value.status_code == 409
