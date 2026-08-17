from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from prompt.budget import estimate_tokens

if TYPE_CHECKING:
    from harness_kernel.tool_registry import ToolRegistry


DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
DEFAULT_OLLAMA_STARTUP_RETRY_ATTEMPTS = 3
DEFAULT_OLLAMA_STARTUP_RETRY_BACKOFF_SECONDS = 1.0
FENCED_CODE_RE = re.compile(r"```(?:[a-zA-Z0-9_+#.-]+)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
# A stray bare language-tag line (e.g. "cpp") some models emit without backticks.
LANGUAGE_TAG_LINE_RE = re.compile(r"^[ \t]*(?:python|py|cpp|c\+\+|cxx|cc|c)[ \t]*$", re.IGNORECASE)


@dataclass(frozen=True)
class OllamaGenerationConfig:
    temperature: float = 0.1
    num_predict: int = 512
    num_ctx: int = 8192


class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout_seconds: int = 120,
        keep_alive: str | None = None,
        startup_retry_attempts: int = DEFAULT_OLLAMA_STARTUP_RETRY_ATTEMPTS,
        startup_retry_backoff_seconds: float = DEFAULT_OLLAMA_STARTUP_RETRY_BACKOFF_SECONDS,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive
        self.startup_retry_attempts = max(1, startup_retry_attempts)
        self.startup_retry_backoff_seconds = max(0.0, startup_retry_backoff_seconds)
        self._opener = opener or urlopen
        self._sleep = sleep or time.sleep
        self.last_usage: dict[str, int] = {}

    def generate(
        self,
        prompt: str,
        model: str = DEFAULT_OLLAMA_MODEL,
        config: OllamaGenerationConfig | None = None,
        system: str | None = None,
    ) -> str:
        config = config or OllamaGenerationConfig()
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": config.temperature,
                "num_predict": config.num_predict,
                "num_ctx": config.num_ctx,
            },
        }
        if system:
            payload["system"] = system
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        body = self._request_with_startup_retry(request)
        result = body.get("response", "")
        if not isinstance(result, str) or not result.strip():
            raise RuntimeError("Ollama returned an empty response.")
        prompt_tokens = int(body.get("prompt_eval_count") or 0)
        completion_tokens = int(body.get("eval_count") or 0)
        self.last_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        return result

    def _request_with_startup_retry(self, request: Request) -> dict[str, Any]:
        for attempt in range(1, self.startup_retry_attempts + 1):
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, dict):
                    raise RuntimeError("Ollama returned a non-object response.")
                return body
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                retryable_startup_failure = (
                    exc.code in {500, 503}
                    and "timed out waiting for llama-server to start" in detail.casefold()
                )
                if not retryable_startup_failure or attempt >= self.startup_retry_attempts:
                    raise RuntimeError(f"Ollama generate failed with HTTP {exc.code}: {detail}") from exc
                self._sleep(self.startup_retry_backoff_seconds * attempt)
            except URLError as exc:
                raise RuntimeError(f"Ollama is not reachable at {self.base_url}: {exc.reason}") from exc
        raise RuntimeError("Ollama startup retry loop ended without a response.")


class OllamaModelSupplier:
    def __init__(
        self,
        client: OllamaClient | None = None,
        model: str = DEFAULT_OLLAMA_MODEL,
        config: OllamaGenerationConfig | None = None,
        tool_registry: ToolRegistry | None = None,
        system_prompt: str = (
            "You are a code repair backend. Return code only. "
            "Keep behavior intact while satisfying the stated constraints."
        ),
    ) -> None:
        self.client = client or OllamaClient()
        self.model = model
        self.config = config or OllamaGenerationConfig()
        self.system_prompt = system_prompt
        # Local inference has a resource cost, but not a provider-reported
        # dollar cost. Keep that distinction in telemetry so routing never
        # mistakes an unavailable price for a measured $0.00 observation.
        self.telemetry: list[dict[str, Any]] = []
        if tool_registry is None:
            from harness_kernel.tool_handlers import build_default_tool_registry

            tool_registry = build_default_tool_registry(ollama_client=self.client)
        self.tool_registry = tool_registry

    def generate_draft(self, prompt: str) -> str:
        response = self._generate(
            prompt=prompt,
            model=self.model,
            config=self._config_for_prompt(prompt),
            system=self.system_prompt,
        )
        self._record_usage("draft", prompt)
        return self._extract_code(response)

    def repair_draft(self, draft: str, retry_prompt: str) -> str:
        prompt = (
            "Refactor the current draft to satisfy the repair request.\n\n"
            f"{retry_prompt}\n"
        )
        response = self._generate(
            prompt=prompt,
            model=self.model,
            config=self._config_for_prompt(prompt),
            system=self.system_prompt,
        )
        self._record_usage("repair", prompt)
        return self._extract_code(response)

    def _record_usage(self, stage: str, prompt: str) -> None:
        usage = getattr(self.client, "last_usage", {})
        if not isinstance(usage, dict):
            usage = {}
        prompt_tokens = int(usage.get("prompt_tokens", estimate_tokens(prompt)) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        self.telemetry.append(
            {
                "stage": stage,
                "model": self.model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": int(
                    usage.get("total_tokens", prompt_tokens + completion_tokens)
                    or 0
                ),
                "estimated_cost_usd": None,
                "pricing_basis": "local_unpriced",
            }
        )

    def _generate(
        self,
        *,
        prompt: str,
        model: str,
        config: OllamaGenerationConfig,
        system: str | None,
    ) -> str:
        from harness_kernel.tool_handlers import (
            GenerateResponse,
            OllamaGenerateRequest,
        )

        result = self.tool_registry.dispatch(
            "ollama_generate",
            OllamaGenerateRequest(
                prompt=prompt,
                model=model,
                config=config,
                system=system,
            ),
        )
        if not result.ok or not isinstance(result.value, GenerateResponse):
            raise RuntimeError(
                f"Ollama tool failed ({result.error_kind or 'tool_error'}): "
                f"{result.error or 'no response'}"
            )
        return result.value.text

    def _config_for_prompt(self, prompt: str) -> OllamaGenerationConfig:
        target_predict = self.config.num_predict
        target_ctx = self.config.num_ctx
        prompt_chars = len(prompt)
        if prompt_chars > 24000:
            target_predict = max(target_predict, 2048)
            target_ctx = max(target_ctx, 16384)
        elif prompt_chars > 12000:
            target_predict = max(target_predict, 1536)
            target_ctx = max(target_ctx, 8192)
        elif prompt_chars > 6000:
            target_predict = max(target_predict, 1024)
            target_ctx = max(target_ctx, 4096)
        if target_predict == self.config.num_predict and target_ctx == self.config.num_ctx:
            return self.config
        return OllamaGenerationConfig(
            temperature=self.config.temperature,
            num_predict=target_predict,
            num_ctx=target_ctx,
        )

    def _extract_code(self, response: str) -> str:
        match = FENCED_CODE_RE.search(response)
        text = match.group(1).strip() if match else response.strip()
        if not match and text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        # Some models prefix the code with a bare language tag line (e.g. "cpp")
        # that is not inside backticks; drop it so it does not corrupt the draft.
        lines = text.splitlines()
        if lines and LANGUAGE_TAG_LINE_RE.match(lines[0]):
            text = "\n".join(lines[1:]).strip()
        return text
