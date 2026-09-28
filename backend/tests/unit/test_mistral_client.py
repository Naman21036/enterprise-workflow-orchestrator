import json

import httpx
import pytest

from backend.app.core.config import settings
from backend.app.core.errors import LLMProviderException
from backend.app.discovery.actions import AgentAction
from backend.app.llm.mistral import MistralLLMClient


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, content=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload).encode()

    def json(self):
        return self._payload


class FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, headers, json):
        self.calls.append({"url": url, "headers": headers, "payload": json})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response_for_action(action):
    return FakeResponse(payload={
        "choices": [{"message": {"content": json.dumps(action)}}],
        "usage": {"prompt_tokens": 31, "completion_tokens": 12, "total_tokens": 43},
    })


@pytest.mark.asyncio
async def test_missing_mistral_key_is_classified_without_network(monkeypatch):
    client = MistralLLMClient(api_key="")
    with pytest.raises(LLMProviderException) as error:
        await client.generate_structured("system", "user")
    assert error.value.code == "MISTRAL_CONFIGURATION_REQUIRED"


@pytest.mark.asyncio
async def test_valid_response_uses_configured_model_and_schema(monkeypatch):
    fake = FakeAsyncClient([response_for_action({"action_type": "click", "selector": "#search-btn"})])
    monkeypatch.setattr("backend.app.llm.mistral.httpx.AsyncClient", lambda **_kwargs: fake)
    action = await MistralLLMClient(api_key="test-key", model="configured-model").generate_structured(
        "system", "user", response_schema=AgentAction
    )
    assert action["action_type"] == "click"
    assert fake.calls[0]["payload"]["model"] == "configured-model"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_rate_limit_retries_once_then_returns_structured_action(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse(status_code=429, payload={"error": "ignored"}, headers={"Retry-After": "0"}),
        response_for_action({"action_type": "complete"}),
    ])
    sleeps = []

    async def no_wait(delay):
        sleeps.append(delay)

    monkeypatch.setattr("backend.app.llm.mistral.httpx.AsyncClient", lambda **_kwargs: fake)
    monkeypatch.setattr("backend.app.llm.mistral.asyncio.sleep", no_wait)
    monkeypatch.setattr(settings, "MISTRAL_MAX_RETRIES", 1)
    action = await MistralLLMClient(api_key="test-key").generate_structured("system", "user", AgentAction)
    assert action["action_type"] == "complete"
    assert len(fake.calls) == 2
    assert sleeps == [0.0]


@pytest.mark.asyncio
async def test_authentication_error_is_not_retried(monkeypatch):
    fake = FakeAsyncClient([FakeResponse(status_code=401, payload={"error": "ignored"})])
    monkeypatch.setattr("backend.app.llm.mistral.httpx.AsyncClient", lambda **_kwargs: fake)
    monkeypatch.setattr(settings, "MISTRAL_MAX_RETRIES", 2)
    with pytest.raises(LLMProviderException) as error:
        await MistralLLMClient(api_key="test-key").generate_structured("system", "user")
    assert error.value.code == "MISTRAL_AUTHENTICATION_FAILED"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_malformed_response_and_invalid_action_schema_are_classified(monkeypatch):
    malformed = FakeAsyncClient([FakeResponse(payload={"choices": [{"message": {"content": "not-json"}}]})])
    monkeypatch.setattr("backend.app.llm.mistral.httpx.AsyncClient", lambda **_kwargs: malformed)
    with pytest.raises(LLMProviderException) as malformed_error:
        await MistralLLMClient(api_key="test-key").generate_structured("system", "user")
    assert malformed_error.value.code == "MISTRAL_MALFORMED_RESPONSE"

    invalid_schema = FakeAsyncClient([response_for_action({"action_type": "transmit_money"})])
    monkeypatch.setattr("backend.app.llm.mistral.httpx.AsyncClient", lambda **_kwargs: invalid_schema)
    with pytest.raises(LLMProviderException) as schema_error:
        await MistralLLMClient(api_key="test-key").generate_structured("system", "user", AgentAction)
    assert schema_error.value.code == "MISTRAL_SCHEMA_INVALID"


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_classified(monkeypatch):
    fake = FakeAsyncClient([httpx.ReadTimeout("private detail")])
    monkeypatch.setattr("backend.app.llm.mistral.httpx.AsyncClient", lambda **_kwargs: fake)
    monkeypatch.setattr(settings, "MISTRAL_MAX_RETRIES", 0)
    with pytest.raises(LLMProviderException) as error:
        await MistralLLMClient(api_key="test-key").generate_structured("system", "user")
    assert error.value.code == "MISTRAL_TIMEOUT"
    assert "private detail" not in str(error.value)
