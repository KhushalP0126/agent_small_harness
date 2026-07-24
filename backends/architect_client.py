from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backends.ollama_client import FENCED_CODE_RE, LANGUAGE_TAG_LINE_RE
from harness_kernel.function_contracts import ContractQueue, ContractQueuePlan, parse_contract_queue_json, parse_contract_queue_plan_json
from prompt.budget import continuation_prompt, estimate_tokens, looks_truncated_text
from prompt.contract_builder import build_contract_queue_planner_prompt, build_deal_contract_architect_prompt

if TYPE_CHECKING:
    from harness_kernel.tool_registry import ToolRegistry


DEFAULT_ARCHITECT_API_KEY_ENV = "ARCHITECT_API_KEY"
DEFAULT_DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_ARCHITECT_MODEL_ENV = "ARCHITECT_MODEL"
DEFAULT_ARCHITECT_API_BASE_URL_ENV = "ARCHITECT_API_BASE_URL"
DEFAULT_ARCHITECT_TIMEOUT_SECONDS_ENV = "ARCHITECT_TIMEOUT_SECONDS"
DEFAULT_ARCHITECT_TEMPERATURE_ENV = "ARCHITECT_TEMPERATURE"
DEFAULT_ARCHITECT_MAX_TOKENS_ENV = "ARCHITECT_MAX_TOKENS"
DEFAULT_ARCHITECT_THINKING_TYPE_ENV = "ARCHITECT_THINKING_TYPE"
DEFAULT_ARCHITECT_REASONING_EFFORT_ENV = "ARCHITECT_REASONING_EFFORT"
DEFAULT_ARCHITECT_RETRY_ATTEMPTS_ENV = "ARCHITECT_RETRY_ATTEMPTS"
DEFAULT_ARCHITECT_RETRY_BACKOFF_SECONDS_ENV = "ARCHITECT_RETRY_BACKOFF_SECONDS"
DEFAULT_ARCHITECT_MODEL = "deepseek-v4-pro"
DEFAULT_ARCHITECT_API_BASE_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_ARCHITECT_ENV_FILE = ".env"
DEFAULT_ARCHITECT_RETRY_ATTEMPTS = 3
DEFAULT_ARCHITECT_RETRY_BACKOFF_SECONDS = 0.5


@dataclass(frozen=True)
class ArchitectProfile:
    model: str
    timeout_seconds: int
    temperature: float
    max_tokens: int
    thinking_type: str
    reasoning_effort: str


@dataclass(frozen=True)
class ArchitectUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float = 0.0


CONTRACT_PROFILE = ArchitectProfile(
    model=DEFAULT_ARCHITECT_MODEL,
    timeout_seconds=60,
    temperature=0.0,
    max_tokens=3000,
    thinking_type="disabled",
    reasoning_effort="low",
)


REPAIR_PROFILE = ArchitectProfile(
    model=DEFAULT_ARCHITECT_MODEL,
    timeout_seconds=90,
    temperature=0.1,
    max_tokens=4000,
    thinking_type="disabled",
    reasoning_effort="medium",
)


class ContractArchitectError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
    retry_attempts_env: str = DEFAULT_ARCHITECT_RETRY_ATTEMPTS_ENV
    retry_backoff_seconds_env: str = DEFAULT_ARCHITECT_RETRY_BACKOFF_SECONDS_ENV
    env_file: str = DEFAULT_ARCHITECT_ENV_FILE
    repair_profile: ArchitectProfile = field(default_factory=lambda: REPAIR_PROFILE)
    contract_profile: ArchitectProfile = field(default_factory=lambda: CONTRACT_PROFILE)
    model_override: str | None = None
    base_url_override: str | None = None

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def api_key_source_env(self) -> str:
        if self._config_value(self.api_key_env):
            return self.api_key_env
        if self._config_value(self.fallback_api_key_env):
            return self.fallback_api_key_env
        return ""

    @property
    def api_key(self) -> str:
        return (
            self._config_value(self.api_key_env)
            or self._config_value(self.fallback_api_key_env)
        )

    @property
    def model(self) -> str:
        return self.repair_profile_from_env.model

    @property
    def base_url(self) -> str:
        return self.base_url_override or self._config_value(self.base_url_env) or DEFAULT_ARCHITECT_API_BASE_URL

    @property
    def request_timeout_seconds(self) -> int:
        return self.repair_profile_from_env.timeout_seconds

    @property
    def request_temperature(self) -> float:
        return self.repair_profile_from_env.temperature

    @property
    def request_max_tokens(self) -> int:
        return self.repair_profile_from_env.max_tokens

    @property
    def request_thinking_type(self) -> str:
        return self.repair_profile_from_env.thinking_type

    @property
    def request_reasoning_effort(self) -> str:
        return self.repair_profile_from_env.reasoning_effort

    @property
    def retry_attempts(self) -> int:
        return self._int_config_value(
            self.retry_attempts_env,
            DEFAULT_ARCHITECT_RETRY_ATTEMPTS,
            minimum=1,
        )

    @property
    def retry_backoff_seconds(self) -> float:
        return self._float_config_value(
            self.retry_backoff_seconds_env,
            DEFAULT_ARCHITECT_RETRY_BACKOFF_SECONDS,
            minimum=0.0,
        )

    @property
    def repair_profile_from_env(self) -> ArchitectProfile:
        return ArchitectProfile(
            model=self.model_override or self._config_value(self.model_env) or self.repair_profile.model,
            timeout_seconds=self._int_config_value(
                self.timeout_seconds_env,
                self.repair_profile.timeout_seconds,
                minimum=1,
            ),
            temperature=self._float_config_value(
                self.temperature_env,
                self.repair_profile.temperature,
                minimum=0.0,
            ),
            max_tokens=self._int_config_value(
                self.max_tokens_env,
                self.repair_profile.max_tokens,
                minimum=1,
            ),
            thinking_type=self._config_value(self.thinking_type_env) or self.repair_profile.thinking_type,
            reasoning_effort=(
                self._config_value(self.reasoning_effort_env) or self.repair_profile.reasoning_effort
            ),
        )

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

    def __init__(
        self,
        config: ArchitectConfig | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config or ArchitectConfig()
        self.last_usage: ArchitectUsage | None = None
        self._sleep = sleep or time.sleep

    def generate(self, prompt: str, system: str, profile: ArchitectProfile | None = None) -> str:
        if not self.config.api_key_configured:
            raise RuntimeError(
                "Architect model API key is not configured. "
                f"Set {self.config.api_key_env} or {self.config.fallback_api_key_env} "
                "before using big-LLM escalation."
            )
        profile = _profile_for_prompt(profile or self.config.repair_profile_from_env, prompt)
        payload: dict[str, Any] = {
            "model": profile.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "thinking": {"type": profile.thinking_type},
            "reasoning_effort": profile.reasoning_effort,
            "stream": False,
        }
        body = self._request_with_retries(payload, profile)

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = body.get("choices", [{}])[0].get("text", "") if isinstance(body.get("choices"), list) else ""
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Architect API returned an empty response.")
        self.last_usage = _usage_from_response(body, profile.model, prompt, content)
        return content

    def _request_with_retries(self, payload: dict[str, Any], profile: ArchitectProfile) -> dict[str, Any]:
        attempts = self.config.retry_attempts
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
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
                with urlopen(request, timeout=profile.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except socket.timeout as exc:
                last_error = exc
                if attempt >= attempts:
                    raise TimeoutError(
                        f"Architect API timed out after {profile.timeout_seconds}s "
                        f"({attempts} attempt{'s' if attempts != 1 else ''})"
                    ) from exc
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if not _is_retryable_http_status(exc.code) or attempt >= attempts:
                    raise RuntimeError(f"Architect API failed with HTTP {exc.code}: {detail}") from exc
                last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
            except URLError as exc:
                last_error = exc
                if attempt >= attempts:
                    raise RuntimeError(
                        f"Architect API is not reachable at {self.config.base_url} "
                        f"after {attempts} attempt{'s' if attempts != 1 else ''}: {exc.reason}"
                    ) from exc

            self._sleep(_retry_delay(self.config.retry_backoff_seconds, attempt))

        raise RuntimeError(f"Architect API request failed after {attempts} attempts: {last_error}")


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
        tool_registry: ToolRegistry | None = None,
        system_prompt: str = (
            "You are the architect repair tier in a verified code harness. "
            "Return code only. Preserve behavior while satisfying all engine constraints."
        ),
    ) -> None:
        self.config = config or ArchitectConfig()
        self.client = client or ArchitectApiClient(self.config)
        self.profile = self.config.repair_profile_from_env
        self.system_prompt = system_prompt
        self.telemetry: list[dict[str, Any]] = []
        if tool_registry is None:
            from harness_kernel.tool_handlers import build_default_tool_registry

            tool_registry = build_default_tool_registry(architect_client=self.client)
        self.tool_registry = tool_registry

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
        response = self._generate(prompt, self.system_prompt, self.profile)
        self._record_usage("repair", prompt)
        code = self._extract_code(response)
        if looks_truncated_text(code):
            continuation = self._generate(
                continuation_prompt(code),
                self.system_prompt,
                self.profile,
            )
            self._record_usage("repair_continuation", continuation_prompt(code))
            code = f"{code}\n{self._extract_code(continuation)}".strip()
        return code

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
        response = self._generate(
            prompt,
            (
                "You are the architect formal verification tier in a verified code harness. "
                "Return Nagini-oriented Python code only; do not include prose."
            ),
            self.profile,
        )
        self._record_usage("formalize", prompt)
        return self._extract_code(response)

    def _generate(
        self,
        prompt: str,
        system: str,
        profile: ArchitectProfile,
    ) -> str:
        from harness_kernel.tool_handlers import (
            ArchitectGenerateRequest,
            GenerateResponse,
        )

        result = self.tool_registry.dispatch(
            "architect_generate",
            ArchitectGenerateRequest(
                prompt=prompt,
                system=system,
                profile=profile,
            ),
        )
        if not result.ok or not isinstance(result.value, GenerateResponse):
            raise RuntimeError(
                f"Architect tool failed ({result.error_kind or 'tool_error'}): "
                f"{result.error or 'no response'}"
            )
        return result.value.text

    def _record_usage(self, stage: str, prompt: str) -> None:
        usage = getattr(self.client, "last_usage", None)
        if isinstance(usage, ArchitectUsage):
            payload = {
                "stage": stage,
                "model": usage.model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "estimated_cost_usd": usage.estimated_cost_usd,
            }
        else:
            payload = {
                "stage": stage,
                "model": self.profile.model,
                "prompt_tokens": estimate_tokens(prompt),
                "completion_tokens": 0,
                "total_tokens": estimate_tokens(prompt),
                "estimated_cost_usd": 0.0,
            }
        self.telemetry.append(payload)

    def _extract_code(self, response: str) -> str:
        match = FENCED_CODE_RE.search(response)
        text = match.group(1).strip() if match else response.strip()
        if not match and text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        lines = text.splitlines()
        if lines and LANGUAGE_TAG_LINE_RE.match(lines[0]):
            text = "\n".join(lines[1:]).strip()
        return text


class ContractArchitectSupplier:
    def __init__(
        self,
        client: ArchitectApiClient | None = None,
        profile: ArchitectProfile | None = None,
    ) -> None:
        self.profile = profile or CONTRACT_PROFILE
        self.client = client or ArchitectApiClient()
        self.last_response = ""
        self.telemetry: list[dict[str, Any]] = []

    def build_contract_queue(self, plan_packet: str, preserved_context: str = "") -> ContractQueue:
        prompt = build_deal_contract_architect_prompt(
            plan_packet=plan_packet,
            preserved_context=preserved_context,
        )
        try:
            response = self.client.generate(
                prompt=prompt,
                system="Return strict JSON contract queues only.",
                profile=self.profile,
            )
            self._record_usage("contract_queue", prompt)
        except TimeoutError as exc:
            raise ContractArchitectError("architect_contract_timeout", str(exc)) from exc
        except RuntimeError as exc:
            message = str(exc)
            if "empty response" in message.lower():
                raise ContractArchitectError("architect_contract_empty_response", message) from exc
            raise

        if not response.strip():
            raise ContractArchitectError("architect_contract_empty_response", "Architect returned an empty contract response.")
        try:
            queue = parse_contract_queue_json(response)
        except json.JSONDecodeError as exc:
            code = "architect_contract_truncated_json" if _looks_truncated_json(response) else "architect_contract_invalid_json"
            raise ContractArchitectError(code, str(exc)) from exc
        except ValueError as exc:
            raise ContractArchitectError("architect_contract_invalid_json", str(exc)) from exc
        if not queue.contracts:
            raise ContractArchitectError("architect_contract_zero_contracts", "Architect returned zero function contracts.")
        return queue

    def _record_usage(self, stage: str, prompt: str) -> None:
        usage = getattr(self.client, "last_usage", None)
        self.telemetry.append(_usage_payload(stage, usage, self.profile.model, prompt))


class ContractPlannerSupplier:
    def __init__(
        self,
        client: ArchitectApiClient | None = None,
        profile: ArchitectProfile | None = None,
    ) -> None:
        self.profile = profile or CONTRACT_PROFILE
        self.client = client or ArchitectApiClient()
        self.telemetry: list[dict[str, Any]] = []

    def build_contract_plan(
        self,
        plan_packet: str,
        preserved_context: str = "",
        available_contracts: list[str] | None = None,
    ) -> ContractQueuePlan:
        prompt = build_contract_queue_planner_prompt(
            plan_packet=plan_packet,
            preserved_context=preserved_context,
            available_contracts=available_contracts,
        )
        try:
            response = self.client.generate(
                prompt=prompt,
                system="Return compact JSON contract queue plans only.",
                profile=self.profile,
            )
            self._record_usage("contract_plan", prompt)
        except TimeoutError as exc:
            raise ContractArchitectError("architect_contract_plan_timeout", str(exc)) from exc
        except RuntimeError as exc:
            message = str(exc)
            if "empty response" in message.lower():
                raise ContractArchitectError("architect_contract_plan_empty_response", message) from exc
            raise

        if not response.strip():
            raise ContractArchitectError(
                "architect_contract_plan_empty_response",
                "Architect returned an empty contract plan response.",
            )
        self.last_response = response
        try:
            plan = parse_contract_queue_plan_json(response)
        except json.JSONDecodeError as exc:
            code = "architect_contract_plan_truncated_json" if _looks_truncated_json(response) else "architect_contract_plan_invalid_json"
            raise ContractArchitectError(code, str(exc)) from exc
        except ValueError as exc:
            raise ContractArchitectError("architect_contract_plan_invalid_json", str(exc)) from exc
        if not plan.contract_order and not plan.dependencies and not plan.contract_notes:
            raise ContractArchitectError("architect_contract_plan_zero_contracts", "Architect returned an empty contract plan.")
        return plan

    def _record_usage(self, stage: str, prompt: str) -> None:
        usage = getattr(self.client, "last_usage", None)
        self.telemetry.append(_usage_payload(stage, usage, self.profile.model, prompt))


def _looks_truncated_json(response: str) -> bool:
    stripped = response.strip()
    if stripped.startswith("```"):
        stripped = stripped.rstrip("`").strip()
    if not stripped.startswith("{"):
        return False
    return stripped.count("{") != stripped.count("}") or stripped.count("[") != stripped.count("]")


def _profile_for_prompt(profile: ArchitectProfile, prompt: str) -> ArchitectProfile:
    target = profile.max_tokens
    prompt_chars = len(prompt)
    if prompt_chars > 48000:
        target = max(target, 12000)
    elif prompt_chars > 24000:
        target = max(target, 8000)
    elif prompt_chars > 12000:
        target = max(target, 6000)
    target = min(target, 16000)
    if target == profile.max_tokens:
        return profile
    return ArchitectProfile(
        model=profile.model,
        timeout_seconds=profile.timeout_seconds,
        temperature=profile.temperature,
        max_tokens=target,
        thinking_type=profile.thinking_type,
        reasoning_effort=profile.reasoning_effort,
    )


def _is_retryable_http_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def _retry_delay(base_seconds: float, attempt: int) -> float:
    if base_seconds <= 0:
        return 0.0
    return base_seconds * (2 ** max(0, attempt - 1))


def _usage_from_response(body: dict[str, Any], model: str, prompt: str, completion: str) -> ArchitectUsage:
    raw_usage = body.get("usage", {}) if isinstance(body, dict) else {}
    prompt_tokens = _int_usage(raw_usage.get("prompt_tokens"), estimate_tokens(prompt))
    completion_tokens = _int_usage(raw_usage.get("completion_tokens"), estimate_tokens(completion))
    total_tokens = _int_usage(raw_usage.get("total_tokens"), prompt_tokens + completion_tokens)
    return ArchitectUsage(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _int_usage(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _usage_payload(stage: str, usage: Any, model: str, prompt: str) -> dict[str, Any]:
    if isinstance(usage, ArchitectUsage):
        return {
            "stage": stage,
            "model": usage.model,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "estimated_cost_usd": usage.estimated_cost_usd,
        }
    prompt_tokens = estimate_tokens(prompt)
    return {
        "stage": stage,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": 0,
        "total_tokens": prompt_tokens,
        "estimated_cost_usd": 0.0,
    }
