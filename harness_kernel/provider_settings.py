"""Secure provider configuration for the private TUI bridge.

Secrets are deliberately represented separately from serializable settings.  No
implementation in this module writes a credential to a regular file.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol
from urllib.parse import urlsplit


SERVICE_NAME = "agent-coder-structure"


class CredentialStore(Protocol):
    def get(self, provider: str) -> str | None: ...
    def set(self, provider: str, credential: str) -> None: ...
    def clear(self, provider: str) -> None: ...


class SessionCredentialStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, provider: str) -> str | None:
        return self._values.get(provider)

    def set(self, provider: str, credential: str) -> None:
        if credential:
            self._values[provider] = credential

    def clear(self, provider: str) -> None:
        value = self._values.pop(provider, None)
        # Best-effort removal; Python strings cannot be reliably zeroized.
        del value


class MacOSKeychainStore:
    def _account(self, provider: str) -> str:
        return f"provider:{provider}"

    def get(self, provider: str) -> str | None:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE_NAME, "-a", self._account(provider), "-w"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return result.stdout.rstrip("\n") if result.returncode == 0 else None

    def set(self, provider: str, credential: str) -> None:
        # With ``-w`` as the final option, security reads the password from
        # stdin. Keeping it out of argv prevents exposure through process lists.
        result = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", SERVICE_NAME, "-a", self._account(provider), "-w"],
            input=credential, capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode:
            raise RuntimeError("macOS Keychain rejected the credential")

    def clear(self, provider: str) -> None:
        subprocess.run(
            ["security", "delete-generic-password", "-s", SERVICE_NAME, "-a", self._account(provider)],
            capture_output=True, text=True, timeout=10, check=False,
        )


class SecretServiceStore:
    def get(self, provider: str) -> str | None:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", SERVICE_NAME, "provider", provider],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return result.stdout.rstrip("\n") if result.returncode == 0 else None

    def set(self, provider: str, credential: str) -> None:
        result = subprocess.run(
            ["secret-tool", "store", "--label", f"{SERVICE_NAME} {provider}", "service", SERVICE_NAME, "provider", provider],
            input=credential, capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode:
            raise RuntimeError("Secret Service rejected the credential")

    def clear(self, provider: str) -> None:
        subprocess.run(
            ["secret-tool", "clear", "service", SERVICE_NAME, "provider", provider],
            capture_output=True, text=True, timeout=10, check=False,
        )


def default_credential_store() -> CredentialStore:
    if shutil.which("security") and os.uname().sysname == "Darwin":
        return MacOSKeychainStore()
    if shutil.which("secret-tool"):
        return SecretServiceStore()
    return SessionCredentialStore()


class Provider(str, Enum):
    QWEN = "qwen"
    DEEPSEEK = "deepseek"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ProviderSettings:
    provider: Provider
    endpoint: str
    model: str
    cost_cap_usd: float = 1.0
    local_development_confirmed: bool = False

    def validate(self) -> None:
        if self.cost_cap_usd < 0:
            raise ValueError("cost cap must be non-negative")
        parsed = urlsplit(self.endpoint)
        if parsed.username or parsed.password:
            raise ValueError("credentials embedded in endpoint URLs are forbidden")
        host = (parsed.hostname or "").casefold()
        local = host in {"localhost", "127.0.0.1", "::1"}
        if local and not self.local_development_confirmed:
            raise ValueError("localhost requires explicit local-development confirmation")
        if not local and parsed.scheme != "https":
            raise ValueError("remote provider endpoints must use HTTPS")
        if local and parsed.scheme not in {"http", "https"}:
            raise ValueError("local provider endpoints must use HTTP or HTTPS")
        if not host:
            raise ValueError("provider endpoint requires a hostname")
        if parsed.fragment or parsed.query:
            raise ValueError("provider endpoints cannot contain query strings or fragments")


def resolve_credential(
    provider: Provider,
    *,
    environment: Mapping[str, str],
    store: CredentialStore,
    dotenv: Mapping[str, str],
    session: CredentialStore,
) -> tuple[str | None, str]:
    names = {
        Provider.DEEPSEEK: ("DEEPSEEK_API_KEY", "ARCHITECT_API_KEY"),
        Provider.OPENAI_COMPATIBLE: ("OPENAI_API_KEY", "ARCHITECT_API_KEY"),
        Provider.QWEN: (),
    }[provider]
    for name in names:
        if value := environment.get(name, "").strip():
            return value, "environment"
    if value := store.get(provider.value):
        return value, "credential_store"
    for name in names:
        if value := dotenv.get(name, "").strip():
            return value, "dotenv"
    if value := session.get(provider.value):
        return value, "session"
    return None, "unconfigured"


def credential_metadata(value: str | None) -> dict[str, object]:
    if not value:
        return {"configured": False, "last_four": "", "fingerprint_prefix": ""}
    return {
        "configured": True,
        "last_four": value[-4:],
        "fingerprint_prefix": hashlib.sha256(value.encode()).hexdigest()[:10],
    }


def redact_secret(text: str, secrets: list[str]) -> str:
    result = text
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    return result
