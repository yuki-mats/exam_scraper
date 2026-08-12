import copy
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.codex_app_server import (
    APP_SERVER_AGENT_MAX_DEPTH,
    APP_SERVER_AGENT_THREAD_CAP,
    APP_SERVER_CONTROL_PLANE_CAPACITY,
    APP_SERVER_CONTROL_REQUEST_TIMEOUT_SECONDS,
    CodexAppServerError,
    CodexAppServerClient,
    CodexControlRequestTimeoutError,
    CodexRequestTimeoutError,
    CodexTerminalTurnFailedError,
    CodexTurnTimeoutError,
    DEFAULT_TURN_TIMEOUT_SECONDS,
    FAST_SPEED_MODE,
    MIN_APP_SERVER_FILE_DESCRIPTORS,
    QUESTION_MAINTENANCE_RETRY_MODEL,
    RESEARCH_AGENT_CONFIG,
    RESEARCH_AGENT_CONFIG_FILENAME,
    RESEARCH_AGENT_ROLE,
    SAFE_SHELL_PATH,
    STRUCTURED_OUTPUT_TRAILING_WHITESPACE_CHARS,
    SUBSCRIPTION_STATUS_READ_ATTEMPTS,
    SubscriptionGateError,
    _NonBlockingObserverAdapter,
    _TurnState,
    adapt_output_schema_for_app_server,
    ensure_app_server_file_descriptor_capacity,
    validate_subscription_access,
)


class OutputSchemaAdapterTests(unittest.TestCase):
    def test_removes_nested_unsupported_keywords_without_mutating_source(self):
        source = {
            "type": "object",
            "required": ["values"],
            "properties": {
                "values": {
                    "type": "array",
                    "minItems": 1,
                    "uniqueItems": True,
                    "items": {
                        "type": "object",
                        "properties": {
                            "codes": {"type": "array", "uniqueItems": True}
                        },
                    },
                }
            },
        }
        original = copy.deepcopy(source)

        adapted = adapt_output_schema_for_app_server(source)

        self.assertEqual(source, original)
        self.assertNotIn("uniqueItems", adapted["properties"]["values"])
        self.assertNotIn(
            "uniqueItems",
            adapted["properties"]["values"]["items"]["properties"]["codes"],
        )
        self.assertEqual(adapted["required"], ["values"])
        self.assertEqual(adapted["properties"]["values"]["minItems"], 1)


class StructuredMessageCompletionTests(unittest.TestCase):
    def test_only_final_answer_starts_missing_completion_grace(self):
        state = _TurnState(
            thread_id="thread-1",
            turn_id="turn-1",
            emit=lambda _line: None,
            structured_output=True,
        )
        client = object.__new__(CodexAppServerClient)

        client._record_turn_item(
            state,
            {
                "id": "commentary-1",
                "type": "agentMessage",
                "phase": "commentary",
                "text": '{"status":"needs_rework","summary":"調査を続行します"}',
            },
        )

        self.assertIsNone(state.completed_message_at)
        self.assertEqual(
            state.messages,
            [
                (
                    "commentary",
                    '{"status":"needs_rework","summary":"調査を続行します"}',
                )
            ],
        )

        client._record_turn_item(
            state,
            {
                "id": "final-1",
                "type": "agentMessage",
                "phase": "final_answer",
                "text": '{"status":"passed"}',
            },
        )

        self.assertIsNotNone(state.completed_message_at)


class FileDescriptorCapacityTests(unittest.TestCase):
    class FakeResource:
        RLIMIT_NOFILE = 7
        RLIM_INFINITY = -1

        def __init__(self, soft, hard):
            self.limits = (soft, hard)
            self.calls = []

        def getrlimit(self, resource_id):
            self.calls.append(("get", resource_id))
            return self.limits

        def setrlimit(self, resource_id, limits):
            self.calls.append(("set", resource_id, limits))
            self.limits = limits

    def test_raises_soft_limit_before_starting_app_server(self):
        fake = self.FakeResource(256, self.FakeResource.RLIM_INFINITY)

        with patch(
            "tools.question_review_console.codex_app_server.resource",
            fake,
        ):
            actual = ensure_app_server_file_descriptor_capacity()

        self.assertEqual(actual, MIN_APP_SERVER_FILE_DESCRIPTORS)
        self.assertIn(
            (
                "set",
                fake.RLIMIT_NOFILE,
                (MIN_APP_SERVER_FILE_DESCRIPTORS, fake.RLIM_INFINITY),
            ),
            fake.calls,
        )

    def test_rejects_hard_limit_below_sixty_four_turn_requirement(self):
        fake = self.FakeResource(256, 1024)

        with patch(
            "tools.question_review_console.codex_app_server.resource",
            fake,
        ), self.assertRaisesRegex(
            CodexAppServerError,
            "file descriptor上限",
        ):
            ensure_app_server_file_descriptor_capacity()


def account_response(plan="pro"):
    return {
        "account": {
            "type": "chatgpt",
            "email": "person@example.com",
            "planType": plan,
        },
        "requiresOpenaiAuth": True,
    }


def rate_limit_response(plan="pro"):
    return {
        "rateLimits": {
            "limitId": "codex",
            "limitName": "Codex",
            "primary": {
                "usedPercent": 10,
                "windowDurationMins": 300,
                "resetsAt": 1,
            },
            "secondary": None,
            "credits": {"hasCredits": False, "unlimited": False, "balance": None},
            "individualLimit": None,
            "planType": plan,
            "rateLimitReachedType": None,
        },
        "rateLimitsByLimitId": {
            "codex_bengalfox": {
                "credits": None,
                "individualLimit": None,
                "rateLimitReachedType": None,
            }
        },
        "rateLimitResetCredits": None,
    }


class SubscriptionGateTests(unittest.TestCase):
    def test_allows_chatgpt_subscription_without_credits(self):
        status = validate_subscription_access(account_response(), rate_limit_response())

        self.assertTrue(status["allowed"])
        self.assertEqual(status["accountType"], "chatgpt")
        self.assertEqual(status["planType"], "pro")
        self.assertFalse(status["creditsEnabled"])
        self.assertFalse(status["fastModeAvailable"])
        self.assertTrue(status["standardMode"])
        self.assertFalse(status["fastMode"])

    def test_public_subscription_status_reports_effective_and_turn_model_settings(self):
        client = CodexAppServerClient(Path.cwd(), binary_path=Path("/bin/echo"))
        client._ensure_started = lambda: None
        client._effective_model = "gpt-5.6-sol"
        client._configured_reasoning_effort = "xhigh"
        client._request = lambda method, _params: (
            account_response() if method == "account/read" else rate_limit_response()
        )

        status = client.assert_subscription_access()

        self.assertEqual(status["configuredModel"], "gpt-5.6-sol")
        self.assertEqual(status["configuredReasoningEffort"], "xhigh")
        self.assertEqual(status["model"], "gpt-5.6-luna")
        self.assertEqual(status["retryModel"], "gpt-5.6-sol")
        self.assertEqual(status["turnReasoningEffort"], "high")

    def test_concurrent_forced_status_checks_share_one_fresh_read(self):
        client = CodexAppServerClient(Path.cwd(), binary_path=Path("/bin/echo"))
        client._ensure_started = lambda: None
        calls = []

        def request(method, _params):
            calls.append(method)
            time.sleep(0.02)
            return (
                account_response()
                if method == "account/read"
                else rate_limit_response()
            )

        client._request = request
        barrier = threading.Barrier(8)

        def check():
            barrier.wait()
            return client.assert_subscription_access(force=True)

        with ThreadPoolExecutor(max_workers=8) as executor:
            statuses = list(executor.map(lambda _index: check(), range(8)))

        self.assertTrue(all(status["allowed"] for status in statuses))
        self.assertEqual(calls.count("account/read"), 1)
        self.assertEqual(calls.count("account/rateLimits/read"), 1)

    def test_staggered_turns_share_the_same_fresh_status_window(self):
        client = CodexAppServerClient(Path.cwd(), binary_path=Path("/bin/echo"))
        client._ensure_started = lambda: None
        calls = []

        def request(method, _params):
            calls.append(method)
            return (
                account_response()
                if method == "account/read"
                else rate_limit_response()
            )

        client._request = request
        first = client.assert_subscription_access(force=False)
        time.sleep(0.01)
        with ThreadPoolExecutor(max_workers=16) as executor:
            statuses = list(
                executor.map(
                    lambda _index: client.assert_subscription_access(force=False),
                    range(64),
                )
            )

        self.assertTrue(first["allowed"])
        self.assertTrue(all(status["allowed"] for status in statuses))
        self.assertEqual(calls.count("account/read"), 1)
        self.assertEqual(calls.count("account/rateLimits/read"), 1)

    def test_transient_status_read_is_retried_without_weakening_gate(self):
        client = CodexAppServerClient(Path.cwd(), binary_path=Path("/bin/echo"))
        client._ensure_started = lambda: None
        rate_limit_calls = 0

        def request(method, _params):
            nonlocal rate_limit_calls
            if method == "account/read":
                return account_response()
            rate_limit_calls += 1
            if rate_limit_calls < SUBSCRIPTION_STATUS_READ_ATTEMPTS:
                raise CodexAppServerError("temporary rate limit read failure")
            return rate_limit_response()

        client._request = request
        with patch(
            "tools.question_review_console.codex_app_server.time.sleep"
        ) as sleep:
            status = client.assert_subscription_access(force=True)

        self.assertTrue(status["allowed"])
        self.assertEqual(rate_limit_calls, SUBSCRIPTION_STATUS_READ_ATTEMPTS)
        self.assertEqual(sleep.call_count, SUBSCRIPTION_STATUS_READ_ATTEMPTS - 1)

    def test_persistent_status_read_failure_remains_fail_closed(self):
        client = CodexAppServerClient(Path.cwd(), binary_path=Path("/bin/echo"))
        client._ensure_started = lambda: None
        client._last_status = validate_subscription_access(
            account_response(), rate_limit_response()
        )
        client._last_status_at = time.monotonic()
        calls = 0

        def request(_method, _params):
            nonlocal calls
            calls += 1
            raise CodexAppServerError("persistent status read failure")

        client._request = request
        with patch("tools.question_review_console.codex_app_server.time.sleep"):
            with self.assertRaisesRegex(
                CodexAppServerError, "persistent status read failure"
            ):
                client.assert_subscription_access(force=True)

        self.assertEqual(calls, SUBSCRIPTION_STATUS_READ_ATTEMPTS)

    def test_provider_recovery_restarts_connection_after_backoff(self):
        class Observer:
            def __init__(self):
                self.gaps = 0

            def record_observation_gap(self, count=1):
                self.gaps += count

        observer = Observer()
        client = CodexAppServerClient(
            Path.cwd(),
            binary_path=Path("/bin/echo"),
            observer=observer,
        )
        process = object()
        stream = object()
        client._process = process
        client._stdin = stream
        client._initialized = True
        client._last_status = {"allowed": True}
        client._last_status_at = time.monotonic()
        messages = []

        with (
            patch.object(client, "_stop_process") as stop_process,
            patch.object(client, "_fail_all") as fail_all,
            patch.object(client, "_ensure_started") as ensure_started,
            patch("tools.question_review_console.codex_app_server.time.sleep") as sleep,
        ):
            client.recover_after_provider_failure(
                attempt=2,
                emit=messages.append,
            )

        stop_process.assert_called_once_with(process, stream)
        fail_all.assert_called_once()
        sleep.assert_called_once_with(60.0)
        ensure_started.assert_called_once()
        self.assertIsNone(client._process)
        self.assertIsNone(client._stdin)
        self.assertFalse(client._initialized)
        self.assertIsNone(client._last_status)
        self.assertEqual(client._last_status_at, 0.0)
        self.assertIn("60秒後に再試行", messages[0])
        self.assertTrue(client._monitor_observer_adapter.drain_for_test())
        self.assertEqual(observer.gaps, 1)
        client.close()

    def test_rejects_non_subscription_accounts(self):
        for account in (
            {"account": {"type": "apiKey"}},
            {"account": {"type": "amazonBedrock", "credentialSource": "env"}},
            {"account": None},
        ):
            with self.subTest(account=account):
                with self.assertRaises(SubscriptionGateError):
                    validate_subscription_access(account, rate_limit_response())

    def test_rejects_usage_based_unknown_or_malformed_credit_and_spend_paths(self):
        cases = []
        cases.append((account_response("self_serve_business_usage_based"), rate_limit_response("self_serve_business_usage_based")))
        cases.append((account_response("unknown"), rate_limit_response("unknown")))
        missing_credits = rate_limit_response()
        missing_credits["rateLimits"]["credits"] = None
        cases.append((account_response(), missing_credits))
        spend = rate_limit_response()
        spend["rateLimits"]["individualLimit"] = "unknown"
        cases.append((account_response(), spend))
        for account, limits in cases:
            with self.subTest(account=account, limits=limits):
                with self.assertRaises(SubscriptionGateError):
                    validate_subscription_access(account, limits)

    def test_rejects_reached_missing_or_invalid_rate_limits(self):
        cases = []
        reached = rate_limit_response()
        reached["rateLimits"]["rateLimitReachedType"] = "rate_limit_reached"
        cases.append(reached)
        full = rate_limit_response()
        full["rateLimits"]["primary"]["usedPercent"] = 100
        cases.append(full)
        missing_primary = rate_limit_response()
        missing_primary["rateLimits"]["primary"] = None
        cases.append(missing_primary)
        missing_reached = rate_limit_response()
        missing_reached["rateLimits"].pop("rateLimitReachedType")
        cases.append(missing_reached)
        mismatched_plan = rate_limit_response("plus")
        cases.append(mismatched_plan)

        for limits in cases:
            with self.subTest(limits=limits):
                with self.assertRaises(SubscriptionGateError):
                    validate_subscription_access(account_response(), limits)

    def test_rejects_fast_and_any_additional_credits(self):
        allowed = rate_limit_response()
        allowed["rateLimitResetCredits"] = {
            "availableCount": 1,
            "credits": [{"status": "available", "title": "Full reset"}],
        }
        validate_subscription_access(account_response(), allowed)

        with self.assertRaisesRegex(ValueError, "Standard mode"):
            validate_subscription_access(
                account_response(),
                allowed,
                speed_mode=FAST_SPEED_MODE,
            )

        enabled = copy.deepcopy(allowed)
        enabled["rateLimits"]["credits"]["hasCredits"] = True
        enabled["rateLimitsByLimitId"]["codex_bengalfox"]["credits"] = {
            "hasCredits": True
        }
        with self.assertRaisesRegex(SubscriptionGateError, "追加Codex credits"):
            validate_subscription_access(account_response(), enabled)

        auxiliary_only = copy.deepcopy(allowed)
        auxiliary_only["rateLimitsByLimitId"]["codex_bengalfox"]["credits"] = {
            "hasCredits": True
        }
        with self.assertRaisesRegex(SubscriptionGateError, "補助Codex credits"):
            validate_subscription_access(account_response(), auxiliary_only)

    def test_rejects_missing_or_malformed_auxiliary_spend_fields(self):
        cases = []
        missing_limits = rate_limit_response()
        missing_limits.pop("rateLimitsByLimitId")
        cases.append(missing_limits)
        malformed_limit = rate_limit_response()
        malformed_limit["rateLimitsByLimitId"]["codex_bengalfox"] = "unknown"
        cases.append(malformed_limit)
        missing_reached = rate_limit_response()
        missing_reached["rateLimitsByLimitId"]["codex_bengalfox"].pop(
            "rateLimitReachedType"
        )
        cases.append(missing_reached)
        missing_credits = rate_limit_response()
        missing_credits["rateLimitsByLimitId"]["codex_bengalfox"].pop("credits")
        cases.append(missing_credits)
        unknown_credits = rate_limit_response()
        unknown_credits["rateLimitsByLimitId"]["codex_bengalfox"]["credits"] = {}
        cases.append(unknown_credits)
        missing_spend = rate_limit_response()
        missing_spend["rateLimitsByLimitId"]["codex_bengalfox"].pop(
            "individualLimit"
        )
        cases.append(missing_spend)

        for limits in cases:
            with self.subTest(limits=limits):
                with self.assertRaises(SubscriptionGateError):
                    validate_subscription_access(account_response(), limits)


class ProtocolClient(CodexAppServerClient):
    def __init__(self, **kwargs):
        super().__init__(Path.cwd(), binary_path=Path("/bin/echo"), **kwargs)
        self.calls = []
        self.turn_number = 0
        self.sent = []
        self.subscription_forces = []
        self.research_threads = set()
        self.subagent_parents = {}
        self.research_child_count = 2
        self.research_child_model = "gpt-5.6-luna"
        self.research_child_effort = "high"
        self.research_agent_config_path = Path(
            "/isolated/question-maintenance-explorer.toml"
        )
        self.isolated_model_workspace = Path("/isolated/model-workspace")

    def assert_subscription_access(self, *, force=True, speed_mode="standard"):
        self.subscription_forces.append((force, speed_mode))
        return {"allowed": True, "planType": "pro", "speedMode": speed_mode}

    def _ensure_started(self):
        # Protocol tests replace JSON-RPC directly and do not launch a process.
        return None

    def _trusted_research_agent_config(self):
        return self.research_agent_config_path

    def _isolated_model_cwd(self):
        return self.isolated_model_workspace

    def _request(self, method, params, *, timeout=None):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "thread/start":
            self.turn_number += 1
            thread_id = f"thread-{self.turn_number}"
            if (
                params.get("threadSource") == "exam_scraper_maintenance_research"
                and params.get("config", {}).get("features", {}).get("multi_agent") is True
            ):
                self.research_threads.add(thread_id)
            sandbox_type = "readOnly" if params["sandbox"] == "read-only" else "workspaceWrite"
            return {
                "thread": {"id": thread_id, "sessionId": f"session-{self.turn_number}"},
                "model": params["model"],
                "modelProvider": "openai",
                "serviceTier": params.get("serviceTier"),
                "sandbox": {"type": sandbox_type, "networkAccess": False},
            }
        if method == "hooks/list":
            return {
                "data": [
                    {
                        "cwd": params["cwds"][0],
                        "hooks": [],
                        "warnings": [],
                        "errors": [],
                    }
                ]
            }
        if method == "mcpServerStatus/list":
            return {"data": [], "nextCursor": None}
        if method == "turn/start":
            thread_id = params["threadId"]
            turn_id = thread_id.replace("thread", "turn")
            if thread_id in self.research_threads:
                child_ids = [
                    f"{thread_id}-child-{index}"
                    for index in range(1, self.research_child_count + 1)
                ]
                self.subagent_parents.update(
                    {child_id: thread_id for child_id in child_ids}
                )
                self._handle_turn_notification(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "type": "collabAgentToolCall",
                                "tool": "spawnAgent",
                                "status": "completed",
                                "receiverThreadIds": child_ids,
                                "model": self.research_child_model,
                                "reasoningEffort": self.research_child_effort,
                            },
                        },
                    }
                )
            self._handle_turn_notification(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": thread_id,
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "phase": "final_answer",
                            "text": '{"status":"ok"}',
                        },
                    },
                }
            )
            self._handle_turn_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {
                            "id": turn_id,
                            "status": "completed",
                            "error": None,
                            "items": [],
                        },
                    },
                }
            )
            return {"turn": {"id": turn_id}}
        if method == "thread/read":
            child_id = params["threadId"]
            return {
                "thread": {
                    "id": child_id,
                    "modelProvider": "openai",
                    "parentThreadId": self.subagent_parents[child_id],
                }
            }
        raise AssertionError(method)

    def _send(self, message):
        self.sent.append(copy.deepcopy(dict(message)))


class MonitorObserverTests(unittest.TestCase):
    def test_reader_enqueue_does_not_iterate_or_copy_notification(self):
        delivered = threading.Event()

        class ReaderMessage(dict):
            def keys(self):
                raise AssertionError("reader thread must not normalize")

            def __iter__(self):
                raise AssertionError("reader thread must not iterate")

        class Observer:
            def __init__(self):
                self.message = None

            def observe(self, message):
                self.message = message
                delivered.set()

        observer = Observer()
        adapter = _NonBlockingObserverAdapter(observer)
        message = ReaderMessage(
            method="turn/started",
            params={"threadId": "thread-1", "turnId": "turn-1"},
        )

        adapter.put_nowait(message)

        self.assertTrue(delivered.wait(timeout=0.5))
        self.assertTrue(adapter.drain_for_test())
        self.assertIs(observer.message, message)
        adapter.close()

    def test_adapter_close_drains_every_accepted_notification(self):
        entered = threading.Event()
        release = threading.Event()

        class Observer:
            def __init__(self):
                self.messages = []

            def put_nowait(self, message):
                self.messages.append(message["params"]["number"])
                if len(self.messages) == 1:
                    entered.set()
                    release.wait()

        observer = Observer()
        adapter = _NonBlockingObserverAdapter(observer, capacity=4)
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 1}}
        )
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 2}}
        )
        self.assertTrue(entered.wait(timeout=0.5))

        closer = threading.Thread(target=adapter.close)
        closer.start()
        time.sleep(0.25)
        self.assertTrue(closer.is_alive())
        release.set()
        closer.join(timeout=1)

        self.assertFalse(closer.is_alive())
        self.assertEqual(observer.messages, [1, 2])
        self.assertEqual(adapter._queue.unfinished_tasks, 0)

    def test_monitor_enabled_and_disabled_keep_model_rpcs_and_usage_identical(self):
        class UsageProtocolClient(ProtocolClient):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.protocol_usage = []

            def _request(self, method, params, *, timeout=None):
                result = super()._request(method, params, timeout=timeout)
                if method == "turn/start":
                    usage = {
                        "last": {
                            "inputTokens": 120,
                            "cachedInputTokens": 80,
                            "outputTokens": 30,
                            "reasoningOutputTokens": 10,
                            "totalTokens": 150,
                        },
                        "total": {
                            "inputTokens": 120,
                            "cachedInputTokens": 80,
                            "outputTokens": 30,
                            "reasoningOutputTokens": 10,
                            "totalTokens": 150,
                        },
                        "modelContextWindow": 200000,
                    }
                    self.protocol_usage.append(copy.deepcopy(usage))
                    self._handle_message(
                        {
                            "method": "thread/tokenUsage/updated",
                            "params": {
                                "threadId": params["threadId"],
                                "tokenUsage": usage,
                            },
                        }
                    )
                return result

        class Observer:
            def __init__(self):
                self.messages = []
                self.monitor_model_requests = 0

            def observe(self, message):
                self.messages.append(copy.deepcopy(message))

            def bind_runtime(self, _context, _thread_id, _turn_id=None):
                return None

        observer = Observer()
        without_monitor = UsageProtocolClient()
        with_monitor = UsageProtocolClient(observer=observer)
        kwargs = {
            "work_type": "evaluation",
            "sandbox": "read-only",
            "emit": lambda _message: None,
        }

        result_without = without_monitor.run_turn("same prompt", **kwargs)
        result_with = with_monitor.run_turn("same prompt", **kwargs)

        self.assertTrue(with_monitor._monitor_observer_adapter.drain_for_test())
        model_methods = {"thread/start", "turn/start"}
        rpcs_without = [
            (method, params)
            for method, params in without_monitor.calls
            if method in model_methods
        ]
        rpcs_with = [
            (method, params)
            for method, params in with_monitor.calls
            if method in model_methods
        ]
        self.assertEqual(rpcs_with, rpcs_without)
        self.assertEqual(
            [method for method, _params in rpcs_with],
            ["thread/start", "turn/start"],
        )
        self.assertEqual(with_monitor.protocol_usage, without_monitor.protocol_usage)
        self.assertEqual(result_with.final_message, result_without.final_message)
        self.assertEqual(observer.monitor_model_requests, 0)
        observed_usage = [
            message["params"]["tokenUsage"]
            for message in observer.messages
            if message.get("method") == "thread/tokenUsage/updated"
        ]
        self.assertEqual(observed_usage, with_monitor.protocol_usage)
        without_monitor.close()
        with_monitor.close()

    def test_allowlisted_notifications_and_exact_runtime_bindings_are_observed(self):
        class Observer:
            def __init__(self):
                self.messages = []
                self.bindings = []

            def observe(self, message):
                self.messages.append(copy.deepcopy(message))

            def bind_runtime(self, context, thread_id, turn_id=None):
                self.bindings.append((copy.deepcopy(context), thread_id, turn_id))

        observer = Observer()
        client = ProtocolClient(observer=observer)
        client._monitor_context = {"qualification": "sample", "runId": "run-1"}

        result = client.run_turn(
            "prompt",
            work_type="evaluation",
            sandbox="read-only",
            emit=lambda _message: None,
        )

        self.assertTrue(client._monitor_observer_adapter.drain_for_test())
        self.assertEqual(result.thread_id, "thread-1")
        self.assertIn(len(observer.bindings), {1, 2})
        self.assertEqual(
            observer.bindings[-1][1:],
            ("thread-1", "turn-1"),
        )
        if len(observer.bindings) == 2:
            self.assertEqual(
                observer.bindings[0][1:],
                ("thread-1", None),
            )
        for context, _thread_id, _turn_id in observer.bindings:
            self.assertEqual(context["runId"], "run-1")
            self.assertEqual(context["sessionId"], "session-1")
        client.close()

    def test_observer_exception_does_not_change_protocol_handling(self):
        class BrokenObserver:
            def observe(self, _message):
                raise RuntimeError("monitor unavailable")

        client = CodexAppServerClient(
            Path.cwd(),
            binary_path=Path("/bin/echo"),
            observer=BrokenObserver(),
        )
        state = _TurnState("thread", "turn", lambda _message: None)
        client._turns[("thread", "turn")] = state

        client._handle_message(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {"id": "turn", "status": "completed", "items": []},
                },
            }
        )

        self.assertTrue(state.event.is_set())
        self.assertEqual(state.status, "completed")
        client.close()

    def test_unexpected_stdout_eof_marks_next_connection_as_a_gap(self):
        client = CodexAppServerClient(
            Path.cwd(),
            binary_path=Path("/bin/echo"),
        )

        class Process:
            pass

        process = Process()
        client._process = process
        client._stdin = object()
        client._initialized = True

        client._read_stdout([], process)

        self.assertTrue(client._observation_connection_lost)
        self.assertIsNone(client._process)
        self.assertFalse(client._initialized)
        client.close()

    def test_stdout_path_never_calls_blocking_observer_directly(self):
        release = threading.Event()
        entered = threading.Event()

        class BlockingObserver:
            def observe(self, _message):
                entered.set()
                release.wait(timeout=1)

        client = CodexAppServerClient(
            Path.cwd(),
            binary_path=Path("/bin/echo"),
            observer=BlockingObserver(),
        )
        started_at = time.monotonic()

        client._handle_message(
            {
                "method": "thread/status/changed",
                "params": {
                    "threadId": "thread",
                    "status": {"type": "active", "activeFlags": []},
                },
            }
        )

        self.assertLess(time.monotonic() - started_at, 0.05)
        self.assertTrue(entered.wait(timeout=0.5))
        release.set()
        self.assertTrue(client._monitor_observer_adapter.drain_for_test())
        client.close()

    def test_runtime_binding_survives_saturated_notification_queue(self):
        entered = threading.Event()
        release = threading.Event()

        class SlowObserver:
            def __init__(self):
                self.bindings = []
                self.gaps = 0
                self.log = []

            def put_nowait(self, message):
                self.log.append(f"notification:{message['params']['number']}")
                entered.set()
                release.wait(timeout=1)

            def bind_runtime(self, context, thread_id, turn_id=None):
                self.bindings.append((dict(context), thread_id, turn_id))

            def record_observation_gap(self, count=1):
                self.gaps += count
                self.log.append(f"gap:{count}")

        observer = SlowObserver()
        adapter = _NonBlockingObserverAdapter(observer, capacity=1)
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 1}}
        )
        self.assertTrue(entered.wait(timeout=0.5))
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 2}}
        )
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 3}}
        )
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 4}}
        )
        adapter.bind_runtime({"runId": "run"}, "thread", "turn")
        release.set()

        self.assertTrue(adapter.drain_for_test())
        self.assertEqual(
            observer.bindings,
            [({"runId": "run"}, "thread", "turn")],
        )
        self.assertEqual(observer.gaps, 2)
        self.assertEqual(
            observer.log,
            ["notification:1", "notification:2", "gap:2"],
        )
        adapter.close()

    def test_binding_backlog_is_coalesced_and_bounded_by_thread(self):
        entered = threading.Event()
        release = threading.Event()

        class Observer:
            def __init__(self):
                self.bindings = []
                self.gaps = []

            def put_nowait(self, _message):
                entered.set()
                release.wait()

            def bind_runtime(self, context, thread_id, turn_id=None):
                self.bindings.append((dict(context), thread_id, turn_id))

            def record_observation_gap(
                self,
                count=1,
                *,
                affected_routes=None,
                scope_truncated=False,
            ):
                self.gaps.append(
                    (count, affected_routes, scope_truncated)
                )

        observer = Observer()
        adapter = _NonBlockingObserverAdapter(
            observer,
            capacity=1,
            binding_capacity=2,
            control_batch_size=1,
        )
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 1}}
        )
        self.assertTrue(entered.wait(timeout=0.5))

        adapter.bind_runtime(
            {"qualification": "qual", "runId": "run-1", "version": 1},
            "thread-1",
        )
        adapter.bind_runtime(
            {"qualification": "qual", "runId": "run-2", "version": 1},
            "thread-2",
        )
        adapter.bind_runtime(
            {"qualification": "qual", "runId": "run-2", "version": 2},
            "thread-2",
            "turn-2",
        )
        with adapter._gap_lock:
            self.assertEqual(len(adapter._pending_bindings), 2)
            self.assertEqual(
                adapter._pending_bindings["thread-2"],
                (
                    {
                        "qualification": "qual",
                        "runId": "run-2",
                        "version": 2,
                    },
                    "thread-2",
                    "turn-2",
                ),
            )
        for index in range(3, 21):
            adapter.bind_runtime(
                {
                    "qualification": "qual",
                    "runId": f"run-{index}",
                    "version": 1,
                },
                f"thread-{index}",
            )

        with adapter._gap_lock:
            self.assertEqual(
                tuple(adapter._pending_bindings),
                ("thread-19", "thread-20"),
            )
            self.assertLessEqual(len(adapter._thread_route_groups), 2)
        release.set()

        self.assertTrue(adapter.drain_for_test())
        self.assertEqual(
            observer.bindings,
            [
                (
                    {
                        "qualification": "qual",
                        "runId": "run-19",
                        "version": 1,
                    },
                    "thread-19",
                    None,
                ),
                (
                    {
                        "qualification": "qual",
                        "runId": "run-20",
                        "version": 1,
                    },
                    "thread-20",
                    None,
                ),
            ],
        )
        self.assertEqual(observer.gaps, [(18, (), True)])
        adapter.close()

    def test_control_batch_cannot_starve_accepted_notifications(self):
        entered = threading.Event()
        release = threading.Event()

        class Observer:
            def __init__(self):
                self.log = []

            def put_nowait(self, message):
                number = message["params"]["number"]
                self.log.append(f"notification:{number}")
                if number == 1:
                    entered.set()
                    release.wait()

            def bind_runtime(self, _context, thread_id, _turn_id=None):
                self.log.append(f"binding:{thread_id}")

        observer = Observer()
        adapter = _NonBlockingObserverAdapter(
            observer,
            capacity=2,
            binding_capacity=8,
            control_batch_size=2,
        )
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 1}}
        )
        self.assertTrue(entered.wait(timeout=0.5))
        for index in range(6):
            adapter.bind_runtime({}, f"thread-{index}")
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 2}}
        )
        release.set()

        self.assertTrue(adapter.drain_for_test())
        notification_index = observer.log.index("notification:2")
        self.assertEqual(notification_index, 3)
        self.assertEqual(
            observer.log[1:notification_index],
            ["binding:thread-0", "binding:thread-1"],
        )
        adapter.close()

    def test_dropped_terminal_freezes_route_and_cleans_live_state(self):
        entered = threading.Event()
        release = threading.Event()

        class Observer:
            def __init__(self):
                self.gaps = []

            def put_nowait(self, message):
                if message["params"].get("number") == 1:
                    entered.set()
                    release.wait()

            def bind_runtime(self, _context, _thread_id, _turn_id=None):
                return None

            def record_observation_gap(
                self,
                count=1,
                *,
                affected_routes=None,
                scope_truncated=False,
            ):
                self.gaps.append(
                    (count, affected_routes, scope_truncated)
                )

        observer = Observer()
        adapter = _NonBlockingObserverAdapter(observer, capacity=1)
        adapter.bind_runtime(
            {"qualification": "qual", "runId": "run-1"},
            "thread-1",
            "turn-1",
        )
        adapter.put_nowait(
            {
                "method": "turn/started",
                "params": {"threadId": "thread-1", "number": 1},
            }
        )
        self.assertTrue(entered.wait(timeout=0.5))
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 2}}
        )
        adapter.put_nowait(
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1"},
            }
        )

        expected_route = (
            "qual",
            "run-1",
            (("qual", "run-1"),),
        )
        with adapter._gap_lock:
            self.assertNotIn("thread-1", adapter._thread_route_groups)
            self.assertEqual(adapter._route_snapshot, ())
        release.set()

        self.assertTrue(adapter.drain_for_test())
        self.assertEqual(
            observer.gaps,
            [(1, (expected_route,), False)],
        )
        adapter.close()

    def test_gap_scope_overflow_is_global_and_has_exact_drop_count(self):
        entered = threading.Event()
        release = threading.Event()

        class Observer:
            def __init__(self):
                self.gaps = []

            def put_nowait(self, message):
                if message["params"].get("number") == 1:
                    entered.set()
                    release.wait()

            def bind_runtime(self, _context, _thread_id, _turn_id=None):
                return None

            def record_observation_gap(
                self,
                count=1,
                *,
                affected_routes=None,
                scope_truncated=False,
            ):
                self.gaps.append(
                    (count, affected_routes, scope_truncated)
                )

        observer = Observer()
        adapter = _NonBlockingObserverAdapter(
            observer,
            capacity=1,
            binding_capacity=4,
            gap_capacity=1,
        )
        adapter.bind_runtime(
            {"qualification": "qual", "runId": "run-1"},
            "thread-1",
        )
        adapter.put_nowait(
            {
                "method": "turn/started",
                "params": {"threadId": "thread-1", "number": 1},
            }
        )
        self.assertTrue(entered.wait(timeout=0.5))
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 2}}
        )
        adapter.put_nowait(
            {
                "method": "thread/status/changed",
                "params": {"threadId": "thread-1"},
            }
        )
        adapter.bind_runtime(
            {"qualification": "qual", "runId": "run-2"},
            "thread-2",
        )
        for _index in range(2):
            adapter.put_nowait(
                {
                    "method": "thread/status/changed",
                    "params": {"threadId": "thread-2"},
                }
            )

        with adapter._gap_lock:
            self.assertLessEqual(len(adapter._gap_segments), 1)
            self.assertEqual(adapter._gap_overflow_count, 2)
        release.set()

        self.assertTrue(adapter.drain_for_test())
        precise = [gap for gap in observer.gaps if not gap[2]]
        truncated = [gap for gap in observer.gaps if gap[2]]
        self.assertEqual(sum(gap[0] for gap in precise), 1)
        self.assertEqual(truncated, [(2, (), True)])
        adapter.close()

    def test_close_timeout_is_bounded_and_retryable(self):
        entered = threading.Event()
        release = threading.Event()

        class Observer:
            def put_nowait(self, _message):
                entered.set()
                release.wait()

        adapter = _NonBlockingObserverAdapter(Observer())
        adapter.put_nowait(
            {"method": "turn/started", "params": {"number": 1}}
        )
        self.assertTrue(entered.wait(timeout=0.5))

        started_at = time.monotonic()
        try:
            with self.assertRaisesRegex(
                TimeoutError,
                "did not drain accepted work",
            ):
                adapter.close(timeout=0.02)
        finally:
            release.set()
        self.assertLess(time.monotonic() - started_at, 0.25)

        adapter.close(timeout=1)
        self.assertTrue(adapter.drain_for_test())


class ReceiptInterruptProtocolClient(ProtocolClient):
    def _request(self, method, params, *, timeout=None):
        if method == "turn/start":
            self.calls.append((method, copy.deepcopy(params)))
            thread_id = params["threadId"]
            return {"turn": {"id": thread_id.replace("thread", "turn")}}
        if method == "turn/interrupt":
            self.calls.append((method, copy.deepcopy(params)))
            self._handle_turn_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": params["threadId"],
                        "turn": {
                            "id": params["turnId"],
                            "status": "interrupted",
                            "error": None,
                            "items": [],
                        },
                    },
                }
            )
            return {}
        return super()._request(method, params, timeout=timeout)


class AppServerTurnTests(unittest.TestCase):
    def test_protocol_terminal_failed_raises_typed_turn_failure(self):
        class TerminalFailedClient(ProtocolClient):
            def _request(self, method, params, *, timeout=None):
                if method == "turn/start":
                    self.calls.append((method, copy.deepcopy(params)))
                    thread_id = params["threadId"]
                    turn_id = thread_id.replace("thread", "turn")
                    self._handle_turn_notification(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": turn_id,
                                    "status": "failed",
                                    "error": {"code": "model_at_capacity"},
                                    "items": [],
                                },
                            },
                        }
                    )
                    return {"turn": {"id": turn_id}}
                return super()._request(method, params, timeout=timeout)

        client = TerminalFailedClient()

        with self.assertRaises(CodexTerminalTurnFailedError) as raised:
            client.run_turn(
                "question",
                work_type="maintenance_question_type_aggregate_review_1_candidate",
                sandbox="read-only",
                emit=lambda _line: None,
                output_schema={"type": "object", "properties": {}},
            )

        self.assertEqual(raised.exception.thread_id, "thread-1")
        self.assertEqual(raised.exception.turn_id, "turn-1")
        self.assertEqual(raised.exception.status, "failed")
        self.assertEqual(
            raised.exception.error,
            {"code": "model_at_capacity"},
        )
        self.assertEqual(client._turns, {})

    def test_structured_output_trailing_whitespace_stall_is_interrupted(self):
        class WhitespaceStallClient(ProtocolClient):
            def __init__(self):
                super().__init__(
                    turn_timeout=10,
                    structured_output_stall_timeout=0,
                )
                self.interrupted = []

            def _request(self, method, params, *, timeout=None):
                if method == "turn/start":
                    self.calls.append((method, copy.deepcopy(params)))
                    thread_id = params["threadId"]
                    turn_id = thread_id.replace("thread", "turn")
                    for delta in (
                        '{"status":"ok"',
                        " " * STRUCTURED_OUTPUT_TRAILING_WHITESPACE_CHARS,
                    ):
                        self._handle_message(
                            {
                                "method": "item/agentMessage/delta",
                                "params": {
                                    "threadId": thread_id,
                                    "turnId": turn_id,
                                    "delta": delta,
                                },
                            }
                        )
                    return {"turn": {"id": turn_id}}
                if method == "turn/interrupt":
                    self.interrupted.append(
                        (params["threadId"], params["turnId"])
                    )
                    return {}
                return super()._request(method, params, timeout=timeout)

        client = WhitespaceStallClient()

        with self.assertRaisesRegex(
            CodexTurnTimeoutError,
            "実質的な出力進捗のない空白生成",
        ):
            client.run_turn(
                "question",
                work_type="maintenance_explanation_candidate",
                sandbox="read-only",
                emit=lambda _line: None,
                output_schema={"type": "object", "properties": {}},
            )

        self.assertEqual(client.interrupted, [("thread-1", "turn-1")])
        self.assertEqual(client._turns, {})

    def test_completed_structured_message_without_turn_completion_is_validated(self):
        class MissingCompletionClient(ProtocolClient):
            def __init__(self):
                super().__init__(
                    turn_timeout=10,
                    structured_output_completion_grace=0,
                )

            def _request(self, method, params, *, timeout=None):
                if method == "turn/start":
                    self.calls.append((method, copy.deepcopy(params)))
                    thread_id = params["threadId"]
                    turn_id = thread_id.replace("thread", "turn")
                    self._handle_turn_notification(
                        {
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": {
                                    "id": "message-1",
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": '{"status":"ok"}',
                                },
                            },
                        }
                    )
                    return {"turn": {"id": turn_id}}
                if method == "turn/interrupt":
                    self.calls.append((method, copy.deepcopy(params)))
                    self._handle_turn_notification(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": params["threadId"],
                                "turn": {
                                    "id": params["turnId"],
                                    "status": "interrupted",
                                    "error": None,
                                    "items": [],
                                },
                            },
                        }
                    )
                    return {}
                return super()._request(method, params, timeout=timeout)

        client = MissingCompletionClient()

        result = client.run_turn(
            "question",
            work_type="maintenance_explanation_candidate",
            sandbox="read-only",
            emit=lambda _line: None,
            output_schema={"type": "object", "properties": {}},
        )

        self.assertEqual(result.final_message, '{"status":"ok"}')
        self.assertEqual(
            result.completion_mode,
            "completed_message_interrupted",
        )
        self.assertTrue(
            any(method == "turn/interrupt" for method, _params in client.calls)
        )
        client.close()

    def test_active_turn_deadline_raises_question_scoped_timeout(self):
        class HangingTurnClient(ProtocolClient):
            def __init__(self):
                super().__init__(turn_timeout=10)
                self.interrupted = []

            def _request(self, method, params, *, timeout=None):
                if method == "turn/start":
                    self.calls.append((method, copy.deepcopy(params)))
                    return {
                        "turn": {
                            "id": params["threadId"].replace("thread", "turn")
                        }
                    }
                if method == "turn/interrupt":
                    self.interrupted.append(
                        (params["threadId"], params["turnId"])
                    )
                    return {}
                return super()._request(method, params, timeout=timeout)

        client = HangingTurnClient()

        with self.assertRaisesRegex(
            CodexTurnTimeoutError,
            "turnが時間切れ",
        ):
            client.run_turn(
                "question",
                work_type="maintenance_explanation_candidate",
                sandbox="read-only",
                emit=lambda _line: None,
                turn_timeout=0.001,
            )

        self.assertEqual(client.interrupted, [("thread-1", "turn-1")])
        self.assertEqual(client._turns, {})
        self.assertEqual(client.turn_timeout, 10)

    def test_control_requests_allow_a_full_sixty_four_thread_startup_wave(self):
        class TimeoutClient(ProtocolClient):
            def __init__(self):
                super().__init__()
                self.timeouts = []

            def _request(self, method, params, *, timeout=None):
                self.timeouts.append((method, timeout))
                return super()._request(method, params, timeout=timeout)

        client = TimeoutClient()

        client._control_request("hooks/list", {"cwds": ["/isolated"]})
        client._control_request(
            "hooks/list",
            {"cwds": ["/isolated"]},
            timeout=7,
        )

        self.assertEqual(
            client.timeouts,
            [
                ("hooks/list", APP_SERVER_CONTROL_REQUEST_TIMEOUT_SECONDS),
                ("hooks/list", 7),
            ],
        )

    def test_hook_check_is_singleflight_cached_and_generation_scoped(self):
        client = ProtocolClient()
        cwd = Path("/isolated/model-workspace")

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = [
                executor.submit(client._assert_no_active_hooks, cwd)
                for _index in range(16)
            ]
            for future in futures:
                future.result(timeout=2)
        client._assert_no_active_hooks(cwd)

        hook_calls = [
            call for call in client.calls if call[0] == "hooks/list"
        ]
        self.assertEqual(len(hook_calls), 1)

        with client._state_lock:
            client._app_server_generation += 1
        client._assert_no_active_hooks(cwd)
        hook_calls = [
            call for call in client.calls if call[0] == "hooks/list"
        ]
        self.assertEqual(len(hook_calls), 2)

    def test_hook_check_shares_one_timeout_but_later_request_retries(self):
        class TimeoutHookClient(ProtocolClient):
            def __init__(self):
                super().__init__()
                self.hook_requests = 0
                self.hook_request_lock = threading.Lock()

            def _request(self, method, params, *, timeout=None):
                if method != "hooks/list":
                    return super()._request(method, params, timeout=timeout)
                with self.hook_request_lock:
                    self.hook_requests += 1
                time.sleep(0.05)
                raise CodexRequestTimeoutError("hooks/list timeout")

        client = TimeoutHookClient()
        barrier = threading.Barrier(8)

        def check():
            barrier.wait(timeout=2)
            client._assert_no_active_hooks(
                Path("/isolated/model-workspace")
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(check) for _index in range(8)]
            errors = []
            for future in futures:
                with self.assertRaises(CodexControlRequestTimeoutError) as raised:
                    future.result(timeout=2)
                errors.append(str(raised.exception))

        self.assertEqual(client.hook_requests, 1)
        self.assertEqual(errors, ["hooks/list timeout"] * 8)
        with self.assertRaises(CodexControlRequestTimeoutError):
            client._assert_no_active_hooks(
                Path("/isolated/model-workspace")
            )
        self.assertEqual(client.hook_requests, 2)

    def test_control_plane_is_bounded_while_300_model_turns_stay_active(self):
        class BoundedControlPlaneClient(ProtocolClient):
            CONTROL_METHODS = {
                "hooks/list",
                "thread/start",
                "mcpServerStatus/list",
                "turn/start",
            }

            def __init__(self):
                super().__init__()
                self.control_lock = threading.RLock()
                self.active_control_requests = 0
                self.peak_control_requests = 0
                self.started_turns = []
                self.all_turns_started = threading.Event()

            def _request(self, method, params, *, timeout=None):
                if method not in self.CONTROL_METHODS:
                    return super()._request(method, params, timeout=timeout)
                with self.control_lock:
                    self.active_control_requests += 1
                    self.peak_control_requests = max(
                        self.peak_control_requests,
                        self.active_control_requests,
                    )
                try:
                    time.sleep(0.005)
                    self.calls.append((method, copy.deepcopy(params)))
                    if method == "hooks/list":
                        return {
                            "data": [
                                {
                                    "cwd": params["cwds"][0],
                                    "hooks": [],
                                    "warnings": [],
                                    "errors": [],
                                }
                            ]
                        }
                    if method == "thread/start":
                        with self.control_lock:
                            self.turn_number += 1
                            number = self.turn_number
                        sandbox_type = (
                            "readOnly"
                            if params["sandbox"] == "read-only"
                            else "workspaceWrite"
                        )
                        return {
                            "thread": {
                                "id": f"thread-{number}",
                                "sessionId": f"session-{number}",
                            },
                            "model": params["model"],
                            "modelProvider": "openai",
                            "serviceTier": params.get("serviceTier"),
                            "sandbox": {
                                "type": sandbox_type,
                                "networkAccess": False,
                            },
                        }
                    if method == "mcpServerStatus/list":
                        return {"data": [], "nextCursor": None}
                    thread_id = params["threadId"]
                    turn_id = thread_id.replace("thread", "turn")
                    with self.control_lock:
                        self.started_turns.append((thread_id, turn_id))
                        if len(self.started_turns) == 300:
                            self.all_turns_started.set()
                    self._handle_message(
                        {
                            "method": "turn/started",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": turn_id,
                                    "status": "inProgress",
                                },
                            },
                        }
                    )
                    return {"turn": {"id": turn_id}}
                finally:
                    with self.control_lock:
                        self.active_control_requests -= 1

            def complete_all(self):
                with self.control_lock:
                    started_turns = list(self.started_turns)
                for thread_id, turn_id in started_turns:
                    self._handle_turn_notification(
                        {
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": {
                                    "id": f"answer-{turn_id}",
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": '{"status":"ok"}',
                                },
                            },
                        }
                    )
                    self._handle_turn_notification(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": turn_id,
                                    "status": "completed",
                                    "error": None,
                                    "items": [],
                                },
                            },
                        }
                    )

        client = BoundedControlPlaneClient()
        with ThreadPoolExecutor(max_workers=300) as executor:
            futures = [
                executor.submit(
                    client.run_turn,
                    f"question-{index}",
                    work_type="maintenance_law_context_candidate",
                    sandbox="read-only",
                    emit=lambda _line: None,
                    turn_group="gas-shunin-otsu",
                )
                for index in range(300)
            ]
            self.assertTrue(client.all_turns_started.wait(10))
            turn_budget = client.turn_budget.snapshot()
            control_budget = client.control_plane_budget.snapshot()
            model_turns = client._model_turn_snapshot()
            public_status = client.public_status(refresh=False)
            client.complete_all()
            self.assertEqual(turn_budget["inFlight"], 300)
            self.assertEqual(turn_budget["peakInFlight"], 300)
            self.assertEqual(model_turns["inFlight"], 300)
            self.assertEqual(model_turns["peakInFlight"], 300)
            self.assertEqual(
                control_budget["capacity"],
                APP_SERVER_CONTROL_PLANE_CAPACITY,
            )
            self.assertGreater(
                control_budget["peakInFlight"],
                8,
            )
            self.assertLessEqual(
                client.peak_control_requests,
                APP_SERVER_CONTROL_PLANE_CAPACITY,
            )
            self.assertEqual(public_status["turnBudget"], turn_budget)
            self.assertEqual(public_status["controlPlaneBudget"], control_budget)
            self.assertEqual(public_status["modelTurns"], model_turns)
            results = [future.result(timeout=5) for future in futures]

        self.assertEqual(len({result.thread_id for result in results}), 300)
        self.assertEqual(client._model_turn_snapshot()["inFlight"], 0)
        self.assertEqual(client._model_turn_snapshot()["peakInFlight"], 300)
        self.assertTrue(
            all(result.final_message == '{"status":"ok"}' for result in results)
        )
        self.assertEqual(
            client.subscription_forces,
            [(False, "standard")] * 300,
        )
        methods = [method for method, _params in client.calls]
        for method in BoundedControlPlaneClient.CONTROL_METHODS:
            self.assertEqual(
                methods.count(method),
                1 if method == "hooks/list" else 300,
            )

    def test_model_turn_snapshot_uses_protocol_lifecycle_notifications(self):
        client = ProtocolClient()
        started = {
            "method": "turn/started",
            "params": {
                "threadId": "thread-early",
                "turn": {"id": "turn-early", "status": "inProgress"},
            },
        }
        client._handle_message(started)
        client._handle_message(started)

        self.assertEqual(
            client._model_turn_snapshot(),
            {"capacity": 300, "inFlight": 1, "peakInFlight": 1},
        )
        started_at, started_monotonic = client._notified_turn_started[
            ("thread-early", "turn-early")
        ]
        self.assertIn("T", started_at)
        self.assertGreater(started_monotonic, 0)

        client._handle_message(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-early",
                    "turn": {
                        "id": "turn-early",
                        "status": "completed",
                        "error": None,
                        "items": [],
                    },
                },
            }
        )

        self.assertEqual(
            client._model_turn_snapshot(),
            {"capacity": 300, "inFlight": 0, "peakInFlight": 1},
        )
        self.assertEqual(
            [
                notification["method"]
                for notification in client._early_notifications[
                    ("thread-early", "turn-early")
                ]
            ],
            ["turn/completed"],
        )
        client.close()

    def test_model_turn_lifecycle_callback_uses_monotonic_durations(self):
        client = ProtocolClient()
        events = []
        state = _TurnState(
            thread_id="thread-timed",
            turn_id="turn-timed",
            emit=lambda _line: None,
            requested_monotonic=2.0,
            on_model_turn_event=events.append,
        )
        client._turns[("thread-timed", "turn-timed")] = state

        with patch(
            "tools.question_review_console.codex_app_server.time.monotonic",
            side_effect=[5.0, 8.0],
        ):
            client._handle_message(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread-timed",
                        "turn": {
                            "id": "turn-timed",
                            "status": "inProgress",
                        },
                    },
                }
            )
            client._handle_message(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-timed",
                        "turn": {
                            "id": "turn-timed",
                            "status": "completed",
                            "error": None,
                            "items": [],
                        },
                    },
                }
            )

        self.assertEqual([value["event"] for value in events], ["started", "finished"])
        self.assertEqual(events[0]["queueWaitSeconds"], 3.0)
        self.assertEqual(events[1]["durationSeconds"], 3.0)
        self.assertEqual(
            client._model_turn_snapshot(),
            {"capacity": 300, "inFlight": 0, "peakInFlight": 1},
        )
        client.close()

    def test_early_protocol_lifecycle_is_attached_to_turn_result(self):
        class EarlyLifecycleClient(ProtocolClient):
            def _request(self, method, params, *, timeout=None):
                if method != "turn/start":
                    return super()._request(method, params, timeout=timeout)
                self.calls.append((method, copy.deepcopy(params)))
                thread_id = params["threadId"]
                turn_id = thread_id.replace("thread", "turn")
                for message in (
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": thread_id,
                            "turn": {
                                "id": turn_id,
                                "status": "inProgress",
                            },
                        },
                    },
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {
                                "type": "agentMessage",
                                "phase": "final_answer",
                                "text": '{"status":"ok"}',
                            },
                        },
                    },
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": thread_id,
                            "turn": {
                                "id": turn_id,
                                "status": "completed",
                                "error": None,
                                "items": [],
                            },
                        },
                    },
                ):
                    self._handle_turn_notification(message)
                return {"turn": {"id": turn_id}}

        client = EarlyLifecycleClient()
        events = []

        result = client.run_turn(
            "question",
            work_type="maintenance_explanation_candidate",
            sandbox="read-only",
            emit=lambda _line: None,
            on_model_turn_event=events.append,
        )

        self.assertEqual([value["event"] for value in events], ["started", "finished"])
        self.assertIsNotNone(result.model_turn_started_at)
        self.assertIsNotNone(result.model_turn_finished_at)
        self.assertGreaterEqual(result.model_turn_queue_wait_seconds, 0.0)
        self.assertGreaterEqual(result.model_turn_duration_seconds, 0.0)
        client.close()

    def test_turn_item_logs_include_safe_failure_evidence_and_relative_paths(self):
        class StructuredEmitter:
            def __init__(self):
                self.lines = []
                self.events = []

            def __call__(self, line):
                self.lines.append(line)

            def event(self, value):
                self.events.append(value)

        client = ProtocolClient()
        emit = StructuredEmitter()
        state = _TurnState(thread_id="thread", turn_id="turn", emit=emit)
        command_item = {
            "id": "command-1",
            "type": "commandExecution",
            "command": "python verify.py",
            "status": "failed",
            "exitCode": 9,
            "aggregatedOutput": "verification failed near question 12",
        }
        client._record_turn_item(state, command_item)
        client._record_turn_item(state, command_item)

        changed_path = str(Path.cwd() / "tools" / "sample.py")
        file_item = {
            "id": "change-1",
            "type": "fileChange",
            "changes": [{"path": changed_path}],
        }
        client._record_turn_item(state, file_item)
        client._record_turn_item(state, file_item)

        self.assertEqual(emit.lines, [])
        self.assertEqual(len(emit.events), 2)
        self.assertIn("exitCode=9", emit.events[0]["message"])
        self.assertIn(
            "verification failed near question 12",
            emit.events[0]["outputTail"],
        )
        self.assertEqual(emit.events[0]["commandStatus"], "failed")
        self.assertEqual(emit.events[0]["exitCode"], 9)
        self.assertEqual(
            client._failure_output_tail(
                "Authorization: Bearer sensitive-token"
            ),
            "<redacted sensitive output>",
        )
        self.assertIn("tools/sample.py", emit.events[1]["message"])
        self.assertEqual(emit.events[1]["changedPaths"], ["tools/sample.py"])
        self.assertNotIn(str(Path.cwd()), emit.events[1]["message"])
        self.assertEqual(state.changed_files, {changed_path})

    def test_run_turn_calls_heartbeat_while_waiting(self):
        class DelayedProtocolClient(ProtocolClient):
            def _request(self, method, params, *, timeout=None):
                if method != "turn/start":
                    return super()._request(method, params, timeout=timeout)
                self.calls.append((method, copy.deepcopy(params)))
                thread_id = params["threadId"]
                turn_id = thread_id.replace("thread", "turn")

                def complete():
                    self._handle_turn_notification(
                        {
                            "method": "item/completed",
                            "params": {
                                "threadId": thread_id,
                                "turnId": turn_id,
                                "item": {
                                    "id": "answer-1",
                                    "type": "agentMessage",
                                    "phase": "final_answer",
                                    "text": '{"status":"ok"}',
                                },
                            },
                        }
                    )
                    self._handle_turn_notification(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": turn_id,
                                    "status": "completed",
                                    "error": None,
                                    "items": [],
                                },
                            },
                        }
                    )

                self.timer = threading.Timer(0.06, complete)
                self.timer.daemon = True
                self.timer.start()
                return {"turn": {"id": turn_id}}

        client = DelayedProtocolClient()
        heartbeats = []
        with patch(
            "tools.question_review_console.codex_app_server."
            "TURN_HEARTBEAT_INTERVAL_SECONDS",
            0.01,
        ):
            result = client.run_turn(
                "evaluate",
                work_type="evaluation",
                sandbox="read-only",
                emit=lambda _line: None,
                heartbeat=lambda: heartbeats.append(True),
            )
        client.timer.join(1)

        self.assertEqual(result.final_message, '{"status":"ok"}')
        self.assertGreaterEqual(len(heartbeats), 1)

    def test_runtime_home_copies_only_chatgpt_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            source_home = Path(directory) / "source"
            source_home.mkdir()
            (source_home / "auth.json").write_text('{"auth": "chatgpt"}', encoding="utf-8")
            (source_home / "config.toml").write_text(
                '[agents.explorer]\nconfig_file = "/tmp/unsafe.toml"\n',
                encoding="utf-8",
            )
            agents = source_home / "agents"
            agents.mkdir()
            (agents / "other.toml").write_text(
                'name = "other"\ndescription = "unsafe"\n'
                'developer_instructions = "unsafe"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(source_home)}):
                client = CodexAppServerClient(
                    Path(directory), binary_path=Path("/bin/echo")
                )
                runtime_home = client._prepare_isolated_codex_home()
                self.assertEqual(
                    (runtime_home / "auth.json").read_text(encoding="utf-8"),
                    '{"auth": "chatgpt"}',
                )
                self.assertFalse((runtime_home / "config.toml").exists())
                self.assertFalse((runtime_home / "agents").exists())
                model_workspace = runtime_home / "model-workspace"
                self.assertTrue(model_workspace.is_dir())
                self.assertEqual(model_workspace.stat().st_mode & 0o777, 0o700)
                client.close()
                self.assertFalse(runtime_home.exists())

    def test_trusted_research_agent_config_is_private_exact_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as directory:
            source_home = Path(directory) / "source"
            source_home.mkdir()
            (source_home / "auth.json").write_text(
                '{"auth": "chatgpt"}', encoding="utf-8"
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(source_home)}):
                client = CodexAppServerClient(
                    Path(directory), binary_path=Path("/bin/echo")
                )
                runtime_home = client._prepare_isolated_codex_home()
                config_path = client._trusted_research_agent_config()

                self.assertEqual(
                    config_path,
                    runtime_home / RESEARCH_AGENT_CONFIG_FILENAME,
                )
                self.assertEqual(
                    config_path.read_text(encoding="utf-8"),
                    RESEARCH_AGENT_CONFIG,
                )
                self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
                self.assertFalse((runtime_home / "agents").exists())

                config_path.chmod(0o644)
                with self.assertRaisesRegex(SubscriptionGateError, "安全に確認"):
                    client._trusted_research_agent_config()
                config_path.chmod(0o600)
                config_path.write_text("name = 'tampered'\n", encoding="utf-8")
                with self.assertRaisesRegex(SubscriptionGateError, "安全に確認"):
                    client._trusted_research_agent_config()
                client.close()

    def test_isolated_config_rejects_external_layers_and_custom_roles(self):
        client = ProtocolClient()
        client._assert_isolated_config_layers(
            {
                "layers": [
                    {"name": {"type": "sessionFlags"}},
                    {"name": {"type": "system"}},
                ]
            }
        )
        client._assert_no_custom_agent_config(
            {"agents": {"max_threads": 3, "max_depth": 1}}
        )

        with self.assertRaisesRegex(SubscriptionGateError, "config layer"):
            client._assert_isolated_config_layers(
                {"layers": [{"name": {"type": "user"}}]}
            )
        with self.assertRaisesRegex(SubscriptionGateError, "explorer"):
            client._assert_no_custom_agent_config(
                {
                    "agents": {
                        "max_threads": 3,
                        "explorer": {"config_file": "/tmp/unsafe.toml"},
                    }
                }
            )

    def test_startup_disables_configured_mcp_plugins_and_shell_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            (codex_home / "config.toml").write_text(
                '[mcp_servers.lawzilla]\ncommand = "lawzilla"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                client = CodexAppServerClient(
                    Path(directory), binary_path=Path("/bin/echo")
                )
                command = client._app_server_command()

        self.assertIn('shell_environment_policy.inherit="none"', command)
        self.assertIn(
            f'shell_environment_policy.set={{PATH="{SAFE_SHELL_PATH}"}}',
            command,
        )
        self.assertIn(
            'mcp_servers.lawzilla={command="/usr/bin/false",enabled=false}',
            command,
        )
        self.assertIn("plugins", command)
        self.assertIn("hooks", command)
        self.assertIn("browser_use", command)
        self.assertIn("multi_agent", command)
        self.assertEqual(command[command.index("multi_agent") - 1], "--disable")
        self.assertNotIn(f"agents.max_threads={APP_SERVER_AGENT_THREAD_CAP}", command)
        self.assertNotIn(f"agents.max_depth={APP_SERVER_AGENT_MAX_DEPTH}", command)
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn("notify=[]", command)
        self.assertIn("analytics.enabled=false", command)
        self.assertIn('otel.exporter="none"', command)
        self.assertIn('otel.metrics_exporter="none"', command)
        self.assertIn('otel.trace_exporter="none"', command)
        self.assertIn("otel.log_user_prompt=false", command)
        self.assertNotIn("openai_base_url=null", command)
        self.assertNotIn("chatgpt_base_url=null", command)

    def test_shell_environment_allows_only_fixed_path(self):
        safe = {
            "shell_environment_policy": {
                "inherit": "none",
                "set": {"PATH": SAFE_SHELL_PATH},
            }
        }

        CodexAppServerClient._assert_safe_shell_environment(safe)

        for explicit_path in ("/usr/bin", f"{SAFE_SHELL_PATH}:/tmp/bin"):
            with self.subTest(path=explicit_path):
                with self.assertRaises(SubscriptionGateError):
                    CodexAppServerClient._assert_safe_shell_environment(
                        {
                            "shell_environment_policy": {
                                "inherit": "none",
                                "set": {"PATH": explicit_path},
                            }
                        }
                    )

    def test_each_run_starts_a_fresh_thread_with_explicit_sandbox(self):
        client = ProtocolClient()
        started = []

        first = client.run_turn(
            "evaluate",
            work_type="evaluation",
            sandbox="read-only",
            output_schema={"type": "object"},
            emit=lambda _line: None,
            on_thread_started=lambda thread_id, session_id: started.append(
                (thread_id, session_id)
            ),
            on_turn_started=lambda thread_id, turn_id: started.append(
                (thread_id, turn_id)
            ),
        )
        second = client.run_turn(
            "maintain",
            work_type="maintenance",
            sandbox="workspace-write",
            emit=lambda _line: None,
        )

        self.assertNotEqual(first.thread_id, second.thread_id)
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(
            client.subscription_forces,
            [(False, "standard"), (False, "standard")],
        )
        second_turn = next(
            params
            for method, params in client.calls
            if method == "turn/start" and params["threadId"] == second.thread_id
        )
        self.assertEqual(second_turn["sandboxPolicy"]["writableRoots"], [])
        self.assertTrue(second_turn["sandboxPolicy"]["excludeTmpdirEnvVar"])
        self.assertTrue(second_turn["sandboxPolicy"]["excludeSlashTmp"])
        self.assertEqual(first.final_message, '{"status":"ok"}')
        self.assertEqual(first.model, "gpt-5.6-luna")
        self.assertEqual(first.reasoning_effort, "high")
        self.assertEqual(
            started,
            [
                ("thread-1", "session-1"),
                ("thread-1", "turn-1"),
            ],
        )
        methods = [method for method, _params in client.calls]
        self.assertEqual(
            methods,
            [
                "hooks/list",
                "thread/start",
                "mcpServerStatus/list",
                "turn/start",
                "hooks/list",
                "thread/start",
                "mcpServerStatus/list",
                "turn/start",
            ],
        )
        thread_params = [params for method, params in client.calls if method == "thread/start"]
        self.assertEqual(thread_params[0]["sandbox"], "read-only")
        self.assertEqual(thread_params[1]["sandbox"], "workspace-write")
        self.assertEqual(
            Path(thread_params[0]["cwd"]),
            client.isolated_model_workspace,
        )
        self.assertEqual(Path(thread_params[1]["cwd"]), Path.cwd().resolve())
        self.assertTrue(thread_params[0]["ephemeral"])
        self.assertFalse(thread_params[1]["ephemeral"])
        self.assertTrue(all(params["approvalPolicy"] == "never" for params in thread_params))
        self.assertTrue(all(params["serviceTier"] is None for params in thread_params))
        self.assertTrue(all(params["modelProvider"] == "openai" for params in thread_params))
        self.assertTrue(all(params["model"] == "gpt-5.6-luna" for params in thread_params))
        self.assertTrue(all(params["config"]["features"]["fast_mode"] is False for params in thread_params))
        self.assertTrue(all(params["config"]["features"]["plugins"] is False for params in thread_params))
        self.assertTrue(all(params["config"]["features"]["hooks"] is False for params in thread_params))
        self.assertTrue(all(params["config"]["features"]["browser_use"] is False for params in thread_params))
        self.assertFalse(thread_params[0]["config"]["features"]["multi_agent"])
        self.assertFalse(thread_params[1]["config"]["features"]["multi_agent"])
        self.assertTrue(
            all(
                params["config"]["agents"]["max_threads"]
                == APP_SERVER_AGENT_THREAD_CAP
                for params in thread_params
            )
        )
        self.assertTrue(
            all(
                params["config"]["agents"]["max_depth"]
                == APP_SERVER_AGENT_MAX_DEPTH
                for params in thread_params
            )
        )
        self.assertTrue(
            all(
                RESEARCH_AGENT_ROLE not in params["config"]["agents"]
                for params in thread_params
            )
        )
        self.assertTrue(all(params["config"]["web_search"] == "live" for params in thread_params))
        self.assertIn("外部状態は変更しない", thread_params[1]["developerInstructions"])
        self.assertIn("subagentは使わない", thread_params[1]["developerInstructions"])
        turn_params = [params for method, params in client.calls if method == "turn/start"]
        self.assertEqual(turn_params[0]["sandboxPolicy"]["type"], "readOnly")
        self.assertEqual(turn_params[1]["sandboxPolicy"]["type"], "workspaceWrite")
        self.assertTrue(all(params["sandboxPolicy"]["networkAccess"] is False for params in turn_params))
        self.assertTrue(all(params["serviceTier"] is None for params in turn_params))
        self.assertTrue(all(params["effort"] == "high" for params in turn_params))

    def test_retry_model_is_applied_to_thread_and_high_effort_turn(self):
        client = ProtocolClient()

        result = client.run_turn(
            "retry failed question",
            work_type="maintenance_question_type_candidate",
            sandbox="read-only",
            emit=lambda _line: None,
            model=QUESTION_MAINTENANCE_RETRY_MODEL,
            reasoning_effort="high",
        )

        thread_params = next(
            params for method, params in client.calls if method == "thread/start"
        )
        turn_params = next(
            params for method, params in client.calls if method == "turn/start"
        )
        self.assertEqual(thread_params["model"], "gpt-5.6-sol")
        self.assertEqual(turn_params["effort"], "high")
        self.assertEqual(result.model, "gpt-5.6-sol")
        self.assertEqual(result.reasoning_effort, "high")

    def test_fast_mode_is_rejected_before_provider_request(self):
        client = ProtocolClient()

        with self.assertRaisesRegex(ValueError, "Standard mode"):
            client.run_turn(
                "fast maintenance",
                work_type="maintenance_question_type_candidate",
                sandbox="read-only",
                emit=lambda _line: None,
                speed_mode=FAST_SPEED_MODE,
                turn_group="gas",
            )

        self.assertEqual(client.calls, [])

    def test_success_receipt_probe_interrupts_writer_and_returns_terminal_result(self):
        client = ReceiptInterruptProtocolClient()
        probe_count = 0

        def completion_probe():
            nonlocal probe_count
            probe_count += 1
            return True

        result = client.run_turn(
            "maintain",
            work_type="maintenance",
            sandbox="workspace-write",
            emit=lambda _line: None,
            completion_probe=completion_probe,
        )

        self.assertGreaterEqual(probe_count, 1)
        self.assertEqual(result.completion_mode, "receipt_interrupted")
        self.assertEqual(
            result.final_message,
            "成功receipt保存後にturnを停止しました。",
        )
        self.assertTrue(
            any(method == "turn/interrupt" for method, _params in client.calls)
        )

    def test_read_only_research_uses_one_thread_without_subagents(self):
        client = ProtocolClient()
        with tempfile.TemporaryDirectory() as directory:
            result = client.run_turn(
                "research",
                work_type="maintenance_research",
                sandbox="read-only",
                emit=lambda _line: None,
                cwd=Path(directory),
            )

        self.assertEqual(result.model, "gpt-5.6-luna")
        self.assertEqual(result.subagent_thread_ids, ())
        self.assertEqual(result.subagent_models, ())
        self.assertEqual(result.subagent_reasoning_efforts, ())
        thread_params = next(
            params for method, params in client.calls if method == "thread/start"
        )
        self.assertFalse(thread_params["config"]["features"]["multi_agent"])
        self.assertEqual(
            thread_params["config"]["agents"]["max_threads"],
            APP_SERVER_AGENT_THREAD_CAP,
        )
        self.assertEqual(
            thread_params["config"]["agents"]["max_depth"],
            APP_SERVER_AGENT_MAX_DEPTH,
        )
        self.assertNotIn(RESEARCH_AGENT_ROLE, thread_params["config"]["agents"])
        self.assertTrue(thread_params["ephemeral"])
        self.assertIn("subagentは使わず", thread_params["developerInstructions"])
        self.assertEqual(client.turn_timeout, DEFAULT_TURN_TIMEOUT_SECONDS)


    def test_research_rejects_any_project_custom_agent_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            agents = project / ".codex" / "agents"
            agents.mkdir(parents=True)
            (agents / "custom.toml").write_text(
                'name = "other"\ndescription = "override"\n'
                'developer_instructions = "override"\n',
                encoding="utf-8",
            )
            client = ProtocolClient()
            with self.assertRaisesRegex(SubscriptionGateError, "custom agent"):
                client.run_turn(
                    "research",
                    work_type="maintenance_research",
                    sandbox="read-only",
                    emit=lambda _line: None,
                    cwd=project,
                )

        self.assertNotIn("thread/start", [method for method, _params in client.calls])

    def test_official_source_review_is_read_only_without_subagents(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            agents = project / ".codex" / "agents"
            agents.mkdir(parents=True)
            (agents / "custom.toml").write_text(
                'name = "other"\ndescription = "override"\n'
                'developer_instructions = "override"\n',
                encoding="utf-8",
            )
            client = ProtocolClient()
            result = client.run_turn(
                "official source",
                work_type="official_source_review",
                sandbox="read-only",
                emit=lambda _line: None,
                cwd=project,
            )

        self.assertEqual(result.subagent_thread_ids, ())
        thread_params = next(
            params for method, params in client.calls if method == "thread/start"
        )
        self.assertEqual(thread_params["cwd"], str(project.resolve()))
        self.assertTrue(thread_params["ephemeral"])
        self.assertFalse(thread_params["config"]["features"]["multi_agent"])
        self.assertIn(
            "公式問題冊子とのread-only照合専用",
            thread_params["developerInstructions"],
        )

    def test_research_does_not_spawn_children_even_when_fixture_requests_them(self):
        client = ProtocolClient()
        client.research_child_count = 20
        with tempfile.TemporaryDirectory() as directory:
            result = client.run_turn(
                "research",
                work_type="maintenance_research",
                sandbox="read-only",
                emit=lambda _line: None,
                cwd=Path(directory),
            )

        self.assertEqual(result.subagent_thread_ids, ())

    def test_four_work_types_use_distinct_sessions_and_expected_sandboxes(self):
        client = ProtocolClient()
        specs = (
            ("maintenance", "workspace-write", False),
            ("evaluation", "read-only", True),
            ("rework", "workspace-write", False),
            ("reevaluation", "read-only", True),
        )

        results = [
            client.run_turn(
                work_type,
                work_type=work_type,
                sandbox=sandbox,
                emit=lambda _line: None,
            )
            for work_type, sandbox, _ephemeral in specs
        ]

        self.assertEqual(len({result.thread_id for result in results}), 4)
        self.assertEqual(len({result.session_id for result in results}), 4)
        thread_params = [
            params for method, params in client.calls if method == "thread/start"
        ]
        self.assertEqual(
            [params["sandbox"] for params in thread_params],
            [sandbox for _work_type, sandbox, _ephemeral in specs],
        )
        self.assertEqual(
            [params["ephemeral"] for params in thread_params],
            [ephemeral for _work_type, _sandbox, ephemeral in specs],
        )
        self.assertEqual(
            [params["threadSource"] for params in thread_params],
            [
                f"exam_scraper_{work_type}"
                for work_type, _sandbox, _ephemeral in specs
            ],
        )

    def test_external_mcp_tools_block_turn_start(self):
        class UnsafeProtocolClient(ProtocolClient):
            def _request(self, method, params, *, timeout=None):
                if method == "mcpServerStatus/list":
                    self.calls.append((method, copy.deepcopy(params)))
                    return {
                        "data": [
                            {
                                "name": "external",
                                "serverInfo": {"name": "external"},
                                "tools": {"write": {}},
                                "resources": [],
                                "resourceTemplates": [],
                            }
                        ],
                        "nextCursor": None,
                    }
                return super()._request(method, params, timeout=timeout)

        client = UnsafeProtocolClient()

        with self.assertRaises(SubscriptionGateError):
            client.run_turn(
                "maintain",
                work_type="maintenance",
                sandbox="workspace-write",
                emit=lambda _line: None,
            )

        self.assertNotIn("turn/start", [method for method, _params in client.calls])

    def test_rejects_when_requested_model_is_not_applied(self):
        class WrongModelClient(ProtocolClient):
            def _request(self, method, params, *, timeout=None):
                response = super()._request(method, params, timeout=timeout)
                if method == "thread/start":
                    response["model"] = "gpt-other"
                return response

        client = WrongModelClient()

        with self.assertRaisesRegex(SubscriptionGateError, "gpt-5.6-luna"):
            client.run_turn(
                "maintain",
                work_type="maintenance",
                sandbox="workspace-write",
                emit=lambda _line: None,
            )

        self.assertNotIn("turn/start", [method for method, _params in client.calls])

    def test_active_hooks_block_thread_start(self):
        class UnsafeHookClient(ProtocolClient):
            def _request(self, method, params, *, timeout=None):
                if method == "hooks/list":
                    self.calls.append((method, copy.deepcopy(params)))
                    return {
                        "data": [
                            {
                                "cwd": params["cwds"][0],
                                "hooks": [{"enabled": True}],
                                "warnings": [],
                                "errors": [],
                            }
                        ]
                    }
                return super()._request(method, params, timeout=timeout)

        client = UnsafeHookClient()
        with self.assertRaises(SubscriptionGateError):
            client.run_turn(
                "maintain",
                work_type="maintenance",
                sandbox="workspace-write",
                emit=lambda _line: None,
            )

        self.assertNotIn("thread/start", [method for method, _params in client.calls])

    def test_custom_base_url_is_rejected(self):
        client = ProtocolClient()
        client._request = lambda method, params: {
            "config": {
                "openai_base_url": "https://router.example/v1",
                "chatgpt_base_url": None,
                "forced_login_method": "chatgpt",
            }
        }

        with self.assertRaises(SubscriptionGateError):
            client._assert_official_chatgpt_endpoint()

    def test_unexpected_approval_requests_are_declined(self):
        client = ProtocolClient()
        client._handle_server_request(
            {
                "id": "server-request-1",
                "method": "item/commandExecution/requestApproval",
                "params": {},
            }
        )

        self.assertEqual(
            client.sent,
            [{"id": "server-request-1", "result": {"decision": "decline"}}],
        )

    def test_recovery_records_completed_turn_id_without_interrupting_it(self):
        client = ProtocolClient()
        recorded = []
        interrupted = []
        client._request = lambda method, params, timeout=None: {
            "thread": {
                "turns": [{"id": "turn-completed-1", "status": "completed"}]
            }
        }
        client._interrupt_turn = lambda thread_id, turn_id: interrupted.append(
            (thread_id, turn_id)
        )

        client._interrupt_active_turns(
            "thread-1",
            lambda thread_id, turn_id: recorded.append((thread_id, turn_id)),
        )

        self.assertEqual(recorded, [("thread-1", "turn-completed-1")])
        self.assertEqual(interrupted, [])


if __name__ == "__main__":
    unittest.main()
