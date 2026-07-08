from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backends.ollama_client import FENCED_CODE_RE, LANGUAGE_TAG_LINE_RE


DEFAULT_ARCHITECT_API_KEY_ENV = "ARCHITECT_API_KEY"
DEFAULT_DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_ARCHITECT_MODEL_ENV = "ARCHITECT_MODEL"
DEFAULT_ARCHITECT_API_BASE_URL_ENV = "ARCHITECT_API_BASE_URL"
DEFAULT_ARCHITECT_TIMEOUT_SECONDS_ENV = "ARCHITECT_TIMEOUT_SECONDS"
DEFAULT_ARCHITECT_TEMPERATURE_ENV = "ARCHITECT_TEMPERATURE"
DEFAULT_ARCHITECT_MAX_TOKENS_ENV = "ARCHITECT_MAX_TOKENS"
DEFAULT_ARCHITECT_THINKING_TYPE_ENV = "ARCHITECT_THINKING_TYPE"
DEFAULT_ARCHITECT_REASONING_EFFORT_ENV = "ARCHITECT_REASONING_EFFORT"
DEFAULT_ARCHITECT_MODEL = "deepseek-v4-pro"
DEFAULT_ARCHITECT_API_BASE_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_ARCHITECT_ENV_FILE = ".env"


def _dotenv_values(path: str) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class ArchitectConfig:
    api_key_env: str = DEFAULT_ARCHITECT_API_KEY_ENV
    fallback_api_key_env: str = DEFAULT_DEEPSEEK_API_KEY_ENV
    model_env: str = DEFAULT_ARCHITECT_MODEL_ENV
    base_url_env: str = DEFAULT_ARCHITECT_API_BASE_URL_ENV
    timeout_seconds_env: str = DEFAULT_ARCHITECT_TIMEOUT_SECONDS_ENV
    temperature_env: str = DEFAULT_ARCHITECT_TEMPERATURE_ENV
    max_tokens_env: str = DEFAULT_ARCHITECT_MAX_TOKENS_ENV
    thinking_type_env: str = DEFAULT_ARCHITECT_THINKING_TYPE_ENV
    reasoning_effort_env: str = DEFAULT_ARCHITECT_REASONING_EFFORT_ENV
    env_file: str = DEFAULT_ARCHITECT_ENV_FILE
    timeout_seconds: int = 120
    temperature: float = 0.1
    max_tokens: int = 4000
    thinking_type: str = "enabled"
    reasoning_effort: str = "high"

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def api_key(self) -> str:
        return (
            self._config_value(self.api_key_env)
            or self._config_value(self.fallback_api_key_env)
        )

    @property
    def model(self) -> str:
        return self._config_value(self.model_env) or DEFAULT_ARCHITECT_MODEL

    @property
    def base_url(self) -> str:
        return self._config_value(self.base_url_env) or DEFAULT_ARCHITECT_API_BASE_URL

    @property
    def request_timeout_seconds(self) -> int:
        return self._int_config_value(self.timeout_seconds_env, self.timeout_seconds, minimum=1)

    @property
    def request_temperature(self) -> float:
        return self._float_config_value(self.temperature_env, self.temperature, minimum=0.0)

    @property
    def request_max_tokens(self) -> int:
        return self._int_config_value(self.max_tokens_env, self.max_tokens, minimum=1)

    @property
    def request_thinking_type(self) -> str:
        return self._config_value(self.thinking_type_env) or self.thinking_type

    @property
    def request_reasoning_effort(self) -> str:
        return self._config_value(self.reasoning_effort_env) or self.reasoning_effort

    def _config_value(self, name: str) -> str:
        return os.environ.get(name, "").strip() or _dotenv_values(self.env_file).get(name, "").strip()

    def _int_config_value(self, name: str, default: int, minimum: int) -> int:
        value = self._config_value(name)
        if not value:
            return default
        try:
            parsed = int(value)
        except ValueError:
            return default
        return max(minimum, parsed)

    def _float_config_value(self, name: str, default: float, minimum: float) -> float:
        value = self._config_value(name)
        if not value:
            return default
        try:
            parsed = float(value)
        except ValueError:
            return default
        return max(minimum, parsed)


class ArchitectApiClient:
    """Small OpenAI-compatible chat completions client for architect escalation."""

    def __init__(self, config: ArchitectConfig | None = None) -> None:
        self.config = config or ArchitectConfig()

    def generate(self, prompt: str, system: str) -> str:
        if not self.config.api_key_configured:
            raise RuntimeError(
                "Architect model API key is not configured. "
                f"Set {self.config.api_key_env} or {self.config.fallback_api_key_env} "
                "before using big-LLM escalation."
            )
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.request_temperature,
            "max_tokens": self.config.request_max_tokens,
            "thinking": {"type": self.config.request_thinking_type},
            "reasoning_effort": self.config.request_reasoning_effort,
            "stream": False,
        }
        request = Request(
            self.config.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Architect API failed with HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Architect API is not reachable at {self.config.base_url}: {exc.reason}") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = body.get("choices", [{}])[0].get("text", "") if isinstance(body.get("choices"), list) else ""
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Architect API returned an empty response.")
        return content


class ArchitectModelSupplier:
    """Big-LLM repair supplier used after small-worker retries are exhausted.

    Credentials are read from environment variables, never committed files.
    The default client speaks OpenAI-compatible chat completions so providers
    can be swapped by changing ``ARCHITECT_API_BASE_URL`` and ``ARCHITECT_MODEL``.
    DeepSeek is the built-in default via ``DEEPSEEK_API_KEY``.
    """

    def __init__(
        self,
        config: ArchitectConfig | None = None,
        client: ArchitectApiClient | None = None,
        system_prompt: str = (
            "You are the architect repair tier in a verified code harness. "
            "Return code only. Preserve behavior while satisfying all engine constraints."
        ),
    ) -> None:
        self.config = config or ArchitectConfig()
        self.client = client or ArchitectApiClient(self.config)
        self.system_prompt = system_prompt

    def repair_draft(self, draft: str, retry_prompt: str) -> str:
        prompt = "\n".join(
            [
                "The small worker failed to produce compliant code.",
                "Use the current draft, engine feedback, behavioral failures, and diagnostic deltas to produce a corrected draft.",
                "Return only the full corrected source code.",
                "",
                "CURRENT DRAFT:",
                draft,
                "",
                "HARNESS FEEDBACK:",
                retry_prompt,
            ]
        )
        response = self.client.generate(prompt=prompt, system=self.system_prompt)
        return self._extract_code(response)

    def formalize_for_nagini(
        self,
        draft: str,
        spec_context: str = "",
        nagini_feedback: str = "",
    ) -> str:
        prompt = "\n".join(
            [
                "You are in ARCHITECT_FORMALIZATION state.",
                "Convert the target Python function into Nagini-verifiable Python.",
                "",
                "Requirements:",
                "- Preserve the intended behavior.",
                "- Add precise preconditions and postconditions where the specification supports them.",
                "- Use explicit type annotations.",
                "- Avoid dynamic Python features that make verification difficult.",
                "- Keep the function small and proof-friendly.",
                "- If full verification is not practical, return the strongest proof-friendly candidate code only.",
                "",
                "SPEC CONTEXT:",
                spec_context.strip() or "No additional spec context supplied.",
                "",
                "NAGINI FEEDBACK:",
                nagini_feedback.strip() or "No previous Nagini result supplied.",
                "",
                "TARGET CODE:",
                draft,
                "",
                "Return only the full formalization candidate source code.",
            ]
        )
        response = self.client.generate(
            prompt=prompt,
            system=(
                "You are the architect formal verification tier in a verified code harness. "
                "Return Nagini-oriented Python code only; do not include prose."
            ),
        )
        return self._extract_code(response)

    def _extract_code(self, response: str) -> str:
        match = FENCED_CODE_RE.search(response)
        text = match.group(1).strip() if match else response.strip()
        lines = text.splitlines()
        if lines and LANGUAGE_TAG_LINE_RE.match(lines[0]):
            text = "\n".join(lines[1:]).strip()
        return text
