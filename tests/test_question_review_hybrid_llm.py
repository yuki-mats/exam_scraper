from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.question_review_console.model_backend import (
    ProfileModelRouter,
    parse_model_backend_config,
)


class _Codex:
    configured = True
    provider = "Codex App Server"

    def run_turn(self, prompt, **kwargs):
        return (prompt, kwargs)


def _config(*, concurrency=1):
    return parse_model_backend_config(
        {
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
                },
            },
            "profiles": {
                "codex_only": {"roles": {"maintenance": {"backend": "codex"}, "audit": {"backend": "codex"}}},
                "local_generate_codex_audit": {"roles": {"maintenance": {"backend": "local"}, "audit": {"backend": "codex"}}},
            },
        }
    )


def test_profile_router_keeps_backend_choice_behind_one_pipeline_client():
    router = ProfileModelRouter(_config(), _Codex())
    assert router.backend_for("local_generate_codex_audit", "maintenance").kind == "openai_compatible_http"
    assert router.backend_for("local_generate_codex_audit", "audit").kind == "codex_app_server"
    snapshot = router.snapshot_for("local_generate_codex_audit")
    assert snapshot["limits"]["questionParallelism"] == 1
    assert snapshot["roles"]["maintenance"]["model"] == "replaceable-local-model"
    assert "endpoint" not in snapshot["roles"]["maintenance"]


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
