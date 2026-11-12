"""Provider-agnostic LLM wrapper.

``get_llm()`` returns an object with ``complete_json`` (structured extraction) and ``complete``
(free text). Providers:

* ``mock`` - default whenever no API key is configured. Returns empty results and says so
  (``LLMResult.is_mock``) so the UI can label them. It never invents data.
* ``anthropic`` - Claude through the official ``anthropic`` SDK (installed from
  requirements-ai.txt). Uses server-side refusal fallbacks so a declined request is retried
  on a fallback model instead of failing.

Adding another provider means writing one class with the same two methods.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class LLMResult:
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    model: str = "mock"
    is_mock: bool = True
    error: str | None = None


class LLM(Protocol):
    name: str

    async def complete(self, system: str, prompt: str, *, max_tokens: int = 2000) -> LLMResult: ...

    async def complete_json(self, system: str, prompt: str, schema: dict[str, Any], *,
                            max_tokens: int = 2000) -> LLMResult: ...


class MockLLM:
    """No key configured: returns nothing rather than fake answers."""

    name = "mock"

    async def complete(self, system: str, prompt: str, *, max_tokens: int = 2000) -> LLMResult:
        return LLMResult(text="", model="mock", is_mock=True)

    async def complete_json(self, system: str, prompt: str, schema: dict[str, Any], *,
                            max_tokens: int = 2000) -> LLMResult:
        return LLMResult(data={}, model="mock", is_mock=True)


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic  # optional dependency (requirements-ai.txt)

        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=60.0)
        self.model = model

    async def _create(self, **kwargs: Any) -> Any:
        return await self._client.beta.messages.create(
            model=self.model,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low", **kwargs.pop("output_config", {})},
            **kwargs,
        )

    @staticmethod
    def _text(resp: Any) -> str:
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    async def complete(self, system: str, prompt: str, *, max_tokens: int = 2000) -> LLMResult:
        try:
            resp = await self._create(max_tokens=max_tokens, system=system,
                                      messages=[{"role": "user", "content": prompt}])
        except self._anthropic.APIError as exc:
            log.warning("LLM call failed: %s", type(exc).__name__)
            return LLMResult(model=self.model, is_mock=False, error=type(exc).__name__)
        if resp.stop_reason == "refusal":
            return LLMResult(model=resp.model, is_mock=False, error="refusal")
        return LLMResult(text=self._text(resp), model=resp.model, is_mock=False)

    async def complete_json(self, system: str, prompt: str, schema: dict[str, Any], *,
                            max_tokens: int = 2000) -> LLMResult:
        try:
            resp = await self._create(
                max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": prompt}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except self._anthropic.APIError as exc:
            log.warning("LLM JSON call failed: %s", type(exc).__name__)
            return LLMResult(model=self.model, is_mock=False, error=type(exc).__name__)
        if resp.stop_reason == "refusal":
            return LLMResult(model=resp.model, is_mock=False, error="refusal")
        try:
            data = json.loads(self._text(resp) or "{}")
        except json.JSONDecodeError:
            return LLMResult(model=resp.model, is_mock=False, error="bad_json")
        return LLMResult(data=data if isinstance(data, dict) else {}, model=resp.model, is_mock=False)


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        s = get_settings()
        if s.llm_provider == "anthropic":
            try:
                _llm = AnthropicLLM(s.llm_api_key, s.llm_model)
            except ImportError:
                log.warning("anthropic package not installed; using the mock LLM")
                _llm = MockLLM()
        else:
            _llm = MockLLM()
        log.info("LLM provider: %s", _llm.name)
    return _llm


def set_llm(llm: LLM | None) -> None:
    """Tests use this to inject a fake provider."""
    global _llm
    _llm = llm
