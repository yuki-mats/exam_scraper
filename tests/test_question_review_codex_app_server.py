import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.codex_app_server import (
    CodexAppServerClient,
    SubscriptionGateError,
    validate_subscription_access,
)


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
        self.assertEqual(status["turnReasoningEffort"], "high")

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

    def assert_subscription_access(self, *, force=True):
        self.subscription_forces.append(force)
        return {"allowed": True, "planType": "pro"}

    def _request(self, method, params, *, timeout=None):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "thread/start":
            self.turn_number += 1
            thread_id = f"thread-{self.turn_number}"
            sandbox_type = "readOnly" if params["sandbox"] == "read-only" else "workspaceWrite"
            return {
                "thread": {"id": thread_id, "sessionId": f"session-{self.turn_number}"},
                "model": "gpt-5.5",
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
        raise AssertionError(method)

    def _send(self, message):
        self.sent.append(copy.deepcopy(dict(message)))


class AppServerTurnTests(unittest.TestCase):
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
            'mcp_servers.lawzilla={command="/usr/bin/false",enabled=false}',
            command,
        )
        self.assertIn("plugins", command)
        self.assertIn("hooks", command)
        self.assertIn("browser_use", command)
        self.assertIn('forced_login_method="chatgpt"', command)
        self.assertIn("notify=[]", command)
        self.assertIn("analytics.enabled=false", command)
        self.assertIn('otel.exporter="none"', command)
        self.assertIn('otel.metrics_exporter="none"', command)
        self.assertIn('otel.trace_exporter="none"', command)
        self.assertIn("otel.log_user_prompt=false", command)
        self.assertNotIn("openai_base_url=null", command)
        self.assertNotIn("chatgpt_base_url=null", command)

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
        self.assertTrue(all(params["config"]["web_search"] == "live" for params in thread_params))
        self.assertIn("外部状態は変更しない", thread_params[1]["developerInstructions"])
        turn_params = [params for method, params in client.calls if method == "turn/start"]
        self.assertEqual(turn_params[0]["sandboxPolicy"]["type"], "readOnly")
        self.assertEqual(turn_params[1]["sandboxPolicy"]["type"], "workspaceWrite")
        self.assertTrue(all(params["sandboxPolicy"]["networkAccess"] is False for params in turn_params))
        self.assertTrue(all(params["serviceTier"] is None for params in turn_params))
        self.assertTrue(all(params["effort"] == "high" for params in turn_params))

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
