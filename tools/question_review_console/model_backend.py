from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.question_review_console.codex_app_server import CodexAppServerClient


ROLE_NAMES = ("maintenance", "audit")
BACKEND_KINDS = ("codex_app_server", "openai_compatible_http")
_SECRET_FIELD = re.compile(
    r"(?:^|_)(?:api_?key|authorization|credential|password|secret|token)(?:$|_)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProcessingLimits:
    question_parallelism: int
    llm_call_concurrency: int
    audit_batch_questions: int
    audit_batch_input_bytes: int


@dataclass(frozen=True)
class BackendDefinition:
    name: str
    kind: str


@dataclass(frozen=True)
class RoleBinding:
    backend: str


@dataclass(frozen=True)
class ModelProfile:
    name: str
    roles: Mapping[str, RoleBinding]


@dataclass(frozen=True)
class ModelBackendConfig:
    version: int
    limits: ProcessingLimits
    backends: Mapping[str, BackendDefinition]
    profiles: Mapping[str, ModelProfile]
    fingerprint: str

    def profile(self, name: str) -> ModelProfile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise ValueError(f"未定義のLLM profileです: {name}") from exc


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field}は正の整数で指定してください。")
    return value


def _assert_secret_free(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _assert_secret_free(child, (*path, f"[{index}]"))
        return
    if not isinstance(value, Mapping):
        return
    for raw_key, child in value.items():
        key = str(raw_key)
        if _SECRET_FIELD.search(key):
            location = ".".join((*path, key))
            raise ValueError(
                f"LLM設定へsecretを保存できません: {location}"
            )
        _assert_secret_free(child, (*path, key))


def configuration_fingerprint(value: Mapping[str, Any]) -> str:
    """Return a stable fingerprint after proving the config has no secret fields."""
    _assert_secret_free(value)
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def parse_model_backend_config(raw: Mapping[str, Any]) -> ModelBackendConfig:
    _assert_secret_free(raw)
    version = _positive_int(raw.get("version"), "version")

    raw_limits = raw.get("limits")
    if not isinstance(raw_limits, Mapping):
        raise ValueError("limitsを設定してください。")
    limits = ProcessingLimits(
        question_parallelism=_positive_int(
            raw_limits.get("question_parallelism"), "question_parallelism"
        ),
        llm_call_concurrency=_positive_int(
            raw_limits.get("llm_call_concurrency"), "llm_call_concurrency"
        ),
        audit_batch_questions=_positive_int(
            raw_limits.get("audit_batch_questions"), "audit_batch_questions"
        ),
        audit_batch_input_bytes=_positive_int(
            raw_limits.get("audit_batch_input_bytes"), "audit_batch_input_bytes"
        ),
    )

    raw_backends = raw.get("backends")
    if not isinstance(raw_backends, Mapping) or not raw_backends:
        raise ValueError("backendsを設定してください。")
    backends: dict[str, BackendDefinition] = {}
    for name, value in raw_backends.items():
        if not isinstance(value, Mapping):
            raise ValueError(f"backend {name} の設定が不正です。")
        kind = value.get("kind")
        if kind not in BACKEND_KINDS:
            raise ValueError(f"backend {name} のkindが不正です: {kind}")
        backends[str(name)] = BackendDefinition(name=str(name), kind=str(kind))

    raw_profiles = raw.get("profiles")
    if not isinstance(raw_profiles, Mapping) or not raw_profiles:
        raise ValueError("profilesを設定してください。")
    profiles: dict[str, ModelProfile] = {}
    for name, value in raw_profiles.items():
        if not isinstance(value, Mapping) or not isinstance(value.get("roles"), Mapping):
            raise ValueError(f"profile {name} のrolesを設定してください。")
        raw_roles = value["roles"]
        if set(raw_roles) != set(ROLE_NAMES):
            raise ValueError(
                f"profile {name} はmaintenanceとauditの二役を設定してください。"
            )
        roles: dict[str, RoleBinding] = {}
        for role in ROLE_NAMES:
            role_value = raw_roles[role]
            if not isinstance(role_value, Mapping):
                raise ValueError(f"profile {name} の{role}設定が不正です。")
            backend = role_value.get("backend")
            if backend not in backends:
                raise ValueError(
                    f"profile {name} の{role}が未定義backendを参照しています: {backend}"
                )
            roles[role] = RoleBinding(backend=str(backend))
        profiles[str(name)] = ModelProfile(name=str(name), roles=roles)

    return ModelBackendConfig(
        version=version,
        limits=limits,
        backends=backends,
        profiles=profiles,
        fingerprint=configuration_fingerprint(raw),
    )


def load_model_backend_config(path: Path) -> ModelBackendConfig:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    return parse_model_backend_config(raw)


class CodexAppServerBackend:
    """Thin role-neutral wrapper; the existing client retains every safety gate."""

    kind = "codex_app_server"

    def __init__(self, client: CodexAppServerClient) -> None:
        self.client = client

    @property
    def configured(self) -> bool:
        return self.client.configured

    @property
    def provider(self) -> str:
        return self.client.provider

    def run_turn(self, prompt: str, **kwargs: Any) -> Any:
        return self.client.run_turn(prompt, **kwargs)

    def assert_subscription_access(self, *, force: bool = False) -> Any:
        return self.client.assert_subscription_access(force=force)

    def public_status(self, *, refresh: bool = False) -> Any:
        return self.client.public_status(refresh=refresh)


class CodexOnlyAdapter:
    """Resolve both model roles to one existing Codex App Server client."""

    def __init__(self, client: CodexAppServerClient) -> None:
        backend = CodexAppServerBackend(client)
        self._backends = {role: backend for role in ROLE_NAMES}

    def backend_for(self, role: str) -> CodexAppServerBackend:
        try:
            return self._backends[role]
        except KeyError as exc:
            raise ValueError(f"未定義のLLM roleです: {role}") from exc

    def run_turn(self, role: str, prompt: str, **kwargs: Any) -> Any:
        return self.backend_for(role).run_turn(prompt, **kwargs)

    def close(self) -> None:
        self.backend_for("maintenance").client.close()
