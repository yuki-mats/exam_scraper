from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.monitor_service import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_FILES,
    MAX_ARTIFACT_TOTAL_BYTES,
    MonitorReadModel,
)


class FakeRunStore:
    def __init__(self, root: Path, manifests: list[dict]):
        self.root = root / "output" / "question_review_console" / "workflow_runs"
        self.manifests = {
            str(manifest["runId"]): manifest for manifest in manifests
        }
        self.dashboard_calls = 0
        for manifest in manifests:
            self.write(manifest)

    def write(self, manifest: dict) -> Path:
        self.manifests[str(manifest["runId"])] = manifest
        path = (
            self.root
            / str(manifest["qualification"])
            / str(manifest["runId"])
            / "manifest.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def dashboard_runs(self, qualification: str, *, limit: int):
        self.dashboard_calls += 1
        return [
            manifest
            for manifest in self.manifests.values()
            if manifest["qualification"] == qualification
            and not manifest.get("parentRunId")
        ][:limit]


class FakeHub:
    def __init__(self):
        self.calls = []
        self.health_calls = []
        self.snapshot_calls = 0

    def health(self, qualification, run_id):
        self.health_calls.append((qualification, run_id))
        return {
            "status": "healthy",
            "gapCount": 0,
            "prompt": "must not escape",
        }

    def snapshot(self, qualification, run_id):
        self.snapshot_calls += 1
        raise AssertionError("lightweight health path must be preferred")

    def events(self, qualification, run_id, *, after, limit, wait_ms):
        self.calls.append((qualification, run_id, after, limit, wait_ms))
        return {
            "schemaVersion": "attacker-controlled/v9",
            "events": [
                {
                    "schemaVersion": "wrong",
                    "eventId": "instance:2",
                    "serverInstanceId": "instance",
                    "sequence": 2,
                    "observedAt": 2.0,
                    "type": "agentMessage",
                    "correlation": {
                        "runId": run_id,
                        "threadId": "thread-1",
                        "promptPath": "/Users/yuki/private/prompt.md",
                    },
                    "payload": {
                        "text": (
                            "saved /Users/yuki/private/result.json "
                            "Bearer very-secret-token "
                            "xoxb-123456789-secret glpat-123456789-secret "
                            "AIza123456789012345678901234567890 "
                            "https://user:password@example.invalid/path "
                            "postgresql://admin:db-secret@example.invalid/db "
                            "redis://:cache-secret@example.invalid/0 "
                            "ftp://user:file-secret@example.invalid/file"
                        ),
                        "command": "cat ~/.ssh/id_ed25519",
                    },
                    "rawManifest": {"secret": True},
                },
                {
                    "schemaVersion": "wrong",
                    "eventId": "instance:3",
                    "serverInstanceId": "instance",
                    "sequence": 3,
                    "observedAt": 3.0,
                    "type": "tokenUsage",
                    "correlation": {
                        "runId": run_id,
                        "stageId": "03",
                        "workType": "maintenance",
                        "phase": "review",
                        "listGroupId": "2026",
                        "questionIds": ["q-1", "q-2"],
                        "workItemKeys": ["q-1:03", "q-2:03"],
                        "listGroupIds": ["2026"],
                    },
                    "payload": {
                        "usage": {
                            "last": {"inputTokens": 10, "secret": 999},
                            "total": {"totalTokens": 20},
                            "modelContextWindow": 200000,
                        }
                    },
                },
                {
                    "schemaVersion": "wrong",
                    "eventId": "instance:4",
                    "serverInstanceId": "instance",
                    "sequence": 4,
                    "observedAt": 4.0,
                    "type": "plan",
                    "correlation": {"runId": run_id},
                    "payload": {
                        "plan": [
                            {
                                "step": "確認",
                                "status": "inProgress",
                                "prompt": "private",
                            }
                        ],
                        "explanation": "公開計画",
                    },
                },
                {
                    "schemaVersion": "wrong",
                    "eventId": "instance:5",
                    "serverInstanceId": "instance",
                    "sequence": 5,
                    "observedAt": 5.0,
                    "type": "error",
                    "correlation": {"runId": run_id},
                    "payload": {
                        "message": "公開エラー",
                        "willRetry": True,
                        "additionalDetails": "private",
                    },
                },
                {
                    "eventId": "instance:6",
                    "serverInstanceId": "instance",
                    "sequence": 6,
                    "observedAt": 6.0,
                    "type": "reasoningSummaryPart",
                    "correlation": {"runId": run_id},
                    "payload": {
                        "summaryIndex": 2,
                        "rawReasoning": "private",
                    },
                },
                {
                    "eventId": "instance:7",
                    "serverInstanceId": "instance",
                    "sequence": 7,
                    "observedAt": 7.0,
                    "type": "threadState",
                    "correlation": {"runId": run_id},
                    "payload": {
                        "state": "active",
                        "activeFlags": ["waitingOnUserInput"],
                        "thread": {"private": True},
                    },
                }
            ],
            "cursor": "instance:7",
            "command": "forbidden",
        }


class MonitorReadModelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifact_relative = (
            "output/demo/questions_json/2026/"
            "21_explanationText_added/q-1_explanationText_added.json"
        )
        artifact = self.root / self.artifact_relative
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            json.dumps(
                {
                    "question_bodies": [
                        {
                            "questionId": "q-1",
                            "sourceQuestionKey": "demo:2026:q-1",
                            "sourceRecordRef": "source.json#0",
                            "reviewQuestionId": "review-q-1",
                            "explanationText": "保存済み",
                        },
                        *[
                            {
                                "questionId": f"other-{index}",
                                "sourceQuestionKey": f"demo:2026:other-{index}",
                                "sourceRecordRef": f"source.json#{index + 1}",
                                "explanationText": "別問題",
                            }
                            for index in range(24)
                        ],
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.parent = {
            "runId": "run-1",
            "qualification": "demo",
            "status": "running",
            "executionPhase": "candidate:03",
            "heartbeatAt": "2026-07-27T00:00:00Z",
            "receiptValidated": False,
            "artifactSync": {"status": "pending", "message": "private"},
            "childRunIds": ["child-1"],
            "targetGroupIds": ["2026"],
            "targetRecordBindings": [
                {
                    "uiQuestionId": "q-1",
                    "sourceQuestionKey": "demo:2026:q-1",
                    "sourceRecordRef": "source.json#0",
                    "reviewQuestionId": "review-q-1",
                    "aliases": ["q-1"],
                }
            ],
            "progressTargets": [
                {
                    "id": "q-1",
                    "uiQuestionId": "q-1",
                    "sourceQuestionKey": "demo:2026:q-1",
                    "sourceRecordRef": "source.json#0",
                    "reviewQuestionId": "review-q-1",
                    "listGroupId": "2026",
                }
            ],
            "promptPath": "/Users/yuki/private/prompt.md",
            "error": "Bearer should-not-escape",
            "commands": [{"command": "cat secret"}],
            "questionExecutions": [
                {
                    "questionId": "q-1",
                    "workItemKey": "q-1:03",
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "item-1",
                    "prompt": "private",
                }
            ],
        }
        self.child = {
            "runId": "child-1",
            "parentRunId": "run-1",
            "qualification": "demo",
            "status": "succeeded",
            "stageCode": "03",
            "questionId": "q-1",
            "workItemKey": "q-1:03",
            "batchId": "batch-7",
            "targetGroupIds": ["2026"],
            "targetRecordBindings": self.parent["targetRecordBindings"],
            "progressTargets": self.parent["progressTargets"],
            "receiptValidated": True,
            "artifactSync": {"status": "deferred", "message": "private"},
            "batchQuestionResults": [
                {
                    "questionId": "q-1",
                    "workItemKey": "q-1:03",
                    "status": "succeeded",
                    "changedFiles": [self.artifact_relative],
                    "commands": [{"command": "private"}],
                }
            ],
            "result": {
                "status": "succeeded",
                "changedFiles": [self.artifact_relative],
                "commands": [{"command": "private"}],
            },
        }
        self.store = FakeRunStore(self.root, [self.parent, self.child])
        self.hub = FakeHub()
        self.model = MonitorReadModel(self.root, self.store, self.hub)

    def tearDown(self):
        self.temp.cleanup()

    def test_list_uses_existing_index_without_generating_cache(self):
        index = self.store.root / "demo" / "dashboard_runs.json"
        index.write_text(
            json.dumps(
                {
                    "schemaVersion": "qualification-dashboard-run-index/v1",
                    "qualification": "demo",
                    "complete": True,
                    "runs": [self.parent],
                }
            ),
            encoding="utf-8",
        )

        payload = self.model.runs("demo")

        self.assertEqual(self.store.dashboard_calls, 0)
        self.assertEqual(payload["monitorModelRequests"], 0)
        run = payload["runs"][0]
        self.assertEqual(run["executionState"]["status"], "running")
        self.assertEqual(run["artifactState"]["syncStatus"], "pending")
        serialized = json.dumps(payload)
        self.assertNotIn("questionExecutions", serialized)
        self.assertNotIn("promptPath", serialized)
        self.assertNotIn("commands", serialized)
        self.assertNotIn("error", serialized)

    def test_snapshot_is_compact_allowlist_with_exact_identifiers_and_health(self):
        payload = self.model.snapshot("run-1")

        self.assertEqual(payload["identities"]["childRunId"], ["child-1"])
        self.assertEqual(payload["identities"]["questionId"], ["q-1"])
        self.assertEqual(payload["identities"]["workItemKey"], ["q-1:03"])
        self.assertEqual(payload["identities"]["threadId"], ["thread-1"])
        self.assertEqual(payload["identities"]["turnId"], ["turn-1"])
        self.assertEqual(payload["identities"]["itemId"], ["item-1"])
        self.assertEqual(payload["observationHealth"], {
            "status": "healthy",
            "gapCount": 0,
        })
        self.assertEqual(self.hub.health_calls, [("demo", "run-1")])
        self.assertEqual(self.hub.snapshot_calls, 0)
        self.assertEqual(payload["lanes"][0]["runId"], "child-1")
        self.assertEqual(payload["run"]["listGroupId"], "2026")
        self.assertEqual(payload["lanes"][0]["listGroupId"], "2026")
        serialized = json.dumps(payload)
        self.assertNotIn("promptPath", serialized)
        self.assertNotIn("commands", serialized)
        self.assertNotIn("rawManifest", serialized)
        self.assertNotIn("Bearer", serialized)

    def test_snapshot_prefers_valid_list_summary_over_large_parent_manifest(self):
        self.parent["privateBlob"] = "x" * (2 * 1024 * 1024)
        manifest_path = self.store.write(self.parent)
        manifest_stat = manifest_path.stat()
        summary = {
            key: self.parent[key]
            for key in (
                "runId",
                "qualification",
                "status",
                "receiptValidated",
                "artifactSync",
                "childRunIds",
            )
        }
        manifest_path.with_name("list_summary.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "qualification-run-list-summary/v1",
                    "manifestSignature": [
                        manifest_stat.st_ino,
                        manifest_stat.st_mtime_ns,
                        manifest_stat.st_size,
                    ],
                    "summary": summary,
                }
            ),
            encoding="utf-8",
        )

        payload = self.model.snapshot("run-1", qualification="demo")

        self.assertEqual(payload["run"]["runId"], "run-1")
        self.assertNotIn("questionExecutions", json.dumps(payload))

    def test_artifacts_use_compact_parent_when_parent_manifest_exceeds_bound(self):
        child_path = (
            self.store.root / "demo" / "child-1" / "manifest.json"
        )
        bound = child_path.stat().st_size + 1024
        self.parent["privateBlob"] = "x" * (bound * 2)
        manifest_path = self.store.write(self.parent)
        manifest_stat = manifest_path.stat()
        summary = {
            key: self.parent[key]
            for key in (
                "runId",
                "qualification",
                "status",
                "receiptValidated",
                "artifactSync",
                "childRunIds",
            )
        }
        manifest_path.with_name("list_summary.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "qualification-run-list-summary/v1",
                    "manifestSignature": [
                        manifest_stat.st_ino,
                        manifest_stat.st_mtime_ns,
                        manifest_stat.st_size,
                    ],
                    "summary": summary,
                }
            ),
            encoding="utf-8",
        )

        with patch(
            "tools.question_review_console.monitor_service."
            "MAX_MANIFEST_FALLBACK_BYTES",
            bound,
        ):
            payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(len(payload["artifacts"]), 1)
        self.assertIn("保存済み", payload["artifacts"][0]["content"])

    def test_snapshot_poll_load_keeps_latest_two_64_lane_waves(self):
        child_ids = [f"child-{index:03d}" for index in range(451)]
        self.parent["childRunIds"] = child_ids
        self.store.write(self.parent)
        for child_id in child_ids[-128:]:
            self.store.write(
                {
                    "runId": child_id,
                    "parentRunId": "run-1",
                    "qualification": "demo",
                    "status": "running",
                    "stageCode": "03",
                    "receiptValidated": False,
                    "artifactSync": {"status": "pending"},
                }
            )

        snapshots = [
            self.model.snapshot("run-1", qualification="demo")
            for _index in range(8)
        ]

        for payload in snapshots:
            self.assertEqual(len(payload["lanes"]), 128)
            self.assertEqual(payload["lanes"][0]["runId"], "child-323")
            self.assertEqual(payload["lanes"][-1]["runId"], "child-450")
        self.assertEqual(self.hub.snapshot_calls, 0)
        self.assertEqual(len(self.hub.health_calls), 8)

    def test_events_are_bounded_redacted_and_strictly_allowlisted(self):
        payload = self.model.events(
            "run-1", after="1", limit=9999, wait_ms=999999
        )

        self.assertEqual(self.hub.calls, [("demo", "run-1", "1", 500, 30_000)])
        self.assertEqual(payload["schemaVersion"], "monitor-events/v1")
        self.assertEqual(
            payload["events"][0]["schemaVersion"], "monitor-event/v1"
        )
        self.assertEqual(payload["cursor"], "instance:7")
        self.assertEqual(payload["monitorModelRequests"], 0)
        token = payload["events"][1]
        self.assertEqual(token["correlation"]["stageId"], "03")
        self.assertEqual(token["correlation"]["questionIds"], ["q-1", "q-2"])
        self.assertEqual(token["payload"]["usage"]["last"]["inputTokens"], 10)
        self.assertEqual(token["payload"]["usage"]["total"]["totalTokens"], 20)
        self.assertEqual(
            token["payload"]["usage"]["modelContextWindow"], 200000
        )
        self.assertEqual(
            payload["events"][2]["payload"]["plan"],
            [{"step": "確認", "status": "inProgress"}],
        )
        self.assertEqual(
            payload["events"][3]["payload"],
            {"message": "公開エラー", "willRetry": True},
        )
        self.assertEqual(
            payload["events"][4]["payload"], {"summaryIndex": 2}
        )
        self.assertEqual(
            payload["events"][5]["payload"],
            {"state": "active", "activeFlags": ["waitingOnUserInput"]},
        )
        serialized = json.dumps(payload)
        self.assertNotIn("/Users/yuki", serialized)
        self.assertNotIn("very-secret-token", serialized)
        self.assertNotIn("xoxb-", serialized)
        self.assertNotIn("glpat-", serialized)
        self.assertNotIn("AIza", serialized)
        self.assertNotIn("user:password", serialized)
        self.assertNotIn("admin:db-secret", serialized)
        self.assertNotIn(":cache-secret", serialized)
        self.assertNotIn("user:file-secret", serialized)
        self.assertNotIn("command", serialized)
        self.assertNotIn("promptPath", serialized)
        self.assertNotIn("rawManifest", serialized)

    def test_artifacts_use_only_exact_changed_files_with_question_batch_identity(self):
        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(len(payload["artifacts"]), 1)
        artifact = payload["artifacts"][0]
        self.assertEqual(artifact["path"], self.artifact_relative)
        self.assertIn("保存済み", artifact["content"])
        self.assertNotIn("別問題", artifact["content"])
        self.assertEqual(artifact["identity"]["parentRunId"], "run-1")
        self.assertEqual(artifact["identity"]["childRunId"], "child-1")
        self.assertEqual(artifact["identity"]["questionId"], "q-1")
        self.assertEqual(artifact["identity"]["listGroupId"], "2026")
        self.assertEqual(artifact["identity"]["batchId"], "batch-7")
        self.assertEqual(
            artifact["identity"]["sourceQuestionKey"], "demo:2026:q-1"
        )
        self.assertEqual(
            artifact["identity"]["sourceRecordRef"], "source.json#0"
        )
        self.assertEqual(
            artifact["identity"]["reviewQuestionId"], "review-q-1"
        )
        self.assertEqual(
            artifact["receiptValidation"],
            {"status": "validated", "validated": True},
        )
        self.assertEqual(artifact["artifactSync"]["status"], "deferred")
        self.assertEqual(artifact["artifactSync"]["parentStatus"], "pending")
        self.assertEqual(artifact["contentState"], {"status": "saved"})
        self.assertNotIn("commands", json.dumps(payload))
        child_payload = self.model.artifacts("child-1", qualification="demo")
        self.assertEqual(
            child_payload["artifacts"][0]["identity"]["sourceRecordRef"],
            "source.json#0",
        )

    def test_shared_json_patch_without_exact_record_binding_is_rejected(self):
        self.child["targetRecordBindings"] = []
        self.child["progressTargets"] = []
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "record_resolution_failed",
        )

    def test_shared_json_patch_without_question_id_on_batch_result_is_rejected(self):
        self.child["batchQuestionResults"][0].pop("questionId")
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "record_resolution_failed",
        )
        self.assertNotIn("別問題", json.dumps(payload, ensure_ascii=False))

    def test_arbitrary_path_fields_traversal_symlink_and_secrets_are_not_exposed(self):
        outside = self.root.parent / "monitor-secret.txt"
        outside.write_text("outside-super-secret", encoding="utf-8")
        try:
            link = self.root / "output" / "demo" / "link.txt"
            link.symlink_to(outside)
            self.child["resultPath"] = "output/demo/unlisted-secret.txt"
            self.child["reportPath"] = str(outside)
            self.child["batchQuestionResults"].append(
                {
                    "questionId": "q-2",
                    "status": "succeeded",
                    "changedFiles": [
                        "../monitor-secret.txt",
                        "output/demo/link.txt",
                    ],
                }
            )
            self.store.write(self.child)

            payload = self.model.artifacts("run-1", qualification="demo")

            serialized = json.dumps(payload)
            self.assertNotIn("outside-super-secret", serialized)
            self.assertNotIn(str(outside), serialized)
            self.assertNotIn("resultPath", serialized)
            self.assertNotIn("reportPath", serialized)
            reasons = {item["reasonCode"] for item in payload["rejected"]}
            self.assertEqual(reasons, {"unsafe_path", "unavailable"})
        finally:
            outside.unlink(missing_ok=True)

    def test_file_count_is_bounded(self):
        changed_files = []
        for index in range(MAX_ARTIFACT_FILES + 3):
            relative = f"output/demo/questions_json/2026/21_explanationText_added/{index}.txt"
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
            changed_files.append(relative)
        self.child["batchQuestionResults"] = [
            {
                "questionId": "q-many",
                "status": "succeeded",
                "changedFiles": changed_files,
            }
        ]
        self.child["result"]["changedFiles"] = changed_files
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(len(payload["artifacts"]), MAX_ARTIFACT_FILES)
        self.assertIn(
            "file_count_limit",
            {item["reasonCode"] for item in payload["rejected"]},
        )

    def test_per_file_and_total_byte_limits_are_bounded(self):
        oversized = "output/demo/questions_json/2026/oversized.txt"
        oversized_path = self.root / oversized
        oversized_path.parent.mkdir(parents=True, exist_ok=True)
        oversized_path.write_text(
            "x" * (MAX_ARTIFACT_BYTES + 1),
            encoding="utf-8",
        )
        changed_files = [oversized]
        chunk_bytes = MAX_ARTIFACT_TOTAL_BYTES // 4
        for index in range(4):
            relative = f"output/demo/questions_json/2026/chunk-{index}.txt"
            (self.root / relative).write_text("x" * chunk_bytes, encoding="utf-8")
            changed_files.append(relative)
        final = "output/demo/questions_json/2026/over-total.txt"
        (self.root / final).write_text("{}", encoding="utf-8")
        changed_files.append(final)
        self.child["batchQuestionResults"] = [
            {
                "questionId": "q-bytes",
                "status": "succeeded",
                "changedFiles": changed_files,
            }
        ]
        self.child["result"]["changedFiles"] = changed_files
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(
            sum(item["size"] for item in payload["artifacts"]),
            MAX_ARTIFACT_TOTAL_BYTES,
        )
        self.assertEqual(
            {item["reasonCode"] for item in payload["rejected"]},
            {"file_bytes_limit", "total_bytes_limit"},
        )

    def test_missing_run_and_path_traversal_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.model.snapshot("missing", qualification="demo")
        with self.assertRaises(ValueError):
            self.model.snapshot("../run-1")


if __name__ == "__main__":
    unittest.main()
