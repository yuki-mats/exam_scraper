import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tools.question_review_console.monitor_events import (
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
    def test_delta_events_are_append_only_and_cursor_returns_only_new_text(self):
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

        self.assertEqual([event["payload"]["delta"] for event in first["events"]], ["公"])
        self.assertEqual([event["payload"]["delta"] for event in delta["events"]], ["開"])
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
            '"OPENAI_API_KEY": "env-key" Cookie: session=abc '
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
            "/tmp/private",
            "/var/private",
            "C:\\\\Users",
            "must never be copied",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("<redacted", serialized)
        self.assertIn("<absolute-path>", serialized)

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
            hub.close()


if __name__ == "__main__":
    unittest.main()
