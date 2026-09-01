from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backends.architect_client import CONTRACT_PROFILE, REPAIR_PROFILE, ArchitectProfile
from validation.policy import DEFAULT_POLICY


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


class ConfigError(ValueError):
    """Raised when the harness configuration is malformed."""


@dataclass(frozen=True)
class PlatformConfig:
    environment: str = "local"
    log_level: str = "INFO"


@dataclass(frozen=True)
class SyntaxConfig:
    enabled: bool = True
    strict_mode: bool = True


@dataclass(frozen=True)
class PolicyConfig:
    max_loop_depth: int = 2
    max_cyclomatic_complexity: int = 7
    allow_explicit_globals: bool = False
    allow_module_state_mutation: bool = False
    allow_external_dependencies: bool = False
    allow_unknown_registered_apis: bool = False
    allow_unsafe_calls: bool = False
    allow_algorithmic_hotspots: bool = False
    allow_bounds_warnings: bool = True
    allow_state_flow_warnings: bool = False
    allow_lint_errors: bool = False
    allow_lint_skips: bool = False
    allow_import_risk_hard_block: bool = False
    allow_import_risk_advisory_block: bool = True

    def to_validation_policy(self) -> dict[str, Any]:
        policy = dict(DEFAULT_POLICY)
        policy.update(
            {
                "max_loop_depth": self.max_loop_depth,
                "max_cyclomatic_complexity": self.max_cyclomatic_complexity,
                "allow_explicit_globals": self.allow_explicit_globals,
                "allow_module_state_mutation": self.allow_module_state_mutation,
                "allow_external_dependencies": self.allow_external_dependencies,
                "allow_unknown_registered_apis": self.allow_unknown_registered_apis,
                "allow_unsafe_calls": self.allow_unsafe_calls,
                "allow_algorithmic_hotspots": self.allow_algorithmic_hotspots,
                "allow_bounds_warnings": self.allow_bounds_warnings,
                "allow_state_flow_warnings": self.allow_state_flow_warnings,
                "allow_lint_errors": self.allow_lint_errors,
                "allow_lint_skips": self.allow_lint_skips,
                "allow_import_risk_hard_block": self.allow_import_risk_hard_block,
                "allow_import_risk_advisory_block": self.allow_import_risk_advisory_block,
            }
        )
        return policy


@dataclass(frozen=True)
class BehaviorConfig:
    enabled: bool = True
    timeout_seconds: float = 1.0
    execution_trace: bool = True
    debugger_hints: bool = True


@dataclass(frozen=True)
class FormalConfig:
    crosshair_enabled: bool = False
    crosshair_timeout_seconds: float = 3.0


@dataclass(frozen=True)
class EnginesConfig:
    syntax: SyntaxConfig = field(default_factory=SyntaxConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    formal: FormalConfig = field(default_factory=FormalConfig)


@dataclass(frozen=True)
class ModelsConfig:
    worker_model: str = "qwen2.5-coder:1.5b"
    architect_model: str = "deepseek-v4-pro"
    profiles: dict[str, str] = field(
        default_factory=lambda: {
            "tiny": "qwen2.5-coder:1.5b",
            "daily_driver": "qwen2.5-coder:1.5b",
            "architect": "deepseek-v4-pro",
        }
    )
    difficulty_models: dict[str, str] = field(
        default_factory=lambda: {
            "1-2": "qwen2.5-coder:1.5b",
            "3-5": "qwen2.5-coder:1.5b",
            "6+": "qwen2.5-coder:1.5b",
        }
    )

    def resolve_worker_model(self, profile: str | None = None) -> str:
        if profile is None or not profile.strip():
            return self.worker_model
        name = profile.strip()
        try:
            return self.profiles[name]
        except KeyError as exc:
            available = ", ".join(sorted(self.profiles)) or "none"
            raise ConfigError(f"Unknown model profile '{name}'. Available profiles: {available}.") from exc

    def resolve_for_difficulty(self, difficulty: int) -> str:
        for range_text, model in self.difficulty_models.items():
            if _difficulty_matches(difficulty, range_text):
                return model
        return self.worker_model


@dataclass(frozen=True)
class RoutingConfig:
    architect_after_repair_attempts: int | None = 1
    complexity_threshold: float = 0.7
    allow_architect_repair_retry: bool = False


@dataclass(frozen=True)
class GatesConfig:
    auto_repair: bool = True
    max_retries: int = 1
    manual_review_required: bool = True


@dataclass(frozen=True)
class ArchitectProfilesConfig:
    contract: ArchitectProfile = field(default_factory=lambda: CONTRACT_PROFILE)
    repair: ArchitectProfile = field(default_factory=lambda: REPAIR_PROFILE)


@dataclass(frozen=True)
class ExecutionConfig:
    models: ModelsConfig = field(default_factory=ModelsConfig)
    architect: ArchitectProfilesConfig = field(default_factory=ArchitectProfilesConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    gates: GatesConfig = field(default_factory=GatesConfig)


@dataclass(frozen=True)
class HarnessConfig:
    platform: PlatformConfig = field(default_factory=PlatformConfig)
    engines: EnginesConfig = field(default_factory=EnginesConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "HarnessConfig":
        _ensure_known_keys(raw, {"platform", "engines", "execution"}, "config")
        platform = _mapping(raw.get("platform", {}), "platform")
        engines = _mapping(raw.get("engines", {}), "engines")
        execution = _mapping(raw.get("execution", {}), "execution")
        _ensure_known_keys(platform, {"environment", "log_level"}, "platform")
        _ensure_known_keys(engines, {"syntax", "policy", "behavior", "formal"}, "engines")
        _ensure_known_keys(execution, {"models", "architect", "routing", "gates"}, "execution")

        syntax = _mapping(engines.get("syntax", {}), "engines.syntax")
        policy = _mapping(engines.get("policy", {}), "engines.policy")
        behavior = _mapping(engines.get("behavior", {}), "engines.behavior")
        formal = _mapping(engines.get("formal", {}), "engines.formal")
        models = _mapping(execution.get("models", {}), "execution.models")
        architect = _mapping(execution.get("architect", {}), "execution.architect")
        routing = _mapping(execution.get("routing", {}), "execution.routing")
        gates = _mapping(execution.get("gates", {}), "execution.gates")
        _ensure_known_keys(syntax, {"enabled", "strict_mode"}, "engines.syntax")
        _ensure_known_keys(
            policy,
            {
                "max_loop_depth",
                "max_cyclomatic_complexity",
                "allow_explicit_globals",
                "allow_module_state_mutation",
                "allow_external_dependencies",
                "allow_unknown_registered_apis",
                "allow_unsafe_calls",
                "allow_algorithmic_hotspots",
                "allow_bounds_warnings",
                "allow_state_flow_warnings",
                "allow_lint_errors",
                "allow_lint_skips",
                "allow_import_risk_hard_block",
                "allow_import_risk_advisory_block",
            },
            "engines.policy",
        )
        _ensure_known_keys(
            behavior,
            {"enabled", "timeout_seconds", "execution_trace", "debugger_hints"},
            "engines.behavior",
        )
        _ensure_known_keys(
            formal,
            {"crosshair_enabled", "crosshair_timeout_seconds"},
            "engines.formal",
        )
        _ensure_known_keys(
            models,
            {"worker_model", "architect_model", "profiles", "difficulty_models"},
            "execution.models",
        )
        _ensure_known_keys(architect, {"contract", "repair"}, "execution.architect")
        contract_profile = _mapping(architect.get("contract", {}), "execution.architect.contract")
        repair_profile = _mapping(architect.get("repair", {}), "execution.architect.repair")
        _ensure_known_keys(
            contract_profile,
            {"model", "timeout_seconds", "temperature", "max_tokens", "thinking_type", "reasoning_effort"},
            "execution.architect.contract",
        )
        _ensure_known_keys(
            repair_profile,
            {"model", "timeout_seconds", "temperature", "max_tokens", "thinking_type", "reasoning_effort"},
            "execution.architect.repair",
        )
        _ensure_known_keys(
            routing,
            {
                "architect_after_repair_attempts",
                "complexity_threshold",
                "allow_architect_repair_retry",
            },
            "execution.routing",
        )
        _ensure_known_keys(
            gates,
            {"auto_repair", "max_retries", "manual_review_required"},
            "execution.gates",
        )

        return cls(
            platform=PlatformConfig(
                environment=_str(platform, "environment", "local"),
                log_level=_str(platform, "log_level", "INFO"),
            ),
            engines=EnginesConfig(
                syntax=SyntaxConfig(
                    enabled=_bool(syntax, "enabled", True),
                    strict_mode=_bool(syntax, "strict_mode", True),
                ),
                policy=PolicyConfig(
                    max_loop_depth=_int(policy, "max_loop_depth", 2, minimum=0),
                    max_cyclomatic_complexity=_int(
                        policy, "max_cyclomatic_complexity", 7, minimum=1
                    ),
                    allow_explicit_globals=_bool(policy, "allow_explicit_globals", False),
                    allow_module_state_mutation=_bool(policy, "allow_module_state_mutation", False),
                    allow_external_dependencies=_bool(policy, "allow_external_dependencies", False),
                    allow_unknown_registered_apis=_bool(
                        policy, "allow_unknown_registered_apis", False
                    ),
                    allow_unsafe_calls=_bool(policy, "allow_unsafe_calls", False),
                    allow_algorithmic_hotspots=_bool(policy, "allow_algorithmic_hotspots", False),
                    allow_bounds_warnings=_bool(policy, "allow_bounds_warnings", True),
                    allow_state_flow_warnings=_bool(policy, "allow_state_flow_warnings", False),
                    allow_lint_errors=_bool(policy, "allow_lint_errors", False),
                    allow_lint_skips=_bool(policy, "allow_lint_skips", False),
                    allow_import_risk_hard_block=_bool(policy, "allow_import_risk_hard_block", False),
                    allow_import_risk_advisory_block=_bool(
                        policy, "allow_import_risk_advisory_block", True
                    ),
                ),
                behavior=BehaviorConfig(
                    enabled=_bool(behavior, "enabled", True),
                    timeout_seconds=_float(behavior, "timeout_seconds", 1.0, minimum=0.1),
                    execution_trace=_bool(behavior, "execution_trace", True),
                    debugger_hints=_bool(behavior, "debugger_hints", True),
                ),
                formal=FormalConfig(
                    crosshair_enabled=_bool(formal, "crosshair_enabled", False),
                    crosshair_timeout_seconds=_float(
                        formal, "crosshair_timeout_seconds", 3.0, minimum=0.1
                    ),
                ),
            ),
            execution=ExecutionConfig(
                models=ModelsConfig(
                    worker_model=_str(models, "worker_model", "qwen2.5-coder:1.5b"),
                    architect_model=_str(models, "architect_model", "deepseek-v4-pro"),
                    profiles=_str_mapping(
                        models,
                        "profiles",
                        {
                            "tiny": "qwen2.5-coder:1.5b",
                            "daily_driver": "qwen2.5-coder:1.5b",
                            "architect": "deepseek-v4-pro",
                        },
                    ),
                    difficulty_models=_str_mapping(
                        models,
                        "difficulty_models",
                        {
                            "1-2": "qwen2.5-coder:1.5b",
                            "3-5": "qwen2.5-coder:1.5b",
                            "6+": "qwen2.5-coder:1.5b",
                        },
                    ),
                ),
                architect=ArchitectProfilesConfig(
                    contract=_architect_profile(contract_profile, CONTRACT_PROFILE, "execution.architect.contract"),
                    repair=_architect_profile(repair_profile, REPAIR_PROFILE, "execution.architect.repair"),
                ),
                routing=RoutingConfig(
                    architect_after_repair_attempts=_optional_int(
                        routing, "architect_after_repair_attempts", 1, minimum=0
                    ),
                    complexity_threshold=_float(
                        routing, "complexity_threshold", 0.7, minimum=0.0
                    ),
                    allow_architect_repair_retry=_bool(
                        routing, "allow_architect_repair_retry", False
                    ),
                ),
                gates=GatesConfig(
                    auto_repair=_bool(gates, "auto_repair", True),
                    max_retries=_int(gates, "max_retries", 1, minimum=0),
                    manual_review_required=_bool(gates, "manual_review_required", True),
                ),
            ),
        )


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> HarnessConfig:
    config_path = Path(path)
    if not config_path.exists():
        return HarnessConfig()
    raw = parse_yaml_subset(config_path.read_text(encoding="utf-8"))
    return HarnessConfig.from_mapping(raw)


def parse_yaml_subset(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw_line).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in raw_line[:indent]:
            raise ConfigError(f"Tabs are not supported in config indentation at line {line_number}.")
        stripped = line.strip()
        if ":" not in stripped:
            raise ConfigError(f"Expected 'key: value' at line {line_number}.")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ConfigError(f"Empty config key at line {line_number}.")
        while indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)
    return root


def _strip_comment(line: str) -> str:
    quote = ""
    for index, char in enumerate(line):
        if char in {"'", '"'} and (index == 0 or line[index - 1] != "\\"):
            quote = "" if quote == char else char
        if char == "#" and not quote:
            return line[:index]
    return line


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ConfigError(f"Invalid inline list value: {value}") from exc
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping.")
    return value


def _ensure_known_keys(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"{path} contains unknown keys: {', '.join(unknown)}")


def _str(raw: dict[str, Any], key: str, default: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string.")
    return value.strip()


def _str_mapping(raw: dict[str, Any], key: str, default: dict[str, str]) -> dict[str, str]:
    value = raw.get(key, default)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping of names to model tags.")
    result: dict[str, str] = {}
    for profile_name, model_name in value.items():
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ConfigError(f"{key} contains an invalid profile name.")
        if not isinstance(model_name, str) or not model_name.strip():
            raise ConfigError(f"{key}.{profile_name} must be a non-empty string.")
        result[profile_name.strip()] = model_name.strip()
    return result


def _bool(raw: dict[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false.")
    return value


def _int(raw: dict[str, Any], key: str, default: int, minimum: int | None = None) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer.")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}.")
    return value


def _optional_int(
    raw: dict[str, Any], key: str, default: int | None, minimum: int | None = None
) -> int | None:
    value = raw.get(key, default)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer or null.")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}.")
    return value


def _float(raw: dict[str, Any], key: str, default: float, minimum: float | None = None) -> float:
    value = raw.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"{key} must be numeric.")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}.")
    return value


def _architect_profile(raw: dict[str, Any], default: ArchitectProfile, path: str) -> ArchitectProfile:
    return ArchitectProfile(
        model=_str(raw, "model", default.model),
        timeout_seconds=_int(raw, "timeout_seconds", default.timeout_seconds, minimum=1),
        temperature=_float(raw, "temperature", default.temperature, minimum=0.0),
        max_tokens=_int(raw, "max_tokens", default.max_tokens, minimum=1),
        thinking_type=_str(raw, "thinking_type", default.thinking_type),
        reasoning_effort=_str(raw, "reasoning_effort", default.reasoning_effort),
    )


def _difficulty_matches(difficulty: int, range_text: str) -> bool:
    text = str(range_text).strip()
    if text.endswith("+"):
        try:
            return difficulty >= int(text[:-1])
        except ValueError as exc:
            raise ConfigError(f"Invalid difficulty model range '{text}'.") from exc
    if "-" in text:
        left, right = text.split("-", 1)
        try:
            return int(left) <= difficulty <= int(right)
        except ValueError as exc:
            raise ConfigError(f"Invalid difficulty model range '{text}'.") from exc
    try:
        return difficulty == int(text)
    except ValueError as exc:
        raise ConfigError(f"Invalid difficulty model range '{text}'.") from exc
