from __future__ import annotations

import json
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.question_review_console.model_backend import (
    ModelBackendError,
    ProfileModelRouter,
    parse_model_backend_config,
)


class _Codex:
    configured = True
    provider = "Codex App Server"

    def run_turn(self, prompt, **kwargs):
        return (prompt, kwargs)


def _raw_config(*, concurrency=1):
    return {
            "version": 1,
            "limits": {
                "question_parallelism": 1,
                "llm_call_concurrency": concurrency,
                "audit_batch_questions": 5,
                "audit_batch_input_bytes": 120000,
            },
            "backends": {
                "codex": {"kind": "codex_app_server"},
                "local": {
                    "kind": "openai_compatible_http",
                    "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
                    "model": "replaceable-local-model",
                    "retry_model": "replaceable-retry-model",
                },
            },
            "profiles": {
                "codex_only": {"roles": {"maintenance": {"backend": "codex"}, "audit": {"backend": "codex"}}},
                "local_generate_codex_audit": {"roles": {"maintenance": {"backend": "local", "local_attempts_before_fallback": 2, "fallback_backend": "codex"}, "audit": {"backend": "codex"}}},
            },
        }


def _config(*, concurrency=1):
    return parse_model_backend_config(_raw_config(concurrency=concurrency))


def test_profile_router_keeps_backend_choice_behind_one_pipeline_client():
    router = ProfileModelRouter(_config(), _Codex())
    assert router.backend_for("local_generate_codex_audit", "maintenance").kind == "openai_compatible_http"
    assert router.backend_for("local_generate_codex_audit", "audit").kind == "codex_app_server"
    snapshot = router.snapshot_for("local_generate_codex_audit")
    assert snapshot["limits"]["questionParallelism"] == 1
    assert snapshot["roles"]["maintenance"]["model"] == "replaceable-local-model"
    assert snapshot["roles"]["maintenance"]["endpoint"].startswith(
        "http://127.0.0.1:"
    )
    assert snapshot["roles"]["maintenance"]["fallback"]["kind"] == (
        "codex_app_server"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("backends", "local", "endpoint"), "http://127.0.0.1:1234/v1/chat/completions"),
        (("backends", "local", "auth_env"), "LOCAL_LLM_TOKEN"),
        (("backends", "local", "model"), "changed-primary"),
        (("backends", "local", "retry_model"), "changed-retry"),
        (("backends", "local", "timeout_seconds"), 321),
        (("backends", "codex", "kind"), "openai_compatible_http"),
    ],
)
def test_selected_resolved_backend_change_changes_profile_fingerprint(path, value):
    raw = _raw_config()
    if path == ("backends", "codex", "kind"):
        raw["backends"]["codex"] = {
            "kind": value,
            "endpoint": "http://127.0.0.1:1235/v1/chat/completions",
            "model": "fallback-model",
        }
    else:
        raw[path[0]][path[1]][path[2]] = value
    before = ProfileModelRouter(_config(), _Codex()).snapshot_for(
        "local_generate_codex_audit"
    )["fingerprint"]
    after = ProfileModelRouter(
        parse_model_backend_config(raw), _Codex()
    ).snapshot_for("local_generate_codex_audit")["fingerprint"]
    assert before != after


def test_unselected_backend_connection_change_does_not_change_selected_fingerprint():
    raw = _raw_config()
    raw["backends"]["local"]["endpoint"] = (
        "http://127.0.0.1:1234/v1/chat/completions"
    )
    raw["backends"]["local"]["auth_env"] = "LOCAL_LLM_TOKEN"
    before = ProfileModelRouter(_config(), _Codex()).snapshot_for("codex_only")
    after = ProfileModelRouter(
        parse_model_backend_config(raw), _Codex()
    ).snapshot_for("codex_only")
    assert before["fingerprint"] == after["fingerprint"]


def test_profiles_dispatch_without_mutating_pipeline_router():
    router = ProfileModelRouter(_config(), _Codex())
    assert router.backend_for("codex_only", "maintenance").kind == "codex_app_server"
    assert router.backend_for("codex_only", "audit").kind == "codex_app_server"
    assert not hasattr(router, "profile_name")
    assert not hasattr(router, "select_profile")


def test_unselected_profile_change_does_not_change_selected_fingerprint():
    raw = _config()
    first = raw.profile("codex_only")
    changed = parse_model_backend_config(
        {
            "version": 1,
            "limits": {"question_parallelism": 1, "llm_call_concurrency": 1, "audit_batch_questions": 5, "audit_batch_input_bytes": 120000},
            "backends": {
                "codex": {"kind": "codex_app_server"},
                "local": {"kind": "openai_compatible_http", "endpoint": "http://127.0.0.1:11434/v1/chat/completions", "model": "different-local-model"},
            },
            "profiles": {
                "codex_only": {"roles": {"maintenance": {"backend": "codex"}, "audit": {"backend": "codex"}}},
                "local_generate_codex_audit": {"roles": {"maintenance": {"backend": "local"}, "audit": {"backend": "codex"}}},
            },
        }
    )
    del first
    assert ProfileModelRouter(raw, _Codex()).snapshot_for("codex_only")["fingerprint"] == ProfileModelRouter(changed, _Codex()).snapshot_for("codex_only")["fingerprint"]


@pytest.mark.parametrize(("limit", "expected_peak"), [(1, 1), (2, 2)])
def test_shared_semaphore_enforces_all_backend_call_limit(limit, expected_peak):
    router = ProfileModelRouter(_config(concurrency=limit), _Codex())
    active = 0
    peak = 0
    lock = threading.Lock()

    class BlockingBackend:
        configured = True
        provider = "test"

        def run_turn(self, _prompt, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return SimpleNamespace()

    router._instances = {name: BlockingBackend() for name in router._instances}
    profiles = ["codex_only", "local_generate_codex_audit"] * 2
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda name: router.run_turn("x", model_profile=name, work_type="maintenance_candidate"), profiles))
    assert peak == expected_peak


def test_concurrent_profiles_keep_backend_and_fingerprint_separate():
    router = ProfileModelRouter(_config(concurrency=2), _Codex())

    class NamedBackend:
        configured = True
        provider = "test"
        def __init__(self, name): self.name = name
        def run_turn(self, _prompt, **_kwargs): return self.name

    router._instances = {
        "codex": NamedBackend("codex"),
        "local": NamedBackend("local"),
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        codex = pool.submit(router.run_turn, "x", model_profile="codex_only", work_type="maintenance_candidate")
        hybrid = pool.submit(router.run_turn, "x", model_profile="local_generate_codex_audit", work_type="maintenance_candidate")
    assert (codex.result(), hybrid.result()) == ("codex", "local")
    assert router.snapshot_for("codex_only")["fingerprint"] != router.snapshot_for("local_generate_codex_audit")["fingerprint"]


def test_http_identity_is_non_empty_unique_and_matches_callbacks():
    router = ProfileModelRouter(_config(), _Codex())
    backend = router.backend_for("local_generate_codex_audit", "maintenance")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit):
            return json.dumps({"model": "replaceable-local-model", "choices": [{"message": {"content": json.dumps({"ok": True})}}]}).encode()

    identities = []
    with patch("urllib.request.urlopen", return_value=Response()):
        for _ in range(2):
            callbacks = {}
            result = backend.run_turn(
                "x", output_schema=schema,
                on_thread_started=lambda thread, session: callbacks.update(thread=thread, session=session),
                on_turn_started=lambda thread, turn: callbacks.update(turn_thread=thread, turn=turn),
            )
            assert (result.thread_id, result.session_id, result.turn_id) == (callbacks["thread"], callbacks["session"], callbacks["turn"])
            assert callbacks["thread"] == callbacks["turn_thread"]
            assert all((result.thread_id, result.session_id, result.turn_id))
            identities.append((result.thread_id, result.session_id, result.turn_id))
    assert identities[0] != identities[1]


def test_attempt_route_is_pure_primary_retry_then_codex_fallback():
    router = ProfileModelRouter(_config(), _Codex())
    profile = "local_generate_codex_audit"
    primary = router.resolve_maintenance_attempt(profile, [], workflow_model="gpt-workflow")
    retry = router.resolve_maintenance_attempt(
        profile,
        [{**primary.as_mapping(), "retryable": True}],
        workflow_model="gpt-workflow",
    )
    fallback = router.resolve_maintenance_attempt(
        profile,
        [
            {**primary.as_mapping(), "retryable": True},
            {**retry.as_mapping(), "retryable": True},
        ],
        workflow_model="gpt-workflow",
    )
    assert (primary.attempt_mode, primary.requested_model) == (
        "local_primary", "replaceable-local-model"
    )
    assert (retry.attempt_mode, retry.requested_model) == (
        "local_retry", "replaceable-retry-model"
    )
    assert (fallback.attempt_mode, fallback.requested_model) == (
        "codex_fallback", "gpt-workflow"
    )
    assert fallback.fallback_used is True


def test_only_nonretryable_backend_error_stops_attempt_routing():
    router = ProfileModelRouter(_config(), _Codex())
    profile = "local_generate_codex_audit"
    primary = router.resolve_maintenance_attempt(
        profile, [], workflow_model="gpt-workflow"
    )
    retry = router.resolve_maintenance_attempt(
        profile,
        [{**primary.as_mapping(), "retryable": False}],
        workflow_model="gpt-workflow",
    )
    assert retry.attempt_mode == "local_retry"
    with pytest.raises(ModelBackendError) as captured:
        router.resolve_maintenance_attempt(
            profile,
            [
                {
                    **primary.as_mapping(),
                    "backendErrorCode": "schema_mismatch",
                    "retryable": False,
                }
            ],
            workflow_model="gpt-workflow",
        )
    assert captured.value.code == "nonretryable_attempt"


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (urllib.error.HTTPError("http://local", 429, "busy", {}, None), "http_status", True),
        (urllib.error.HTTPError("http://local", 500, "down", {}, None), "http_status", True),
        (urllib.error.HTTPError("http://local", 503, "down", {}, None), "http_status", True),
        (urllib.error.HTTPError("http://local", 400, "secret-body", {}, None), "http_status", False),
        (urllib.error.HTTPError("http://local", 401, "secret-body", {}, None), "http_status", False),
        (urllib.error.HTTPError("http://local", 404, "secret-body", {}, None), "http_status", False),
        (urllib.error.URLError("secret-body"), "network", True),
        (TimeoutError("secret-body"), "timeout", True),
    ],
)
def test_http_failure_taxonomy_is_sanitized(error, code, retryable):
    backend = ProfileModelRouter(_config(), _Codex()).backend_for(
        "local_generate_codex_audit", "maintenance"
    )
    schema = {"type": "object"}
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(ModelBackendError) as captured:
            backend.run_turn("secret-prompt", output_schema=schema)
    assert captured.value.code == code
    assert captured.value.retryable is retryable
    assert "secret-body" not in str(captured.value)


def test_model_mismatch_is_nonretryable_and_request_uses_resolved_model():
    router = ProfileModelRouter(_config(), _Codex())
    route = router.resolve_maintenance_attempt(
        "local_generate_codex_audit", [], workflow_model="gpt-workflow"
    )
    schema = {"type": "object"}

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit):
            return json.dumps({"model": "wrong", "choices": []}).encode()

    with patch("urllib.request.urlopen", return_value=Response()) as call:
        with pytest.raises(ModelBackendError) as captured:
            router.run_turn(
                "x", model_profile="local_generate_codex_audit",
                maintenance_attempt=route, work_type="maintenance_candidate",
                output_schema=schema,
            )
    request_payload = json.loads(call.call_args.args[0].data)
    assert request_payload["model"] == route.requested_model
    assert captured.value.code == "model_mismatch"
    assert captured.value.retryable is False


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"secret invalid json", "invalid_json"),
        (
            json.dumps({
                "model": "replaceable-local-model",
                "choices": [{"message": {"content": json.dumps({"ok": "wrong"})}}],
            }).encode(),
            "schema_mismatch",
        ),
    ],
)
def test_invalid_json_and_schema_are_nonretryable_without_body_leak(body, code):
    backend = ProfileModelRouter(_config(), _Codex()).backend_for(
        "local_generate_codex_audit", "maintenance"
    )

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit): return body

    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    with patch("urllib.request.urlopen", return_value=Response()):
        with pytest.raises(ModelBackendError) as captured:
            backend.run_turn("x", output_schema=schema)
    assert captured.value.code == code
    assert captured.value.retryable is False
    assert "secret" not in str(captured.value)


def test_safety_violation_stops_before_http_call():
    backend = ProfileModelRouter(_config(), _Codex()).backend_for(
        "local_generate_codex_audit", "maintenance"
    )
    with patch("urllib.request.urlopen") as call:
        with pytest.raises(ModelBackendError) as captured:
            backend.run_turn("x", output_schema={}, sandbox="workspace-write")
    assert captured.value.code == "unsafe_request"
    assert captured.value.retryable is False
    call.assert_not_called()


def test_response_limit_is_nonretryable():
    backend = ProfileModelRouter(_config(), _Codex()).backend_for(
        "local_generate_codex_audit", "maintenance"
    )

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit): return b"x" * 2_000_001

    with patch("urllib.request.urlopen", return_value=Response()):
        with pytest.raises(ModelBackendError) as captured:
            backend.run_turn("x", output_schema={})
    assert captured.value.code == "response_too_large"
    assert captured.value.retryable is False


def test_missing_auth_stops_before_http_call(monkeypatch):
    raw = _config()
    local = raw.backends["local"]
    secured = type(local)(
        **{
            **local.__dict__,
            "auth_env": "LOCAL_LLM_SECRET",
        }
    )
    backend = ProfileModelRouter(_config(), _Codex()).backend_for(
        "local_generate_codex_audit", "maintenance"
    )
    backend.definition = secured
    monkeypatch.delenv("LOCAL_LLM_SECRET", raising=False)
    with patch("urllib.request.urlopen") as call:
        with pytest.raises(ModelBackendError) as captured:
            backend.run_turn("x", output_schema={})
    assert captured.value.code == "missing_auth"
    assert captured.value.retryable is False
    call.assert_not_called()
