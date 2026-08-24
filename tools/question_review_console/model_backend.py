from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
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
    endpoint: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: float = 120.0
    auth_env: str | None = None


@dataclass(frozen=True)
class RoleBinding:
    backend: str


@dataclass(frozen=True)
class ModelProfile:
    name: str
    roles: Mapping[str, RoleBinding]


def _safe_http_endpoint(value: Any, *, auth_env: Any) -> tuple[str, str | None]:
    endpoint = str(value or "").strip()
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HTTP backendのendpointはhttp(s) URLで指定してください。")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("HTTP backendのendpointへ認証情報又はfragmentを含められません。")
    forbidden = {"key", "api_key", "apikey", "token", "access_token", "auth", "authorization"}
    if any(key.lower() in forbidden for key, _ in urllib.parse.parse_qsl(parsed.query)):
        raise ValueError("HTTP backendのendpointへcredential queryを含められません。")
    auth_name = str(auth_env or "").strip() or None
    if auth_name and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", auth_name):
        raise ValueError("HTTP backendのauth_envが不正です。")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if not loopback and not auth_name:
        raise ValueError("loopback以外のHTTP backendにはauth_envが必要です。")
    return endpoint, auth_name


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
        if kind == "openai_compatible_http":
            endpoint, auth_env = _safe_http_endpoint(
                value.get("endpoint", "http://127.0.0.1:11434/v1/chat/completions"),
                auth_env=value.get("auth_env"),
            )
            model = str(value.get("model") or "local-model").strip()
            timeout = value.get("timeout_seconds", 120)
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                raise ValueError(f"backend {name} のtimeout_secondsが不正です。")
            backends[str(name)] = BackendDefinition(
                name=str(name), kind=str(kind), endpoint=endpoint, model=model,
                reasoning_effort=str(value.get("reasoning_effort") or "").strip() or None,
                timeout_seconds=float(timeout), auth_env=auth_env,
            )
        else:
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


def _validate_json_shape(value: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Validate the closed subset emitted by this application's output schemas."""
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(expected, str) and not matches.get(expected, False):
        raise ValueError(f"{path}の型がschemaと一致しません。")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}の値がschemaと一致しません。")
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        for key in schema.get("required") or []:
            if key not in value:
                raise ValueError(f"{path}.{key}がありません。")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ValueError(f"{path}に未定義fieldがあります。")
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, Mapping):
                _validate_json_shape(child, child_schema, f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ValueError(f"{path}の件数が不足しています。")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ValueError(f"{path}の件数が上限を超えています。")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, child in enumerate(value):
                _validate_json_shape(child, item_schema, f"{path}[{index}]")


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


class OpenAICompatibleHTTPBackend:
    """Small, fail-closed chat-completions client with no tool or file access."""

    kind = "openai_compatible_http"
    provider = "OpenAI compatible HTTP"
    configured = True

    def __init__(self, definition: BackendDefinition) -> None:
        self.definition = definition

    def run_turn(self, prompt: str, **kwargs: Any) -> Any:
        sandbox = str(kwargs.get("sandbox") or "read-only")
        if sandbox != "read-only" or kwargs.get("tools") or kwargs.get("writable_roots") or kwargs.get("writableRoots"):
            raise ValueError("HTTP backendはread-only JSON生成だけを許可します。")
        schema = kwargs.get("output_schema")
        if not isinstance(schema, Mapping):
            raise ValueError("HTTP backendにはoutput_schemaが必要です。")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.definition.auth_env:
            secret = os.environ.get(self.definition.auth_env)
            if not secret:
                raise ValueError("HTTP backendの認証環境変数が設定されていません。")
            headers["Authorization"] = f"Bearer {secret}"
        payload: dict[str, Any] = {
            "model": self.definition.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "question_maintenance_result", "strict": True, "schema": schema},
            },
        }
        if self.definition.reasoning_effort:
            payload["reasoning_effort"] = self.definition.reasoning_effort
        request = urllib.request.Request(
            str(self.definition.endpoint), data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.definition.timeout_seconds) as response:
                if response.status < 200 or response.status >= 300:
                    raise ValueError(f"HTTP backendが失敗しました (status {response.status})。")
                raw = response.read(2_000_001)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"HTTP backendが失敗しました (status {exc.code})。") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError("HTTP backendへ接続できませんでした。") from None
        if len(raw) > 2_000_000:
            raise ValueError("HTTP backendの応答が2MBを超えました。")
        try:
            envelope = json.loads(raw)
            if not isinstance(envelope, Mapping) or str(envelope.get("model") or "") != self.definition.model:
                raise ValueError
            choices = envelope.get("choices")
            message = choices[0]["message"] if isinstance(choices, list) and len(choices) == 1 else None
            content = message.get("content") if isinstance(message, Mapping) else None
            result = json.loads(content) if isinstance(content, str) else content
            if not isinstance(result, Mapping):
                raise ValueError
        except (KeyError, TypeError, json.JSONDecodeError, ValueError):
            raise ValueError("HTTP backendの応答形式又はmodelが契約と一致しません。") from None
        try:
            _validate_json_shape(result, schema)
        except ValueError:
            raise ValueError("HTTP backendの応答JSONがschemaと一致しません。") from None
        call_id = uuid.uuid4().hex
        thread_id = f"http-thread-{call_id}"
        session_id = f"http-session-{call_id}"
        turn_id = f"http-turn-{call_id}"
        for callback_name, callback_args in (
            ("on_thread_started", (thread_id, session_id)),
            ("on_turn_started", (thread_id, turn_id)),
        ):
            callback = kwargs.get(callback_name)
            if callable(callback):
                callback(*callback_args)
        return SimpleNamespace(
            final_message=json.dumps(result, ensure_ascii=False), changed_files=[],
            thread_id=thread_id, session_id=session_id, turn_id=turn_id,
            model=self.definition.model,
            service_tier=None, reasoning_effort=self.definition.reasoning_effort,
            completion_mode="final_message", model_turn_started_at=None,
            model_turn_finished_at=None,
            subagent_thread_ids=[],
        )


class ProfileModelRouter:
    """Stateless per-run profile dispatch over one shared call semaphore."""

    def __init__(self, config: ModelBackendConfig, codex_client: CodexAppServerClient) -> None:
        self.config = config
        self.codex_client = codex_client
        self._call_slots = threading.BoundedSemaphore(
            config.limits.llm_call_concurrency
        )
        self._instances: dict[str, Any] = {}
        for name, definition in config.backends.items():
            if definition.kind == "codex_app_server":
                self._instances[name] = CodexAppServerBackend(codex_client)
            else:
                self._instances[name] = OpenAICompatibleHTTPBackend(definition)

    def backend_for(self, profile_name: str, role: str) -> Any:
        profile = self.config.profile(profile_name)
        binding = profile.roles.get(role)
        if binding is None:
            raise ValueError(f"未定義のLLM roleです: {role}")
        return self._instances[binding.backend]

    def run_turn(self, prompt: str, **kwargs: Any) -> Any:
        profile_name = str(kwargs.pop("model_profile", "") or "")
        work_type = str(kwargs.get("work_type") or "")
        if not profile_name and work_type.startswith("maintenance"):
            raise ValueError("model_profileを明示してください。")
        if not profile_name:
            profile_name = "codex_only"
        role = "audit" if "evaluation" in work_type or "audit" in work_type else "maintenance"
        with self._call_slots:
            return self.backend_for(profile_name, role).run_turn(prompt, **kwargs)

    def snapshot_for(self, profile_name: str) -> dict[str, Any]:
        profile = self.config.profile(profile_name)
        snapshot = {
            "name": profile_name,
            "limits": {
                "questionParallelism": self.config.limits.question_parallelism,
                "llmCallConcurrency": self.config.limits.llm_call_concurrency,
                "auditBatchQuestions": self.config.limits.audit_batch_questions,
                "auditBatchInputBytes": self.config.limits.audit_batch_input_bytes,
            },
            "roles": {
                role: {
                    "backend": binding.backend,
                    "kind": definition.kind,
                    "model": definition.model,
                    "reasoningEffort": definition.reasoning_effort,
                    "timeoutSeconds": definition.timeout_seconds,
                }
                for role, binding in profile.roles.items()
                for definition in [self.config.backends[binding.backend]]
            },
        }
        snapshot["fingerprint"] = configuration_fingerprint(snapshot)
        return snapshot

    def provider_for(self, profile_name: str, role: str = "maintenance") -> str:
        return str(self.backend_for(profile_name, role).provider)

    def assert_profile_access(self, profile_name: str, *, force: bool = False) -> None:
        seen: set[int] = set()
        for role in ROLE_NAMES:
            backend = self.backend_for(profile_name, role)
            if id(backend) in seen:
                continue
            seen.add(id(backend))
            check = getattr(backend, "assert_subscription_access", None)
            if callable(check):
                check(force=force)

    @property
    def configured(self) -> bool:
        return all(bool(backend.configured) for backend in self._instances.values())

    @property
    def provider(self) -> str:
        return "Profile model router"

    def public_status(self, *, refresh: bool = False) -> dict[str, Any]:
        status = dict(self.codex_client.public_status(refresh=refresh))
        status["modelProfiles"] = {
            name: self.snapshot_for(name) for name in self.config.profiles
        }
        status["modelLimits"] = self.snapshot_for(
            next(iter(self.config.profiles))
        )["limits"]
        return status

    def __getattr__(self, name: str) -> Any:
        return getattr(self.codex_client, name)
