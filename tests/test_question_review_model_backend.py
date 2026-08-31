from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools.question_review_console.model_backend import (
    CodexOnlyAdapter,
    ModelBackendError,
    configuration_fingerprint,
    load_model_backend_config,
    parse_model_backend_config,
)


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config/question_maintenance_llm.toml"


def _raw_config() -> dict:
    return {
        "version": 1,
        "limits": {
            "question_parallelism": 1,
            "llm_call_concurrency": 1,
            "audit_batch_questions": 5,
            "audit_batch_input_bytes": 120000,
        },
        "backends": {
            "codex_app_server": {"kind": "codex_app_server"},
            "openai_compatible_http": {"kind": "openai_compatible_http"},
        },
        "profiles": {
            "codex_only": {
                "roles": {
                    "maintenance": {"backend": "codex_app_server"},
                    "audit": {"backend": "codex_app_server"},
                }
            }
        },
    }


def test_repository_config_defines_profiles_backends_roles_and_initial_limits():
    config = load_model_backend_config(CONFIG_PATH)

    assert set(config.backends) == {
        "codex_app_server",
        "openai_compatible_http",
    }
    assert set(config.profile("codex_only").roles) == {"maintenance", "audit"}
    hybrid = config.profile("local_generate_codex_audit")
    assert config.profile("codex_only").operational is True
    assert hybrid.operational is False
    assert hybrid.roles["maintenance"].backend == "openai_compatible_http"
    assert hybrid.roles["audit"].backend == "codex_app_server"
    local = config.backends["openai_compatible_http"]
    assert local.model == local.retry_model == "qwen3:14b"
    assert local.timeout_seconds == 600
    assert hybrid.roles["maintenance"].local_attempts_before_fallback == 1
    assert hybrid.roles["maintenance"].fallback_backend is None
    assert config.limits.question_parallelism == 20
    assert config.limits.llm_call_concurrency == 1
    assert config.limits.audit_batch_questions == 5
    assert config.limits.audit_batch_input_bytes == 120000


def test_processing_limits_are_configuration_not_hardcoded_constants():
    raw = _raw_config()
    raw["limits"].update(
        question_parallelism=4,
        llm_call_concurrency=2,
        audit_batch_questions=11,
        audit_batch_input_bytes=240000,
    )

    limits = parse_model_backend_config(raw).limits

    assert limits.question_parallelism == 4
    assert limits.llm_call_concurrency == 2
    assert limits.audit_batch_questions == 11
    assert limits.audit_batch_input_bytes == 240000


def test_fingerprint_is_stable_and_changes_with_non_secret_configuration():
    raw = _raw_config()
    reordered = copy.deepcopy(raw)
    reordered["limits"] = dict(reversed(list(reordered["limits"].items())))

    assert configuration_fingerprint(raw) == configuration_fingerprint(reordered)
    raw["limits"]["question_parallelism"] = 3
    assert configuration_fingerprint(raw) != configuration_fingerprint(reordered)


@pytest.mark.parametrize("field", ["api_key", "access_token", "client_secret"])
def test_secret_fields_are_rejected_before_fingerprinting(field):
    raw = _raw_config()
    raw["backends"]["openai_compatible_http"][field] = "must-not-persist"

    with pytest.raises(ValueError, match="secret"):
        parse_model_backend_config(raw)


@pytest.mark.parametrize("container", [list, tuple])
def test_secret_fields_nested_in_sequences_are_rejected_with_index_path(container):
    raw = _raw_config()
    raw["metadata"] = container([{"safe": "value"}, {"access_token": "hidden"}])

    with pytest.raises(ValueError, match=r"metadata\.\[1\]\.access_token"):
        configuration_fingerprint(raw)


def test_profile_requires_exactly_the_two_pipeline_roles():
    raw = _raw_config()
    del raw["profiles"]["codex_only"]["roles"]["audit"]

    with pytest.raises(ValueError, match="maintenance.*audit"):
        parse_model_backend_config(raw)


class _FakeCodexClient:
    configured = True
    provider = "Codex App Server"

    def __init__(self):
        self.calls = []
        self.closed = 0

    def run_turn(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return {"prompt": prompt, "kwargs": kwargs}

    def assert_subscription_access(self, *, force=False):
        return {"force": force}

    def public_status(self, *, refresh=False):
        return {"refresh": refresh}

    def close(self):
        self.closed += 1


def test_codex_only_adapter_uses_same_existing_client_for_both_roles():
    client = _FakeCodexClient()
    adapter = CodexOnlyAdapter(client)  # type: ignore[arg-type]

    maintenance = adapter.backend_for("maintenance")
    audit = adapter.backend_for("audit")

    assert maintenance is audit
    assert maintenance.client is client
    assert adapter.run_turn("maintenance", "fix", work_type="evaluation") == {
        "prompt": "fix",
        "kwargs": {"work_type": "evaluation"},
    }
    assert adapter.run_turn("audit", "check", output_schema={})["prompt"] == "check"
    assert maintenance.assert_subscription_access(force=True) == {"force": True}
    assert audit.public_status(refresh=True) == {"refresh": True}
    adapter.close()
    assert client.closed == 1


def test_codex_only_adapter_rejects_unknown_role():
    adapter = CodexOnlyAdapter(_FakeCodexClient())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="role"):
        adapter.backend_for("writer")


def test_backend_error_is_stable_and_never_includes_lower_level_body():
    error = ModelBackendError("http_status", retryable=True, status=503)
    assert str(error) == "LLM backend error: http_status (status 503)"
    assert error.retryable is True
