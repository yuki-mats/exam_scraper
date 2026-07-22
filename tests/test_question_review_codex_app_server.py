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
    CodexAppServerError,
    CodexAppServerClient,
    DEFAULT_TURN_TIMEOUT_SECONDS,
    QUESTION_MAINTENANCE_RETRY_MODEL,
    RESEARCH_AGENT_CONFIG,
    RESEARCH_AGENT_CONFIG_FILENAME,
    RESEARCH_AGENT_ROLE,
    SAFE_SHELL_PATH,
    SubscriptionGateError,
    _TurnState,
    adapt_output_schema_for_app_server,
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
        self.assertEqual(status["model"], "gpt-5.5")
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

    def test_rejects_non_subscription_accounts(self):
        for account in (
            {"account": {"type": "apiKey"}},
            {"account": {"type": "amazonBedrock", "credentialSource": "env"}},
            {"account": None},
        ):
            with self.subTest(account=account):
                with self.assertRaises(SubscriptionGateError):
                    validate_subscription_access(account, rate_limit_response())

    def test_rejects_usage_based_unknown_credit_and_spend_paths(self):
        cases = []
        cases.append((account_response("self_serve_business_usage_based"), rate_limit_response("self_serve_business_usage_based")))
        cases.append((account_response("unknown"), rate_limit_response("unknown")))
        credits = rate_limit_response()
        credits["rateLimits"]["credits"]["hasCredits"] = True
        cases.append((account_response(), credits))
        missing_credits = rate_limit_response()
        missing_credits["rateLimits"]["credits"] = None
        cases.append((account_response(), missing_credits))
        spend = rate_limit_response()
        spend["rateLimits"]["individualLimit"] = {
            "limit": "10",
            "used": "0",
            "remainingPercent": 100,
            "resetsAt": 1,
        }
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

    def test_auxiliary_null_credits_are_allowed_but_enabled_credits_are_rejected(self):
        allowed = rate_limit_response()
        allowed["rateLimitResetCredits"] = {
            "availableCount": 1,
            "credits": [{"status": "available", "title": "Full reset"}],
        }
        validate_subscription_access(account_response(), allowed)

        blocked = copy.deepcopy(allowed)
        blocked["rateLimitsByLimitId"]["codex_bengalfox"]["credits"] = {
            "hasCredits": True
        }
        with self.assertRaises(SubscriptionGateError):
            validate_subscription_access(account_response(), blocked)

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
    def __init__(self):
        super().__init__(Path.cwd(), binary_path=Path("/bin/echo"))
        self.calls = []
        self.turn_number = 0
        self.sent = []
        self.subscription_forces = []
        self.research_threads = set()
        self.subagent_parents = {}
        self.research_child_count = 2
        self.research_child_model = "gpt-5.5"
        self.research_child_effort = "high"
        self.research_agent_config_path = Path(
            "/isolated/question-maintenance-explorer.toml"
        )

    def assert_subscription_access(self, *, force=True):
        self.subscription_forces.append(force)
        return {"allowed": True, "planType": "pro"}

    def _trusted_research_agent_config(self):
        return self.research_agent_config_path

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
                "serviceTier": None,
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
        self.assertEqual(client.subscription_forces, [True, True])
        second_turn = next(
            params
            for method, params in client.calls
            if method == "turn/start" and params["threadId"] == second.thread_id
        )
        self.assertEqual(second_turn["sandboxPolicy"]["writableRoots"], [])
        self.assertTrue(second_turn["sandboxPolicy"]["excludeTmpdirEnvVar"])
        self.assertTrue(second_turn["sandboxPolicy"]["excludeSlashTmp"])
        self.assertEqual(first.final_message, '{"status":"ok"}')
        self.assertEqual(first.model, "gpt-5.5")
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
        self.assertTrue(thread_params[0]["ephemeral"])
        self.assertFalse(thread_params[1]["ephemeral"])
        self.assertTrue(all(params["approvalPolicy"] == "never" for params in thread_params))
        self.assertTrue(all(params["serviceTier"] is None for params in thread_params))
        self.assertTrue(all(params["modelProvider"] == "openai" for params in thread_params))
        self.assertTrue(all(params["model"] == "gpt-5.5" for params in thread_params))
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

        self.assertEqual(result.model, "gpt-5.5")
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

        with self.assertRaisesRegex(SubscriptionGateError, "gpt-5.5"):
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
