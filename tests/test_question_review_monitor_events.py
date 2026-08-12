import fcntl
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.monitor_events import (
    DISK_FAILURE_CATEGORIES,
    DiskPersistenceError,
    MAX_DISK_BATCH_SIZE,
    MAX_MONITOR_BINDINGS,
    MAX_PENDING_GAP_SEGMENTS,
    MonitorEventHub,
    MonitorEventStore,
)


def bind_store(store, thread_id="thread-1", **context):
    store.bind(
        thread_id=thread_id,
        session_id="session-1",
        turn_id="turn-1",
        context=context,
    )


class MonitorEventStoreTests(unittest.TestCase):
    def test_lossless_input_coalescing_is_reported_without_degrading_health(self):
        store = MonitorEventStore(start_worker=False)

        store.record_lossless_coalescing(
            12,
            method="item/agentMessage/delta",
            queue_capacity=4096,
            queue_peak=101,
        )
        store.record_lossless_coalescing(
            7,
            method="item/reasoning/summaryTextDelta",
        )

        health = store.health()["observationHealth"]
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["droppedNotifications"], 0)
        self.assertEqual(
            health["inputCoalescing"],
            {
                "queueCapacity": 4096,
                "queuePeak": 101,
                "coalescedNotifications": 19,
                "coalescedByMethod": {
                    "item/agentMessage/delta": 12,
                    "item/reasoning/summaryTextDelta": 7,
                },
            },
        )
        self.assertEqual(
            store.snapshot()["observation"]["inputCoalescing"],
            health["inputCoalescing"],
        )
        store.close()

    def test_100_stream_disk_coalescing_is_bounded_auditable_and_terminal_ordered(self):
        class PausedDiskHub(MonitorEventHub):
            disk_gate = threading.Event()

            def _run_disk_writer(self):
                self.disk_gate.wait(timeout=60)
                return super()._run_disk_writer()

        with tempfile.TemporaryDirectory() as directory:
            hub = PausedDiskHub(Path(directory), queue_capacity=4096)
            hub.bind_runtime({"qualification": "sample", "runId": "run"}, "thread", "turn")
            for delta_index in range(300):
                for stream_index in range(100):
                    hub.observe({"method": "item/agentMessage/delta", "params": {
                        "threadId": "thread", "turnId": "turn",
                        "itemId": f"item-{stream_index}", "delta": str(delta_index % 10),
                    }})
                hub.process_pending_for_test()
            hub.observe({"method": "turn/completed", "params": {
                "threadId": "thread", "turnId": "turn",
            }})
            hub.process_pending_for_test()
            hub.disk_gate.set()
            hub.drain(timeout=15)
            hub.close(timeout=15)

            path = Path(directory) / "output/question_review_console/runtime_observations/sample/run/events.jsonl"
            events = [json.loads(line) for line in path.read_text().splitlines()]
            streams = [event for event in events if event["type"] == "agentMessage"]
            self.assertEqual(len(streams), 100)
            self.assertEqual(events[-1]["type"], "turnState")
            for event in streams:
                self.assertEqual(event["payload"]["text"], "0123456789" * 30)
                self.assertEqual(event["diskCoalescing"]["coalescedCount"], 299)
                self.assertLess(*event["diskCoalescing"]["sequenceRange"])
            telemetry = hub.health()["observationHealth"]["diskTelemetry"]
            self.assertEqual(telemetry["coalescedEvents"], 29_900)
            self.assertEqual(telemetry["pendingStreams"], 0)
            self.assertLess(telemetry["queuePeak"], 4096)
            health = hub.health()["observationHealth"]
            self.assertEqual(health["diskFailures"], 0)
            self.assertEqual(health["droppedNotifications"], 0)

    def test_300_producer_disk_batches_are_bounded_ordered_and_durable(self):
        class RecordingHub(MonitorEventHub):
            batch_sizes = []

            def _write_disk_batch(self, events):
                self.batch_sizes.append(len(events))
                return super()._write_disk_batch(events)

        with tempfile.TemporaryDirectory() as directory:
            hub = RecordingHub(Path(directory), queue_capacity=4096)
            for route in range(3):
                hub.bind_runtime(
                    {"qualification": f"sample-{route}", "runId": "run"},
                    f"thread-{route}",
                    "turn",
                )
            barrier = threading.Barrier(300)

            def produce(index):
                barrier.wait()
                hub.observe(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": f"thread-{index % 3}",
                            "turnId": "turn",
                            "itemId": f"item-{index}",
                            "delta": str(index),
                        },
                    }
                )

            with ThreadPoolExecutor(max_workers=300) as executor:
                list(executor.map(produce, range(300)))
            hub.drain(timeout=10)
            hub.close(timeout=10)

            self.assertTrue(hub.batch_sizes)
            self.assertLessEqual(max(hub.batch_sizes), MAX_DISK_BATCH_SIZE)
            observation_root = (
                Path(directory)
                / "output/question_review_console/runtime_observations"
            )
            events = []
            for route in range(3):
                path = observation_root / f"sample-{route}/run/events.jsonl"
                route_events = [
                    json.loads(line) for line in path.read_text().splitlines()
                ]
                self.assertEqual(len(route_events), 100)
                self.assertEqual(
                    [event["sequence"] for event in route_events],
                    sorted(event["sequence"] for event in route_events),
                )
                snapshot = json.loads(path.with_name("snapshot.json").read_text())
                self.assertIn("diskTelemetry", snapshot["observation"])
                events.extend(route_events)
            sequences = [event["sequence"] for event in events]
            self.assertEqual(len(events), 300)
            health = hub.health()["observationHealth"]
            telemetry = health["diskTelemetry"]
            self.assertEqual(health["diskFailures"], 0)
            self.assertEqual(health["droppedNotifications"], 0)
            self.assertLess(telemetry["queuePeak"], 4096)
            self.assertLessEqual(telemetry["lastBatchSize"], MAX_DISK_BATCH_SIZE)
            self.assertGreaterEqual(telemetry["lastBatchDurationMs"], 0)
            self.assertGreaterEqual(telemetry["lastLockHoldMs"], 0)

    def test_disk_failure_categories_are_bounded_and_distinct(self):
        sample = {
            "schemaVersion": "monitor-event/v1",
            "eventId": "server:7",
            "sequence": 7,
            "correlation": {"qualification": "sample", "runId": "run"},
        }
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorEventHub(Path(directory), queue_capacity=1)
            for category in DISK_FAILURE_CATEGORIES:
                error = DiskPersistenceError(category, f"injected {category}")
                hub._mark_disk_failure(sample, category, error)
            telemetry = hub.health()["observationHealth"]["diskTelemetry"]
            self.assertEqual(
                set(telemetry["failureCategories"]),
                set(DISK_FAILURE_CATEGORIES),
            )
            for category in DISK_FAILURE_CATEGORIES:
                failure = telemetry["failureCategories"][category]
                self.assertEqual(failure["count"], 1)
                self.assertEqual(failure["last"]["sequence"], 7)
                self.assertEqual(failure["last"]["route"], "sample/run")
                self.assertEqual(
                    set(failure["last"]),
                    {"errno", "time", "sequence", "route"},
                )
            self.assertEqual(len(telemetry["failureCategories"]), 7)
            hub.close()

    def test_delta_events_are_append_only_and_publish_redacted_replacements(self):
        store = MonitorEventStore(start_worker=False, server_instance_id="server")
        bind_store(store, runId="run-1", questionId="question-1")
        store.observe(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "delta": "公",
                    "rawReasoning": "secret",
                },
            }
        )
        store.process_pending_for_test()
        first = store.replay()

        store.observe(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "delta": "開",
                    "developerInstructions": "secret",
                },
            }
        )
        store.process_pending_for_test()
        delta = store.replay(first["cursor"])

        self.assertEqual(
            [event["payload"]["text"] for event in first["events"]],
            ["公"],
        )
        self.assertEqual(
            [event["payload"]["text"] for event in delta["events"]],
            ["公開"],
        )
        self.assertEqual(first["events"][0]["eventId"], "server:1")
        self.assertEqual(delta["events"][0]["eventId"], "server:2")
        self.assertEqual(delta["events"][0]["correlation"]["questionId"], "question-1")
        self.assertNotIn("secret", str(first) + str(delta))
        self.assertEqual(delta["monitorModelRequests"], 0)

    def test_notification_before_bind_is_held_then_flushed_with_full_context(self):
        store = MonitorEventStore(start_worker=False, server_instance_id="server")
        store.observe(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "inProgress"},
                },
            }
        )
        store.process_pending_for_test()
        self.assertEqual(store.replay()["events"], [])

        store.bind(
            thread_id="thread-1",
            session_id="session-1",
            turn_id="turn-1",
            context={"qualification": "sample", "runId": "run-1"},
        )

        event = store.replay()["events"][0]
        self.assertEqual(event["correlation"]["qualification"], "sample")
        self.assertEqual(event["correlation"]["runId"], "run-1")
        self.assertEqual(event["correlation"]["sessionId"], "session-1")

    def test_prebind_event_preserves_original_receive_timestamp(self):
        store = MonitorEventStore(start_worker=False, server_instance_id="server")
        store.put_observed_nowait(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "inProgress"},
                },
            },
            123.25,
        )
        store.process_pending_for_test()
        bind_store(store, runId="run-1")

        self.assertEqual(store.replay()["events"][0]["observedAt"], 123.25)

    def test_tool_projection_contains_state_but_not_args_stdout_or_absolute_path(self):
        store = MonitorEventStore(start_worker=False)
        bind_store(store)
        store.observe(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "id": "item",
                        "type": "commandExecution",
                        "status": "failed",
                        "command": "cat /Users/person/private",
                        "arguments": {"token": "secret"},
                        "aggregatedOutput": "raw stdout",
                    },
                },
            }
        )
        store.process_pending_for_test()

        event = store.replay()["events"][0]
        self.assertEqual(
            event["payload"],
            {"toolType": "commandExecution", "state": "failed"},
        )
        self.assertNotIn("/Users/person", json.dumps(event))
        self.assertNotIn("raw stdout", json.dumps(event))

    def test_text_projection_strictly_redacts_secrets_jwt_private_key_and_paths(self):
        store = MonitorEventStore(start_worker=False)
        bind_store(store)
        secret_text = (
            "password=hunter2 api_key='key-value' "
            '"OPENAI_API_KEY": "env-key" Cookie: session=abc\n'
            "Q₂/Q₁ Cu2+/Cu /h /600=40kPa "
            "/Q₂/Q₁ /kg/m³ 比は /x/y とする f(x)=/a/b "
            "Authorization: Basic dXNlcjpwYXNz\n"
            "Authorization = Bearer equal-secret-value\n"
            "github_pat_abcdefghijklmnop "
            "Bearer bearer-value "
            "xoxb-1234567890-secret glpat-abcdefghijklmnop "
            "AIzaSyExampleGoogleApiKey123456789 "
            "https://operator:super-secret@example.test/path "
            "postgresql://dbuser:db-secret@example.test/app "
            "redis://:redis-secret@example.test/0 "
            "ftp://ftpuser:ftp-secret@example.test/file "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c "
            "cwd:/tmp/private file:///var/private C:\\Users\\person\\private "
            "-----BEGIN PRIVATE " "KEY-----\nprivate-material\n"
            "-----END PRIVATE " "KEY-----"
        )
        for method, extra in (
            ("item/agentMessage/delta", {"delta": secret_text}),
            ("item/reasoning/summaryTextDelta", {"delta": secret_text}),
            ("item/plan/delta", {"delta": secret_text}),
            (
                "error",
                {
                    "error": {
                        "message": secret_text,
                        "additionalDetails": "must never be copied",
                    },
                    "willRetry": True,
                },
            ),
        ):
            store.observe(
                {
                    "method": method,
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "item-1",
                        **extra,
                    },
                }
            )
        store.process_pending_for_test()

        serialized = json.dumps(store.replay(), ensure_ascii=False)
        for forbidden in (
            "hunter2",
            "key-value",
            "env-key",
            "bearer-value",
            "xoxb-1234567890-secret",
            "glpat-abcdefghijklmnop",
            "AIzaSyExampleGoogleApiKey123456789",
            "operator:super-secret",
            "dbuser:db-secret",
            ":redis-secret",
            "ftpuser:ftp-secret",
            "eyJhbGci",
            "private-material",
            "dXNlcjpwYXNz",
            "equal-secret-value",
            "github_pat_",
            "/tmp/private",
            "/var/private",
            "C:\\\\Users",
            "must never be copied",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("<redacted", serialized)
        self.assertIn("<absolute-path>", serialized)
        for formula in (
            "Q₂/Q₁",
            "Cu2+/Cu",
            "/h",
            "/600=40kPa",
            "/Q₂/Q₁",
            "/kg/m³",
            "/x/y",
            "/a/b",
        ):
            self.assertIn(formula, serialized)

    def test_split_delta_secrets_are_never_reconstructable_from_public_events(self):
        store = MonitorEventStore(
            start_worker=False,
            server_instance_id="server",
        )
        bind_store(store)
        fragments = (
            "公開文 sk-ABC",
            "DEFGHIJK",
            "\n-----BEGIN PRI",
            "VATE KEY-----\nprivate-material",
            "\n-----END PRIVATE KEY-----",
        )
        for fragment in fragments:
            store.observe(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "item-1",
                        "delta": fragment,
                    },
                }
            )
        store.process_pending_for_test()

        events = store.replay()["events"]
        public_texts = [event["payload"]["text"] for event in events]
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertEqual(public_texts[0], "公開文 sk-ABC")
        self.assertIn("<redacted>", public_texts[1])
        self.assertNotIn("DEFGHIJK", serialized)
        self.assertNotIn("private-material", serialized)
        self.assertNotIn("-----END PRIVATE KEY-----", serialized)

    def test_generic_authorization_and_cookie_headers_are_fully_redacted(self):
        store = MonitorEventStore(start_worker=False)
        bind_store(store)
        text = (
            "Authorization: ApiKey opaqueCredential123456\n数式 /Q₂/Q₁\n"
            "Authorization: AWS4-HMAC-SHA256 "
            "Credential=AKIAEXAMPLE/20260727/ap-northeast-1/service/request, "
            "SignedHeaders=host;x-amz-date, Signature=abcdef123456\n"
            "Cookie: session=abc; refresh=def\n数式 /kg/m³"
        )
        store.observe(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "delta": text,
                },
            }
        )
        store.process_pending_for_test()

        public = store.replay()["events"][0]["payload"]["text"]
        for secret in (
            "opaqueCredential123456",
            "AKIAEXAMPLE",
            "abcdef123456",
            "session=abc",
            "refresh=def",
        ):
            self.assertNotIn(secret, public)
        self.assertIn("/Q₂/Q₁", public)
        self.assertIn("/kg/m³", public)

    def test_custom_authorization_payloads_redact_through_end_of_line(self):
        store = MonitorEventStore(start_worker=False)
        bind_store(store)
        store.observe(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "delta": (
                        'Authorization: Custom first {"abc":"SECRET"}\n'
                        "Proxy-Authorization=[opaque SECOND]\n"
                        "数式 /Q₂/Q₁"
                    ),
                },
            }
        )
        store.process_pending_for_test()

        public = store.replay()["events"][0]["payload"]["text"]
        self.assertNotIn("SECRET", public)
        self.assertNotIn("SECOND", public)
        self.assertEqual(public.count("<redacted>"), 2)
        self.assertIn("/Q₂/Q₁", public)

    def test_long_public_text_is_linear_and_event_payload_is_bounded(self):
        store = MonitorEventStore(start_worker=False)
        bind_store(store)
        started = time.monotonic()
        store.observe(
            {
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "plan": [
                        {
                            "step": ("X" * 100_000) + " api_key=end-secret",
                            "status": "inProgress",
                        }
                        for _index in range(200)
                    ],
                },
            }
        )
        store.process_pending_for_test()
        elapsed = time.monotonic() - started

        event = store.replay()["events"][0]
        self.assertLess(elapsed, 2.0)
        self.assertEqual(len(event["payload"]["plan"]), 64)
        self.assertTrue(
            all(
                len(item["step"]) <= 4096
                for item in event["payload"]["plan"]
            )
        )
        self.assertLess(
            len(json.dumps(event, ensure_ascii=False).encode("utf-8")),
            300_000,
        )
        self.assertNotIn("end-secret", json.dumps(event))

    def test_current_token_usage_last_total_shape_is_preserved(self):
        store = MonitorEventStore(start_worker=False)
        bind_store(store)
        store.observe(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "tokenUsage": {
                        "last": {
                            "inputTokens": 10,
                            "cachedInputTokens": 4,
                            "cacheWriteInputTokens": 2,
                            "outputTokens": 3,
                            "reasoningOutputTokens": 1,
                            "totalTokens": 13,
                            "secret": 999,
                        },
                        "total": {
                            "inputTokens": 100,
                            "cachedInputTokens": 40,
                            "outputTokens": 30,
                            "reasoningOutputTokens": 10,
                            "totalTokens": 130,
                        },
                        "modelContextWindow": 200000,
                    },
                },
            }
        )
        store.process_pending_for_test()

        usage = store.replay()["events"][0]["payload"]["usage"]
        self.assertEqual(usage["last"]["inputTokens"], 10)
        self.assertEqual(usage["total"]["totalTokens"], 130)
        self.assertEqual(usage["modelContextWindow"], 200000)
        self.assertNotIn("secret", str(usage))

    def test_exact_turn_correlation_and_official_event_time_are_preserved(self):
        store = MonitorEventStore(
            start_worker=False,
            server_instance_id="server",
        )
        bind_store(store, runId="run-1")
        store.observe(
            {
                "method": "thread/status/changed",
                "emittedAtMs": 1_785_110_499_000,
                "params": {
                    "threadId": "thread-1",
                    "status": {"type": "active"},
                    "timestamp": "2026-07-27T00:00:01Z",
                },
            }
        )
        store.observe(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "exact-turn",
                        "status": "completed",
                        "completedAtMs": 1_785_110_402_000,
                    },
                },
            }
        )
        store.observe(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "item": {
                        "id": "item-1",
                        "turnId": "item-turn",
                        "type": "commandExecution",
                        "status": "completed",
                        "completedAtMs": 1_785_110_403_000,
                    },
                },
            }
        )
        store.process_pending_for_test()

        thread_event, turn_event, item_event = store.replay()["events"]
        self.assertNotIn("turnId", thread_event["correlation"])
        self.assertNotIn("occurredAt", thread_event)
        self.assertEqual(turn_event["correlation"]["turnId"], "exact-turn")
        self.assertEqual(turn_event["occurredAt"], 1_785_110_402_000)
        self.assertEqual(item_event["correlation"]["turnId"], "item-turn")
        self.assertEqual(item_event["occurredAt"], 1_785_110_403_000)
        for event in (thread_event, turn_event, item_event):
            self.assertIn("observedAt", event)

    def test_public_summary_plan_thread_lifecycle_and_error_are_allowlisted(self):
        store = MonitorEventStore(start_worker=False)
        bind_store(store)
        notifications = [
            {
                "method": "item/reasoning/summaryPartAdded",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "reasoning",
                    "summaryIndex": 2,
                    "content": "raw reasoning",
                },
            },
            {
                "method": "turn/plan/updated",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "explanation": "公開計画",
                    "plan": [
                        {"step": "確認", "status": "inProgress", "prompt": "private"}
                    ],
                },
            },
            {
                "method": "thread/status/changed",
                "params": {
                    "threadId": "thread-1",
                    "status": {
                        "type": "active",
                        "activeFlags": ["waitingOnUserInput", "unknown"],
                    },
                },
            },
            {
                "method": "error",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "error": {"message": "公開エラー", "additionalDetails": "private"},
                    "willRetry": False,
                },
            },
        ]
        for notification in notifications:
            store.observe(notification)
        store.process_pending_for_test()

        events = store.replay()["events"]
        self.assertEqual(
            [event["type"] for event in events],
            ["reasoningSummaryPart", "plan", "threadState", "error"],
        )
        self.assertEqual(events[0]["payload"], {"summaryIndex": 2})
        self.assertEqual(
            events[1]["payload"]["plan"],
            [{"step": "確認", "status": "inProgress"}],
        )
        self.assertEqual(events[2]["payload"]["activeFlags"], ["waitingOnUserInput"])
        self.assertNotIn("raw reasoning", str(events))
        self.assertNotIn("private", str(events))

    def test_all_stable_work_identity_fields_are_safely_correlated(self):
        store = MonitorEventStore(start_worker=False)
        bind_store(
            store,
            qualification="sample",
            runId="run",
            parentRunId="parent",
            childRunId="child",
            questionId="q1",
            questionIds=["q1", "/private/q2"],
            workItemKey="w1",
            workItemKeys=["w1", "w2"],
            stageId="03",
            listGroupIds=["2025", "2026"],
            workType="evaluation",
            phase="review",
        )
        store.observe(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )
        store.process_pending_for_test()

        correlation = store.replay()["events"][0]["correlation"]
        self.assertEqual(correlation["questionIds"][0], "q1")
        self.assertEqual(correlation["questionIds"][1], "<absolute-path>")
        self.assertEqual(correlation["workItemKeys"], ["w1", "w2"])
        self.assertEqual(correlation["stageId"], "03")
        self.assertEqual(correlation["listGroupIds"], ["2025", "2026"])
        self.assertEqual(correlation["workType"], "evaluation")
        self.assertEqual(correlation["phase"], "review")

    def test_old_cursor_reports_observation_gap(self):
        store = MonitorEventStore(
            start_worker=False, replay_capacity=1, server_instance_id="server"
        )
        bind_store(store)
        for state in ("started", "completed"):
            store.observe(
                {
                    "method": f"turn/{state}",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": state},
                    },
                }
            )
        store.process_pending_for_test()

        result = store.replay("server:0")

        self.assertEqual(result["events"][0]["type"], "observationGap")
        self.assertEqual(result["events"][1]["type"], "turnState")

    def test_queue_overflow_and_disk_failure_do_not_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            unusable_path = Path(directory)
            store = MonitorEventStore(
                unusable_path,
                start_worker=False,
                queue_capacity=1,
            )
            bind_store(store)
            notification = {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "inProgress"},
                },
            }
            store.put_nowait(notification)
            store.put_nowait(notification)
            store.process_pending_for_test()

            observation = store.replay()["observation"]
            self.assertEqual(observation["droppedNotifications"], 1)
            self.assertEqual(observation["diskFailures"], 1)
            self.assertEqual(
                [
                    event["type"]
                    for event in store.replay()["events"]
                ],
                ["turnState", "observationGap"],
            )

    def test_upstream_gap_stays_between_prior_and_later_notifications(self):
        store = MonitorEventStore(
            start_worker=False,
            queue_capacity=4,
            server_instance_id="server",
        )
        bind_store(store)
        notification = {
            "method": "turn/started",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "inProgress"},
            },
        }
        store.put_nowait(notification)
        store.put_nowait(notification)
        store.record_observation_gap(2)
        store.put_nowait(notification)

        store.process_pending_for_test()

        events = store.replay()["events"]
        self.assertEqual(
            [event["type"] for event in events],
            ["turnState", "turnState", "observationGap", "turnState"],
        )
        self.assertEqual(events[2]["payload"]["droppedNotifications"], 2)

    def test_gap_routes_are_frozen_before_a_later_run_becomes_active(self):
        store = MonitorEventStore(
            start_worker=False,
            server_instance_id="server",
        )
        bind_store(
            store,
            thread_id="thread-a",
            qualification="sample",
            runId="run-a",
        )
        store.observe(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-a",
                    "turn": {"id": "turn-a", "status": "inProgress"},
                },
            }
        )
        store.process_pending_for_test()
        store._record_drop(
            count=1,
            boundary=2,
            affected_routes=store.observation_routes_snapshot(),
        )

        bind_store(
            store,
            thread_id="thread-b",
            qualification="sample",
            runId="run-b",
        )
        store.observe(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-b",
                    "turn": {"id": "turn-b", "status": "inProgress"},
                },
            }
        )
        store.process_pending_for_test()

        self.assertEqual(
            [
                event["type"]
                for event in store._run_events[("sample", "run-a")]
            ],
            ["turnState", "observationGap"],
        )
        self.assertEqual(
            [
                event["type"]
                for event in store._run_events[("sample", "run-b")]
            ],
            ["turnState"],
        )

    def test_scope_truncation_remains_fail_closed_after_gap_replay_eviction(self):
        store = MonitorEventStore(
            start_worker=False,
            replay_capacity=1,
            server_instance_id="server",
        )
        bind_store(
            store,
            qualification="sample",
            runId="current-run",
        )
        store.record_observation_gap(
            3,
            affected_routes=(),
            scope_truncated=True,
        )

        truncated = store.replay()["events"][0]
        self.assertEqual(truncated["correlation"], {})
        self.assertEqual(
            truncated["payload"]["droppedNotifications"],
            3,
        )
        self.assertEqual(
            truncated["payload"]["totalDroppedNotifications"],
            3,
        )
        self.assertTrue(truncated["payload"]["scopeTruncated"])

        store.observe(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-1",
                        "status": "inProgress",
                    },
                },
            }
        )
        store.process_pending_for_test()
        self.assertEqual(
            [event["type"] for event in store.replay()["events"]],
            ["turnState"],
        )

        old_run_health = store.health("sample", "old-run")[
            "observationHealth"
        ]
        self.assertEqual(old_run_health["droppedNotifications"], 0)
        self.assertTrue(old_run_health["scopeTruncated"])
        self.assertEqual(old_run_health["scopeTruncatedDrops"], 3)
        self.assertEqual(old_run_health["status"], "degraded")

    def test_parent_and_64_children_share_one_gap_event_and_disk_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MonitorEventHub(root)
            for index in range(64):
                thread_id = f"thread-{index}"
                hub.bind_runtime(
                    {
                        "qualification": "sample",
                        "runId": f"child-{index}",
                        "parentRunId": "parent",
                        "childRunId": f"child-{index}",
                    },
                    thread_id,
                    f"turn-{index}",
                )
                hub.observe(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": thread_id,
                            "turn": {
                                "id": f"turn-{index}",
                                "status": "inProgress",
                            },
                        },
                    }
                )
            hub.drain()
            hub.record_observation_gap(1)
            hub.drain()

            gaps = [
                event
                for event in hub.snapshot()["events"]
                if event["type"] == "observationGap"
            ]
            self.assertEqual(len(gaps), 1)
            self.assertEqual(
                len(gaps[0]["correlation"]["affectedRunIds"]),
                65,
            )
            for run_id in ("parent", "child-0", "child-63"):
                self.assertEqual(
                    [
                        event["type"]
                        for event in hub.events("sample", run_id)["events"]
                        if event["type"] == "observationGap"
                    ],
                    ["observationGap"],
                )
            observation_root = (
                root
                / "output/question_review_console/runtime_observations/sample"
            )
            self.assertEqual(
                sorted(path.name for path in observation_root.iterdir()),
                ["parent"],
            )
            hub.close()

    def test_runtime_binding_persists_and_rejects_unsafe_disk_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MonitorEventHub(root)
            hub.bind_runtime(
                {
                    "qualification": "sample",
                    "runId": "child",
                    "parentRunId": "parent",
                    "questionId": "q1",
                },
                "thread",
                "turn",
            )
            hub.observe(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn", "status": "completed"},
                    },
                }
            )
            hub.bind_runtime(
                {"qualification": "..", "runId": "unsafe"},
                "unsafe-thread",
                "unsafe-turn",
            )
            hub.observe(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "unsafe-thread",
                        "turn": {"id": "unsafe-turn", "status": "completed"},
                    },
                }
            )
            hub.drain()

            projection = (
                root
                / "output/question_review_console/runtime_observations"
                / "sample/parent"
            )
            self.assertTrue((projection / "events.jsonl").is_file())
            self.assertTrue((projection / "snapshot.json").is_file())
            self.assertFalse(
                (
                    root
                    / "output/question_review_console/runtime_observations/unsafe"
                ).exists()
            )
            events = hub.events("sample", "parent")
            self.assertEqual(events["events"][0]["correlation"]["questionId"], "q1")
            self.assertEqual(events["monitorModelRequests"], 0)
            self.assertEqual(
                events["observation"]["diskFailures"],
                0,
            )
            self.assertEqual(
                hub.events("..", "unsafe")["observation"]["diskFailures"],
                1,
            )
            self.assertEqual(
                hub.health()["observationHealth"]["diskFailures"],
                1,
            )
            hub.close()

    def test_runtime_event_log_rotates_with_fixed_bounded_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "tools.question_review_console.monitor_events."
                "MAX_RUN_EVENT_LOG_BYTES",
                1200,
            ):
                hub = MonitorEventHub(root)
                hub.bind_runtime(
                    {"qualification": "sample", "runId": "run"},
                    "thread",
                    "turn",
                )
                for index in range(12):
                    hub.observe(
                        {
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": "thread",
                                "turnId": "turn",
                                "itemId": f"item-{index}",
                                "delta": f"{index}-" + ("x" * 300),
                            },
                        }
                    )
                hub.drain()

                projection = (
                    root
                    / "output/question_review_console/"
                    "runtime_observations/sample/run"
                )
                current = projection / "events.jsonl"
                backup = projection / "events.jsonl.1"
                self.assertTrue(current.is_file())
                self.assertTrue(backup.is_file())
                self.assertLessEqual(
                    current.stat().st_size + backup.stat().st_size,
                    2400,
                )
                self.assertFalse((projection / "events.jsonl.2").exists())
                for path in (current, backup):
                    for line in path.read_text(encoding="utf-8").splitlines():
                        json.loads(line)
                self.assertEqual(
                    len(hub.events("sample", "run", limit=100)["events"]),
                    12,
                )
                self.assertEqual(
                    hub.health("sample", "run")["observationHealth"][
                        "diskFailures"
                    ],
                    0,
                )
                hub.close()

    def test_rotation_failure_is_observation_failure_not_run_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            projection = (
                root
                / "output/question_review_console/"
                "runtime_observations/sample/run"
            )
            projection.mkdir(parents=True)
            (projection / "events.jsonl").write_bytes(b"x" * 1000)
            hub = MonitorEventHub(root)
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )
            with (
                patch(
                    "tools.question_review_console.monitor_events."
                    "MAX_RUN_EVENT_LOG_BYTES",
                    1024,
                ),
                patch(
                    "tools.question_review_console.monitor_events.os.replace",
                    side_effect=OSError("rotation failed"),
                ),
            ):
                hub.observe(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread",
                            "turn": {
                                "id": "turn",
                                "status": "inProgress",
                            },
                        },
                    }
                )
                hub.drain()

            events = hub.events("sample", "run")
            health = hub.health("sample", "run")["observationHealth"]
            self.assertEqual(len(events["events"]), 1)
            self.assertEqual(events["events"][0]["type"], "turnState")
            self.assertEqual(health["diskFailures"], 1)
            self.assertEqual(health["status"], "degraded")
            self.assertEqual(events["monitorModelRequests"], 0)
            hub.close()

    def test_event_log_hardlink_is_rejected_without_writing_external_inode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside-events.jsonl"
            outside.write_text("external-content\n", encoding="utf-8")
            projection = (
                root
                / "output/question_review_console/"
                "runtime_observations/sample/run"
            )
            projection.mkdir(parents=True)
            (projection / "events.jsonl").hardlink_to(outside)

            hub = MonitorEventHub(root)
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )
            hub.observe(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn", "status": "inProgress"},
                    },
                }
            )
            hub.drain()

            self.assertEqual(
                outside.read_text(encoding="utf-8"),
                "external-content\n",
            )
            health = hub.health("sample", "run")["observationHealth"]
            self.assertEqual(health["eventCount"], 1)
            self.assertEqual(health["diskFailures"], 1)
            hub.close()

    def test_global_runtime_observation_run_count_prunes_oldest_safe_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch(
                    "tools.question_review_console.monitor_events."
                    "MAX_OBSERVATION_RUNS",
                    2,
                ),
                patch(
                    "tools.question_review_console.monitor_events."
                    "MAX_RUN_EVENT_LOG_BYTES",
                    2048,
                ),
            ):
                hub = MonitorEventHub(root)
                for index in range(3):
                    thread_id = f"thread-{index}"
                    hub.bind_runtime(
                        {
                            "qualification": "sample",
                            "runId": f"run-{index}",
                        },
                        thread_id,
                        f"turn-{index}",
                    )
                    hub.observe(
                        {
                            "method": "turn/started",
                            "params": {
                                "threadId": thread_id,
                                "turn": {
                                    "id": f"turn-{index}",
                                    "status": "inProgress",
                                },
                            },
                        }
                    )
                    hub.drain()
                    time.sleep(0.002)

                observation_root = (
                    root
                    / "output/question_review_console/runtime_observations"
                )
                run_directories = sorted(
                    path
                    for qualification in observation_root.iterdir()
                    if qualification.is_dir()
                    for path in qualification.iterdir()
                    if path.is_dir()
                )
                self.assertEqual(
                    [path.name for path in run_directories],
                    ["run-1", "run-2"],
                )
                self.assertTrue(
                    (observation_root / "sample/run-2").is_dir()
                )
                retained_bytes = sum(
                    file.stat().st_size
                    for run_directory in run_directories
                    for file in run_directory.iterdir()
                    if file.is_file()
                )
                per_run_bound = (2 * 2048) + (2 * 16 * 1024)
                self.assertLessEqual(retained_bytes, 2 * per_run_bound)
                self.assertEqual(
                    hub.health("sample", "run-2")["observationHealth"][
                        "diskFailures"
                    ],
                    0,
                )
                hub.close()

    def test_two_hubs_create_lock_and_persist_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hubs = [MonitorEventHub(root), MonitorEventHub(root)]
            barrier = threading.Barrier(2)

            def publish(index):
                hub = hubs[index]
                hub.bind_runtime(
                    {
                        "qualification": "sample",
                        "runId": f"run-{index}",
                    },
                    f"thread-{index}",
                    f"turn-{index}",
                )
                barrier.wait()
                hub.observe(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": f"thread-{index}",
                            "turn": {
                                "id": f"turn-{index}",
                                "status": "inProgress",
                            },
                        },
                    }
                )
                hub.drain()
                return hub.health("sample", f"run-{index}")

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(publish, range(2)))

            self.assertEqual(
                [
                    result["observationHealth"]["diskFailures"]
                    for result in results
                ],
                [0, 0],
            )
            for index in range(2):
                path = (
                    root
                    / "output/question_review_console/runtime_observations"
                    / "sample"
                    / f"run-{index}"
                    / "events.jsonl"
                )
                self.assertTrue(path.is_file())
            for hub in hubs:
                hub.close()

    def test_external_disk_lock_never_blocks_health_or_event_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation_root = (
                root
                / "output/question_review_console/runtime_observations"
            )
            observation_root.mkdir(parents=True)
            lock_path = observation_root / ".observation.lock"
            lock_path.touch(mode=0o600)
            lock_fd = os.open(lock_path, os.O_RDWR)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                hub = MonitorEventHub(root)
                hub.bind_runtime(
                    {"qualification": "sample", "runId": "run"},
                    "thread",
                    "turn",
                )
                hub.observe(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread",
                            "turn": {
                                "id": "turn",
                                "status": "inProgress",
                            },
                        },
                    }
                )
                hub._queue.join()

                started = time.monotonic()
                health = hub.health("sample", "run")
                elapsed = time.monotonic() - started

                self.assertLess(elapsed, 0.05)
                self.assertEqual(
                    health["observationHealth"]["eventCount"],
                    1,
                )
                hub.drain()
                self.assertEqual(
                    hub.health("sample", "run")["observationHealth"][
                        "diskFailures"
                    ],
                    1,
                )
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                hub.close()

    def test_partial_event_write_is_rolled_back_before_next_append(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MonitorEventHub(root)
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )
            original_write = os.write
            failed = False

            def fail_once(fd, data):
                nonlocal failed
                raw = bytes(data)
                if (
                    not failed
                    and b'"schemaVersion":"monitor-event/v1"' in raw
                    and raw.endswith(b"\n")
                ):
                    failed = True
                    original_write(fd, raw[: max(1, len(raw) // 2)])
                    raise OSError("injected partial append")
                return original_write(fd, data)

            with patch(
                "tools.question_review_console.monitor_events.os.write",
                side_effect=fail_once,
            ):
                for turn_id in ("turn-1", "turn-2"):
                    hub.observe(
                        {
                            "method": "turn/started",
                            "params": {
                                "threadId": "thread",
                                "turn": {
                                    "id": turn_id,
                                    "status": "inProgress",
                                },
                            },
                        }
                    )
                    hub.drain()

            path = (
                root
                / "output/question_review_console/runtime_observations"
                / "sample/run/events.jsonl"
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["type"], "turnState")
            self.assertEqual(
                hub.health("sample", "run")["observationHealth"][
                    "diskFailures"
                ],
                1,
            )
            hub.close()

    def test_close_waits_until_accepted_projection_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MonitorEventHub(root)
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )
            entered = threading.Event()
            release = threading.Event()
            original_process = hub._process

            def blocked_process(*args, **kwargs):
                entered.set()
                release.wait()
                return original_process(*args, **kwargs)

            hub._process = blocked_process
            hub.observe(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn", "status": "inProgress"},
                    },
                }
            )
            self.assertTrue(entered.wait(timeout=1))
            closer = threading.Thread(target=hub.close)
            closer.start()
            time.sleep(1.05)
            self.assertTrue(closer.is_alive())
            release.set()
            closer.join(timeout=2)

            self.assertFalse(closer.is_alive())
            self.assertTrue(
                (
                    root
                    / "output/question_review_console/runtime_observations"
                    / "sample/run/events.jsonl"
                ).is_file()
            )

    def test_drain_reports_disk_worker_failure_without_waiting_forever(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MonitorEventHub(root)
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )

            def stop_disk_worker(_event):
                raise KeyboardInterrupt("simulated disk worker failure")

            hub._write_disk_event = stop_disk_worker
            hub.observe(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn", "status": "inProgress"},
                    },
                }
            )

            started = time.monotonic()
            with self.assertRaisesRegex(
                RuntimeError,
                "disk worker stopped",
            ):
                hub.drain(timeout=1)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertFalse(
                (
                    root
                    / "output/question_review_console/runtime_observations"
                    / "sample/run/events.jsonl"
                ).exists()
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "disk worker stopped",
            ):
                hub.close(timeout=1)

    def test_close_timeout_is_bounded_and_retry_preserves_accepted_event(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MonitorEventHub(root)
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )
            entered = threading.Event()
            release = threading.Event()
            original_write = hub._write_disk_event

            def blocked_write(event):
                entered.set()
                release.wait()
                return original_write(event)

            hub._write_disk_event = blocked_write
            hub.observe(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn", "status": "inProgress"},
                    },
                }
            )
            self.assertTrue(entered.wait(timeout=1))

            started = time.monotonic()
            try:
                with self.assertRaisesRegex(
                    TimeoutError,
                    "disk persistence",
                ):
                    hub.close(timeout=0.2)
            finally:
                release.set()
                hub.close(timeout=1)
            self.assertLess(time.monotonic() - started, 0.75)
            lines = (
                root
                / "output/question_review_console/runtime_observations"
                / "sample/run/events.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["type"], "turnState")

    def test_delayed_disk_failures_keep_only_replay_retained_event_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorEventHub(Path(directory), replay_capacity=1)
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )
            entered = threading.Event()
            release = threading.Event()

            def delayed_failure(_event):
                entered.set()
                release.wait()
                raise OSError("simulated delayed disk failure")

            hub._write_disk_event = delayed_failure
            for index in range(100):
                hub.observe(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": "thread",
                            "turnId": "turn",
                            "itemId": f"item-{index}",
                            "delta": str(index),
                        },
                    }
                )
            try:
                self.assertTrue(entered.wait(timeout=1))
                MonitorEventStore.drain(hub, timeout=1)
                self.assertEqual(len(hub._events), 1)
            finally:
                release.set()

            hub.drain(timeout=2)
            retained_ids = {
                str(event.get("eventId") or "")
                for event in hub._events
            }
            self.assertLessEqual(len(hub._event_disk_failures), 1)
            self.assertLessEqual(len(hub._retained_event_ids), 1)
            self.assertTrue(
                set(hub._event_disk_failures).issubset(retained_ids)
            )
            hub.close()

    def test_existing_over_capacity_root_converges_when_current_run_appends(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation_root = (
                root
                / "output/question_review_console/runtime_observations/sample"
            )
            for index in range(3):
                run = observation_root / f"run-{index}"
                run.mkdir(parents=True)
                (run / "events.jsonl").write_text("{}\n", encoding="utf-8")
                time.sleep(0.002)

            with patch(
                "tools.question_review_console.monitor_events."
                "MAX_OBSERVATION_RUNS",
                2,
            ):
                hub = MonitorEventHub(root)
                hub.bind_runtime(
                    {"qualification": "sample", "runId": "run-2"},
                    "thread",
                    "turn",
                )
                hub.observe(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread",
                            "turn": {
                                "id": "turn",
                                "status": "inProgress",
                            },
                        },
                    }
                )
                hub.drain()

                retained = sorted(
                    path.name
                    for path in observation_root.iterdir()
                    if path.is_dir()
                )
                self.assertEqual(len(retained), 2)
                self.assertIn("run-2", retained)
                self.assertEqual(
                    hub.health("sample", "run-2")["observationHealth"][
                        "diskFailures"
                    ],
                    0,
                )
                hub.close()

    def test_prune_directory_swap_fails_closed_without_deleting_external_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation_root = (
                root
                / "output/question_review_console/runtime_observations/sample"
            )
            victim = observation_root / "victim"
            victim.mkdir(parents=True)
            (victim / "events.jsonl").write_text("{}\n", encoding="utf-8")
            outside = root / "outside"
            outside.mkdir()
            outside_file = outside / "events.jsonl"
            outside_file.write_text("do-not-delete\n", encoding="utf-8")
            moved = observation_root / "moved-victim"
            original = MonitorEventHub._prune_observation_run_fd
            swapped = False

            def swap_before_prune(
                observation_fd,
                qualification,
                run_id,
                *,
                expected_identity,
            ):
                nonlocal swapped
                if run_id == "victim" and not swapped:
                    swapped = True
                    victim.rename(moved)
                    victim.symlink_to(outside, target_is_directory=True)
                return original(
                    observation_fd,
                    qualification,
                    run_id,
                    expected_identity=expected_identity,
                )

            with (
                patch(
                    "tools.question_review_console.monitor_events."
                    "MAX_OBSERVATION_RUNS",
                    1,
                ),
                patch.object(
                    MonitorEventHub,
                    "_prune_observation_run_fd",
                    side_effect=swap_before_prune,
                ),
            ):
                hub = MonitorEventHub(root)
                hub.bind_runtime(
                    {"qualification": "sample", "runId": "current"},
                    "thread",
                    "turn",
                )
                hub.observe(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread",
                            "turn": {
                                "id": "turn",
                                "status": "inProgress",
                            },
                        },
                    }
                )
                hub.drain()

                self.assertTrue(outside_file.is_file())
                self.assertEqual(
                    outside_file.read_text(encoding="utf-8"),
                    "do-not-delete\n",
                )
                self.assertEqual(
                    hub.health("sample", "current")["observationHealth"][
                        "diskFailures"
                    ],
                    1,
                )
                hub.close()

    def test_global_retention_rejects_symlink_without_propagating_to_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation_root = (
                root
                / "output/question_review_console/runtime_observations"
            )
            qualification = observation_root / "sample"
            qualification.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            (qualification / "unsafe-run").symlink_to(
                outside,
                target_is_directory=True,
            )
            with patch(
                "tools.question_review_console.monitor_events."
                "MAX_OBSERVATION_RUNS",
                1,
            ):
                hub = MonitorEventHub(root)
                hub.bind_runtime(
                    {"qualification": "sample", "runId": "current-run"},
                    "thread",
                    "turn",
                )
                hub.observe(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": "thread",
                            "turn": {
                                "id": "turn",
                                "status": "inProgress",
                            },
                        },
                    }
                )
                hub.drain()

                health = hub.health(
                    "sample", "current-run"
                )["observationHealth"]
                self.assertEqual(health["eventCount"], 1)
                self.assertEqual(health["diskFailures"], 1)
                self.assertTrue(outside.is_dir())
                self.assertFalse(
                    (qualification / "current-run").exists()
                )
                self.assertEqual(
                    hub.events("sample", "current-run")[
                        "monitorModelRequests"
                    ],
                    0,
                )
                hub.close()

    def test_long_poll_waits_on_condition_and_wakes_for_matching_run(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorEventHub(Path(directory))
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )
            calls = 0
            original = hub._has_result_locked

            def counted(key, cursor):
                nonlocal calls
                calls += 1
                return original(key, cursor)

            hub._has_result_locked = counted
            result = {}

            def read():
                result.update(
                    hub.events(
                        "sample",
                        "run",
                        after=hub.replay()["cursor"],
                        wait_ms=1000,
                    )
                )

            reader = threading.Thread(target=read)
            reader.start()
            time.sleep(0.05)
            hub.observe(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn", "status": "inProgress"},
                    },
                }
            )
            hub.drain()
            reader.join(timeout=1)

            self.assertFalse(reader.is_alive())
            self.assertEqual(result["events"][0]["type"], "turnState")
            self.assertLessEqual(calls, 3)
            hub.close()

    def test_64_producer_burst_is_nonblocking_gap_accounted_and_recovers(self):
        store = MonitorEventStore(
            start_worker=False,
            queue_capacity=128,
            replay_capacity=10_000,
            server_instance_id="server",
        )
        for index in range(64):
            bind_store(
                store,
                thread_id=f"thread-{index}",
                runId="run",
                questionId=f"question-{index}",
            )

        def produce(index):
            for delta_index in range(100):
                store.put_nowait(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": f"thread-{index}",
                            "turnId": "turn-1",
                            "itemId": f"item-{index}",
                            "delta": f"{index}:{delta_index}",
                        },
                    }
                )

        started_at = time.monotonic()
        with ThreadPoolExecutor(max_workers=64) as executor:
            list(executor.map(produce, range(64)))
        producer_elapsed = time.monotonic() - started_at
        store.process_pending_for_test()

        first = store.replay(limit=10_000)
        accepted = sum(
            event["type"] == "agentMessage" for event in first["events"]
        )
        dropped = first["observation"]["droppedNotifications"]
        gaps = [
            event
            for event in first["events"]
            if event["type"] == "observationGap"
        ]
        self.assertEqual(accepted + dropped, 64 * 100)
        self.assertEqual(
            sum(event["payload"]["droppedNotifications"] for event in gaps),
            dropped,
        )
        self.assertGreater(dropped, 0)
        self.assertLess(producer_elapsed, 2.0)

        store.put_nowait(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-0",
                    "turnId": "turn-1",
                    "itemId": "follow-up",
                    "delta": "after-burst",
                },
            }
        )
        store.process_pending_for_test()
        recovered = store.replay(limit=10_000)
        self.assertEqual(
            recovered["events"][-1]["payload"]["text"],
            "after-burst",
        )
        self.assertEqual(
            recovered["observation"]["eventCount"],
            len(recovered["events"]),
        )
        self.assertEqual(recovered["monitorModelRequests"], 0)

    def test_64_concurrent_prebind_producers_remain_nonblocking_and_ordered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hub = MonitorEventHub(root, queue_capacity=4096)

            def produce(index):
                thread_id = f"thread-{index}"
                hub.put_nowait(
                    {
                        "method": "item/agentMessage/delta",
                        "params": {
                            "threadId": thread_id,
                            "turnId": f"turn-{index}",
                            "itemId": f"item-{index}",
                            "delta": str(index),
                        },
                    }
                )
                hub.bind_runtime(
                    {
                        "qualification": "sample",
                        "runId": "run",
                        "sessionId": f"session-{index}",
                        "questionId": f"question-{index}",
                    },
                    thread_id,
                    f"turn-{index}",
                )

            started_at = time.monotonic()
            with ThreadPoolExecutor(max_workers=16) as executor:
                list(executor.map(produce, range(64)))
            producer_elapsed = time.monotonic() - started_at
            hub.drain()

            events = hub.events("sample", "run", limit=500)["events"]
            sequences = [event["sequence"] for event in events]
            event_ids = [event["eventId"] for event in events]
            self.assertEqual(len(events), 64)
            self.assertEqual(sequences, sorted(sequences))
            self.assertEqual(len(event_ids), len(set(event_ids)))
            self.assertLess(producer_elapsed, 2.0)
            event_lines = (
                root
                / "output/question_review_console/runtime_observations"
                / "sample/run/events.jsonl"
            ).read_text(encoding="utf-8").splitlines()
            disk_sequences = [json.loads(line)["sequence"] for line in event_lines]
            self.assertEqual(disk_sequences, sequences)
            self.assertEqual(hub.events("sample", "run")["monitorModelRequests"], 0)
            hub.close()

    def test_health_is_compact_and_does_not_copy_events_or_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            hub = MonitorEventHub(Path(directory))
            empty_health = hub.health("sample", "run")
            self.assertEqual(
                empty_health["observationHealth"]["eventCount"], 0
            )
            self.assertEqual(
                empty_health["observationHealth"]["status"], "healthy"
            )
            hub.bind_runtime(
                {"qualification": "sample", "runId": "run"},
                "thread",
                "turn",
            )
            hub.observe(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": "thread",
                        "turn": {"id": "turn", "status": "inProgress"},
                    },
                }
            )
            hub.drain()

            health = hub.health("sample", "run")

            self.assertEqual(
                health["schemaVersion"], "monitor-observation-health/v1"
            )
            self.assertEqual(health["observationHealth"]["eventCount"], 1)
            self.assertEqual(health["observationHealth"]["status"], "healthy")
            self.assertNotIn("events", health)
            self.assertNotIn("bindings", health)
            self.assertEqual(health["monitorModelRequests"], 0)
            self.assertEqual(
                hub.events("sample", "run")["observation"]["eventCount"],
                1,
            )
            hub.close()

    def test_completed_bindings_and_run_metrics_have_fixed_memory_bounds(self):
        with (
            patch(
                "tools.question_review_console.monitor_events."
                "MAX_MONITOR_BINDINGS",
                5,
            ),
            patch(
                "tools.question_review_console.monitor_events."
                "MAX_RUN_OBSERVATION_METRICS",
                5,
            ),
            tempfile.TemporaryDirectory() as directory,
        ):
            failing_event_log = Path(directory) / "events.jsonl"
            failing_event_log.mkdir()
            store = MonitorEventStore(
                path=failing_event_log,
                start_worker=False,
                replay_capacity=100,
            )
            for index in range(12):
                thread_id = f"thread-{index}"
                bind_store(
                    store,
                    thread_id=thread_id,
                    qualification="sample",
                    runId=f"run-{index}",
                )
                store.observe(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": thread_id,
                            "turn": {
                                "id": f"turn-{index}",
                                "status": "inProgress",
                            },
                        },
                    }
                )
                store.process_pending_for_test()
                store.record_observation_gap(1)
                store.observe(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": thread_id,
                            "turn": {
                                "id": f"turn-{index}",
                                "status": "completed",
                            },
                        },
                    }
                )
                store.process_pending_for_test()

            self.assertLessEqual(len(store._bindings), 5)
            self.assertLessEqual(len(store._binding_order), 5)
            self.assertLessEqual(len(store._run_dropped), 5)
            self.assertLessEqual(len(store._run_disk_failures), 5)
            self.assertLessEqual(len(store._run_metric_order), 5)
            self.assertNotIn(("sample", "run-0"), store._run_dropped)
            self.assertNotIn(
                ("sample", "run-0"),
                store._run_disk_failures,
            )
            oldest_health = store.health("sample", "run-0")
            self.assertEqual(
                oldest_health["observationHealth"]["status"],
                "degraded",
            )
            self.assertEqual(
                oldest_health["observationHealth"]["droppedNotifications"],
                1,
            )
            self.assertEqual(
                oldest_health["observationHealth"]["diskFailures"],
                3,
            )

    def test_route_churn_coalesces_pending_gaps_with_exact_global_overflow(self):
        store = MonitorEventStore(
            start_worker=False,
            queue_capacity=1,
            replay_capacity=5000,
            server_instance_id="server",
        )
        store.put_nowait(
            {
                "method": "turn/started",
                "params": {},
            }
        )
        route_count = 2000

        started = time.monotonic()
        for index in range(route_count):
            thread_id = f"thread-{index}"
            bind_store(
                store,
                thread_id=thread_id,
                qualification="sample",
                runId=f"run-{index}",
            )
            store.put_nowait(
                {
                    "method": "turn/started",
                    "params": {
                        "threadId": thread_id,
                        "turn": {
                            "id": f"turn-{index}",
                            "status": "inProgress",
                        },
                    },
                }
            )
        elapsed = time.monotonic() - started

        expected_drops = route_count + (
            route_count - MAX_MONITOR_BINDINGS
        )
        exact_pending = sum(
            int(segment[1])
            for segment in store._gap_segments.values()
        ) + store._gap_overflow_count
        self.assertLess(elapsed, 8.0)
        self.assertEqual(len(store._bindings), MAX_MONITOR_BINDINGS)
        self.assertLessEqual(
            len(store._gap_segments),
            MAX_PENDING_GAP_SEGMENTS,
        )
        self.assertEqual(store._dropped, expected_drops)
        self.assertEqual(store._pending_gap_total, expected_drops)
        self.assertEqual(exact_pending, expected_drops)
        self.assertTrue(store._scope_truncated)
        self.assertEqual(
            store._scope_truncated_drops,
            store._gap_overflow_count,
        )

        store.process_pending_for_test()

        self.assertEqual(store._pending_gap_total, 0)
        self.assertEqual(store._materialized_gap_total, expected_drops)
        self.assertEqual(store._gap_segments, {})
        self.assertEqual(store._gap_overflow_count, 0)
        replay = store.replay(limit=5000)
        truncated = [
            event
            for event in replay["events"]
            if event.get("payload", {}).get("scopeTruncated") is True
        ]
        self.assertEqual(len(truncated), 1)
        self.assertEqual(truncated[0]["correlation"], {})
        self.assertEqual(
            truncated[0]["payload"]["droppedNotifications"],
            store._scope_truncated_drops,
        )
        self.assertEqual(
            truncated[0]["payload"]["totalDroppedNotifications"],
            expected_drops,
        )
        self.assertTrue(replay["observation"]["scopeTruncated"])
        self.assertEqual(
            replay["observation"]["scopeTruncatedDrops"],
            store._scope_truncated_drops,
        )

        overflow_only_run = store.health("sample", "run-1999")[
            "observationHealth"
        ]
        self.assertEqual(overflow_only_run["eventCount"], 0)
        self.assertEqual(overflow_only_run["droppedNotifications"], 0)
        self.assertTrue(overflow_only_run["scopeTruncated"])
        self.assertEqual(overflow_only_run["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
