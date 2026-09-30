from types import SimpleNamespace

import pytest

from backend.app.safety.policy import RiskLevel, SafetyPolicy
from backend.app.surfaces.playwright import PlaywrightWebSurface


def test_url_policy_rejects_lookalike_hosts_ports_and_encoded_traversal():
    policy = SafetyPolicy(allowed_domains=["localhost", "127.0.0.1"])
    assert not policy.validate_url("http://localhost.evil.example/member/1002")
    assert not policy.validate_url("http://127.0.0.2/member/1002")
    assert not policy.validate_url("http://localhost:8000/member/1002")
    assert not policy.validate_url("http://localhost/member/../admin")
    assert not policy.validate_url("http://localhost/member/%2e%2e/admin")
    assert not policy.validate_url("http://user:password@localhost/member/1002")


def test_write_and_confirmation_actions_are_not_safe_by_default():
    policy = SafetyPolicy()
    assert policy.classify_action_risk("click", "#search-btn") == RiskLevel.SAFE
    assert policy.classify_action_risk("click", "#confirm-dialog-btn") == RiskLevel.RISKY
    assert policy.classify_action_risk("press_key", text_value="Enter") == RiskLevel.RISKY
    assert not policy.validate_action("click", "http://localhost:3001", selector="#transfer-btn")[0]


@pytest.mark.asyncio
async def test_top_level_redirect_request_is_aborted_before_navigation():
    surface = PlaywrightWebSurface()
    page = SimpleNamespace()
    main_frame = SimpleNamespace(page=page)
    page.main_frame = main_frame
    surface.page = page

    class Request:
        url = "https://attacker.example/collect"
        frame = main_frame

        @staticmethod
        def is_navigation_request():
            return True

    class Route:
        aborted = False
        continued = False

        async def abort(self, _reason):
            self.aborted = True

        async def continue_(self):
            self.continued = True

    route = Route()
    await surface._guard_navigation(route, Request())
    assert route.aborted
    assert not route.continued
    assert surface.blocked_navigation_url == Request.url


@pytest.mark.asyncio
async def test_subresource_request_does_not_change_top_level_navigation_policy():
    surface = PlaywrightWebSurface()
    surface.page = SimpleNamespace(main_frame=object())

    class Request:
        url = "https://cdn.example/static.js"
        frame = object()

        @staticmethod
        def is_navigation_request():
            return False

    class Route:
        continued = False

        async def abort(self, _reason):
            raise AssertionError("non-navigation resource must not trigger navigation denial")

        async def continue_(self):
            self.continued = True

    route = Route()
    await surface._guard_navigation(route, Request())
    assert route.continued
