import asyncio
import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

import httpx
from pydantic import ValidationError

from backend.app.core.config import settings
from backend.app.core.errors import LLMProviderException
from backend.app.core.logging import logger
from backend.app.llm.base import LLMClient


class MistralLLMClient(LLMClient):
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        # Read credentials and model from the existing settings object; never log either key.
        self.api_key = api_key if api_key is not None else settings.MISTRAL_API_KEY
        self.model = model or settings.MISTRAL_MODEL
        self.endpoint = "https://api.mistral.ai/v1/chat/completions"
        self.last_usage: Dict[str, Any] = {}
        self.last_request_duration_ms: Optional[int] = None

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = settings.MISTRAL_RETRY_BACKOFF_SECONDS * (2 ** attempt)
        else:
            delay = settings.MISTRAL_RETRY_BACKOFF_SECONDS * (2 ** attempt)
        return min(max(0.0, delay), settings.MISTRAL_RETRY_MAX_BACKOFF_SECONDS)

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Optional[type] = None,
    ) -> Dict[str, Any]:
        if not self.api_key or "your_mistral_api_key" in self.api_key.lower():
            raise LLMProviderException(
                "Mistral API key is missing or unconfigured. Set MISTRAL_API_KEY in the local environment.",
                code="MISTRAL_CONFIGURATION_REQUIRED",
            )

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 512,
        }
        started = time.perf_counter()
        max_retries = max(0, min(settings.MISTRAL_MAX_RETRIES, 3))
        timeout = httpx.Timeout(max(1.0, settings.MISTRAL_TIMEOUT_SECONDS), connect=min(10.0, max(1.0, settings.MISTRAL_TIMEOUT_SECONDS)))

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = None
            for attempt in range(max_retries + 1):
                try:
                    response = await client.post(self.endpoint, headers=headers, json=payload)
                except httpx.TimeoutException as exc:
                    if attempt < max_retries:
                        delay = min(settings.MISTRAL_RETRY_BACKOFF_SECONDS * (2 ** attempt), settings.MISTRAL_RETRY_MAX_BACKOFF_SECONDS)
                        logger.warning("Mistral request timed out; retrying", model=self.model, attempt=attempt + 1, delay_seconds=delay)
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("Mistral request timed out", model=self.model, error_type=type(exc).__name__)
                    raise LLMProviderException(
                        "Mistral request timed out after the configured limit.",
                        code="MISTRAL_TIMEOUT",
                        retryable=True,
                    ) from None
                except httpx.HTTPError as exc:
                    if attempt < max_retries:
                        delay = min(settings.MISTRAL_RETRY_BACKOFF_SECONDS * (2 ** attempt), settings.MISTRAL_RETRY_MAX_BACKOFF_SECONDS)
                        logger.warning("Mistral transport failed; retrying", model=self.model, attempt=attempt + 1, delay_seconds=delay, error_type=type(exc).__name__)
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("Mistral transport failed", model=self.model, error_type=type(exc).__name__)
                    raise LLMProviderException(
                        f"Mistral transport failed ({type(exc).__name__}).",
                        code="MISTRAL_TRANSPORT_ERROR",
                        retryable=True,
                    ) from None

                if response.status_code == 200:
                    break

                if response.status_code == 429 or response.status_code >= 500:
                    retryable = True
                    code = "MISTRAL_RATE_LIMITED" if response.status_code == 429 else "MISTRAL_PROVIDER_UNAVAILABLE"
                    if attempt < max_retries:
                        delay = self._retry_after(response, attempt)
                        logger.warning("Mistral provider asked for a bounded retry", error_code=code, http_status=response.status_code, model=self.model, attempt=attempt + 1, delay_seconds=delay)
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("Mistral request exhausted retries", error_code=code, http_status=response.status_code, model=self.model)
                    raise LLMProviderException(
                        f"{code}: Mistral returned HTTP {response.status_code} after bounded retries.",
                        code=code,
                        retryable=retryable,
                    )

                if response.status_code in {401, 403}:
                    code = "MISTRAL_AUTHENTICATION_FAILED"
                else:
                    code = "MISTRAL_REQUEST_FAILED"
                logger.warning("Mistral request rejected", error_code=code, http_status=response.status_code, model=self.model)
                raise LLMProviderException(
                    f"{code}: Mistral returned HTTP {response.status_code}.",
                    code=code,
                )

        if response is None:
            raise LLMProviderException("Mistral returned no response.", code="MISTRAL_EMPTY_RESPONSE")
        if len(response.content) > settings.MISTRAL_MAX_RESPONSE_BYTES:
            raise LLMProviderException("Mistral response exceeded the configured size limit.", code="MISTRAL_RESPONSE_TOO_LARGE")

        try:
            data = response.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty structured response content")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("structured response must be a JSON object")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            logger.error("Mistral response was empty or malformed", model=self.model)
            raise LLMProviderException(
                "Mistral returned an empty or malformed structured response.",
                code="MISTRAL_MALFORMED_RESPONSE",
            ) from None

        if response_schema is not None:
            try:
                parsed = response_schema.model_validate(parsed).model_dump()
            except (ValidationError, AttributeError, TypeError):
                logger.error("Mistral response did not match the requested schema", model=self.model)
                raise LLMProviderException(
                    "Mistral response did not match the requested action schema.",
                    code="MISTRAL_SCHEMA_INVALID",
                ) from None

        usage = data.get("usage") or {}
        self.last_usage = {
            key: usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if usage.get(key) is not None
        }
        self.last_request_duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Mistral structured response received",
            model=self.model,
            duration_ms=self.last_request_duration_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
        return parsed
