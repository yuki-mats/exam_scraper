from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.monitor_service import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_FILES,
    MAX_ARTIFACT_TOTAL_BYTES,
    MAX_EVENT_TOTAL_BYTES,
    MAX_MONITOR_RESPONSE_BYTES,
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
            "eventCount": 0,
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
                    "occurredAt": "2026-07-27T00:00:01Z",
                    "type": "agentMessage",
                    "correlation": {
                        "runId": run_id,
                        "threadId": "thread-1",
                        "promptPath": "/Users/yuki/private/prompt.md",
                    },
                    "payload": {
                        "text": (
                            "saved /Users/yuki/private/result.json "
                            "cwd:/workspace/project /tmp/private /var/private "
                            "Q₂/Q₁ Cu2+/Cu /h /600=40kPa "
                            "Authorization: Basic dXNlcjpwYXNz "
                            "Authorization = Bearer equal-secret-value "
                            "github_pat_abcdefghijklmnop "
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
            "observation": {
                "eventCount": 6,
                "droppedNotifications": 0,
                "diskFailures": 0,
            },
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

    def _write_v2_question_state(self, run_id: str, state: dict) -> Path:
        material = {
            key: value
            for key, value in state.items()
            if key != "selfHash"
        }
        state["selfHash"] = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        question_id = str(state["questionId"])
        path = (
            self.store.root
            / "demo"
            / run_id
            / "questions"
            / (
                f"{hashlib.sha256(question_id.strip().encode('utf-8')).hexdigest()}"
                ".json"
            )
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def _write_v2_run(self) -> tuple[str, str, str]:
        run_id = "run-v2"
        active_question_id = " 問題 / 1 "
        saved_question_id = "q-1"
        active_work_item = "work-active"
        saved_work_item = "work-saved"
        plan_material = {
            "schemaVersion": "question-maintenance-plan/v2",
            "createdAt": "2026-07-27T00:00:00Z",
            "plan": {
                "qualification": "demo",
                "questionExecutions": [
                    {
                        "questionId": active_question_id,
                        "questionKey": "demo:2026:q-active",
                        "sourceQuestionKey": "demo:2026:q-active",
                        "sourceRecordRef": "source.json#1",
                        "reviewQuestionId": "review-q-active",
                        "listGroupId": "2026",
                        "stages": [
                            {
                                "stageId": "candidate:03",
                                "stageCode": "03",
                                "stageLabel": "解説",
                                "workItemKey": active_work_item,
                            }
                        ],
                    },
                    {
                        "questionId": saved_question_id,
                        "questionKey": "demo:2026:q-1",
                        "sourceQuestionKey": "demo:2026:q-1",
                        "sourceRecordRef": "source.json#0",
                        "reviewQuestionId": "review-q-1",
                        "listGroupId": "2026",
                        "stages": [
                            {
                                "stageId": "candidate:03",
                                "stageCode": "03",
                                "stageLabel": "解説",
                                "workItemKey": saved_work_item,
                            }
                        ],
                    },
                ],
            },
        }
        plan_hash = hashlib.sha256(
            json.dumps(
                plan_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        saved_batch_result = {
            "questionId": saved_question_id,
            "status": "succeeded",
            "summary": "検証済み",
            "commands": [],
            "changedFiles": [self.artifact_relative],
        }
        saved_output_fingerprint = hashlib.sha256(
            json.dumps(
                saved_batch_result,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        active_token = "1" * 16
        saved_token = "2" * 16
        active_attempt = (
            f"qa-{run_id}-"
            f"{hashlib.sha256(active_question_id.strip().encode('utf-8')).hexdigest()}-"
            f"{active_token}"
        )
        saved_attempt = (
            f"qa-{run_id}-"
            f"{hashlib.sha256(saved_question_id.encode('utf-8')).hexdigest()}-"
            f"{saved_token}"
        )
        parent = {
            "schemaVersion": "question-maintenance-run/v2",
            "runId": run_id,
            "qualification": "demo",
            "status": "running",
            "executionPhase": "candidate:03",
            "planHash": plan_hash,
            "questionStateCount": 2,
            "questionSummaryPath": (
                "output/question_review_console/workflow_runs/"
                f"demo/{run_id}/question_summary.json"
            ),
            "questionStateDirectory": (
                "output/question_review_console/workflow_runs/"
                f"demo/{run_id}/questions"
            ),
            "planPath": (
                "output/question_review_console/workflow_runs/"
                f"demo/{run_id}/plan.json"
            ),
            "childRunIds": [],
        }
        self.store.write(parent)
        plan_path = self.store.root / "demo" / run_id / "plan.json"
        plan_path.write_text(
            json.dumps(
                {**plan_material, "planHash": plan_hash},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        summary = {
            "schemaVersion": "question-maintenance-summary/v2",
            "planHash": plan_hash,
            "questionCount": 2,
            "questions": [
                {
                    "questionId": active_question_id,
                    "questionKey": "demo:2026:q-active",
                    "sourceQuestionKey": "demo:2026:q-active",
                    "sourceRecordRef": "source.json#1",
                    "reviewQuestionId": "review-q-active",
                    "displayOrder": 1,
                    "listGroupId": "2026",
                    "stages": [
                        {
                            "stageId": "candidate:03",
                            "stageCode": "03",
                            "stageLabel": "解説",
                            "workItemKey": active_work_item,
                            "status": "preparing",
                            "startedAt": "2026-07-27T01:00:00Z",
                        }
                    ],
                },
                {
                    "questionId": saved_question_id,
                    "questionKey": "demo:2026:q-1",
                    "sourceQuestionKey": "demo:2026:q-1",
                    "sourceRecordRef": "source.json#0",
                    "reviewQuestionId": "review-q-1",
                    "displayOrder": 2,
                    "listGroupId": "2026",
                    "stages": [
                        {
                            "stageId": "candidate:03",
                            "stageCode": "03",
                            "stageLabel": "解説",
                            "workItemKey": saved_work_item,
                            "status": "validated",
                            "outputFingerprint": saved_output_fingerprint,
                            "startedAt": "2026-07-27T01:00:00Z",
                            "finishedAt": "2026-07-27T01:01:00Z",
                        }
                    ],
                },
            ],
        }
        summary_path = (
            self.store.root / "demo" / run_id / "question_summary.json"
        )
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False),
            encoding="utf-8",
        )
        active_directory = (
            "output/question_review_console/workflow_runs/"
            f"demo/{run_id}/attempts/{active_token}"
        )
        saved_directory = (
            "output/question_review_console/workflow_runs/"
            f"demo/{run_id}/attempts/{saved_token}"
        )
        saved_result = {
            "status": "succeeded",
            "changedFiles": [self.artifact_relative],
            "commands": [
                {"command": "private command must not escape"}
            ],
        }
        saved_receipt = self.root / saved_directory / "result.json"
        saved_receipt.parent.mkdir(parents=True, exist_ok=True)
        saved_receipt.write_text(
            json.dumps(saved_result, ensure_ascii=False),
            encoding="utf-8",
        )
        self._write_v2_question_state(
            run_id,
            {
                "schemaVersion": "question-maintenance-question/v2",
                "planHash": plan_hash,
                "questionId": active_question_id,
                "activeAttemptId": active_attempt,
                "execution": {
                    "questionId": active_question_id,
                    "questionKey": "demo:2026:q-active",
                    "listGroupId": "2026",
                    "sourceQuestionKey": "demo:2026:q-active",
                    "sourceRecordRef": "source.json#1",
                    "reviewQuestionId": "review-q-active",
                    "stages": [
                        {
                            "stageId": "candidate:03",
                            "stageCode": "03",
                            "stageLabel": "解説",
                            "workItemKey": active_work_item,
                            "status": "preparing",
                            "privatePrompt": "must-not-escape",
                        }
                    ],
                },
                "attemptArtifacts": {
                    active_attempt: {
                        "attemptId": active_attempt,
                        "parentRunId": run_id,
                        "questionId": active_question_id,
                        "stageId": "candidate:03",
                        "artifactDirectory": active_directory,
                        "resultReceiptPath": f"{active_directory}/result.json",
                        "progressReceiptPath": (
                            f"{active_directory}/progress.jsonl"
                        ),
                        "status": "running",
                        "threadId": "thread-active",
                        "turnId": "turn-active",
                        "privatePlan": "must-not-escape",
                    }
                },
                "validatedReceipts": [],
            },
        )
        self._write_v2_question_state(
            run_id,
            {
                "schemaVersion": "question-maintenance-question/v2",
                "planHash": plan_hash,
                "questionId": saved_question_id,
                "activeAttemptId": "",
                "execution": {
                    "questionId": saved_question_id,
                    "questionKey": "demo:2026:q-1",
                    "listGroupId": "2026",
                    "sourceQuestionKey": "demo:2026:q-1",
                    "sourceRecordRef": "source.json#0",
                    "reviewQuestionId": "review-q-1",
                    "stages": [
                        {
                            "stageId": "candidate:03",
                            "stageCode": "03",
                            "stageLabel": "解説",
                            "workItemKey": saved_work_item,
                            "status": "validated",
                            "outputFingerprint": saved_output_fingerprint,
                            "validationAttempts": [
                                {
                                    "childRunId": saved_attempt,
                                    "status": "validated",
                                    "startedAt": "2026-07-27T01:00:00Z",
                                    "finishedAt": "2026-07-27T01:01:00Z",
                                }
                            ],
                        }
                    ],
                },
                "attemptArtifacts": {
                    saved_attempt: {
                        "attemptId": saved_attempt,
                        "parentRunId": run_id,
                        "questionId": saved_question_id,
                        "stageId": "candidate:03",
                        "artifactDirectory": saved_directory,
                        "resultReceiptPath": f"{saved_directory}/result.json",
                        "progressReceiptPath": (
                            f"{saved_directory}/progress.jsonl"
                        ),
                        "status": "succeeded",
                        "threadId": "thread-saved",
                        "turnId": "turn-saved",
                        "receiptValidated": True,
                        "artifactSync": {"status": "deferred"},
                        "batchQuestionResults": [saved_batch_result],
                        "result": saved_result,
                    }
                },
                "validatedReceipts": [
                    {"childRunId": saved_attempt, "private": "must-not-escape"}
                ],
            },
        )
        return run_id, active_attempt, saved_attempt

    def test_v2_run_projects_parallel_lanes_and_validated_artifact(self):
        run_id, active_attempt, saved_attempt = self._write_v2_run()

        snapshot = self.model.snapshot(run_id, qualification="demo")
        artifacts = self.model.artifacts(run_id, qualification="demo")

        self.assertEqual(len(snapshot["lanes"]), 2)
        by_work_item = {
            lane["workItemKey"]: lane for lane in snapshot["lanes"]
        }
        self.assertEqual(
            by_work_item["work-active"]["runId"],
            active_attempt,
        )
        self.assertEqual(
            by_work_item["work-saved"]["runId"],
            saved_attempt,
        )
        self.assertEqual(
            snapshot["identities"]["questionId"],
            [" 問題 / 1 ", "q-1"],
        )
        self.assertEqual(
            snapshot["identities"]["workItemKey"],
            ["work-active", "work-saved"],
        )
        self.assertTrue(snapshot["artifactFingerprintComplete"])
        self.assertFalse(snapshot["truncated"])
        self.assertEqual(len(artifacts["artifacts"]), 1)
        artifact = artifacts["artifacts"][0]
        self.assertEqual(artifact["identity"]["questionId"], "q-1")
        self.assertEqual(
            artifact["identity"]["childRunId"],
            saved_attempt,
        )
        self.assertEqual(
            artifact["receiptValidation"],
            {"status": "validated", "validated": True},
        )
        self.assertEqual(artifact["artifactSync"]["status"], "deferred")
        self.assertIn("保存済み", artifact["content"])
        self.assertNotIn("別問題", artifact["content"])
        serialized = json.dumps(
            {"snapshot": snapshot, "artifacts": artifacts},
            ensure_ascii=False,
        )
        self.assertNotIn("must-not-escape", serialized)
        self.assertNotIn("private command", serialized)

    def test_v2_fingerprint_ignores_lane_progress_and_tracks_saved_content(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        first = self.model.snapshot(run_id, qualification="demo")
        summary_path = (
            self.store.root / "demo" / run_id / "question_summary.json"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["questions"][0]["stages"][0]["status"] = "prepared"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        progressed = self.model.snapshot(run_id, qualification="demo")

        self.assertEqual(
            first["artifactFingerprint"],
            progressed["artifactFingerprint"],
        )
        artifact = self.root / self.artifact_relative
        content = json.loads(artifact.read_text(encoding="utf-8"))
        content["question_bodies"][0]["explanationText"] = "v2更新済み"
        artifact.write_text(json.dumps(content), encoding="utf-8")
        changed = self.model.snapshot(run_id, qualification="demo")
        self.assertNotEqual(
            progressed["artifactFingerprint"],
            changed["artifactFingerprint"],
        )

    def test_v2_invalid_state_hash_is_explicit_and_never_serves_artifact(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        state_path = (
            self.store.root
            / "demo"
            / run_id
            / "questions"
            / f"{hashlib.sha256(b'q-1').hexdigest()}.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["execution"]["sourceRecordRef"] = "source.json#999"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        snapshot = self.model.snapshot(run_id, qualification="demo")
        artifacts = self.model.artifacts(run_id, qualification="demo")

        self.assertTrue(snapshot["truncated"])
        self.assertIn("v2_question_state_hash_invalid", snapshot["warnings"])
        self.assertEqual(artifacts["artifacts"], [])
        self.assertIn(
            "v2_question_state_hash_invalid",
            {item["reasonCode"] for item in artifacts["rejected"]},
        )

    def test_v2_missing_result_receipt_fails_closed(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        receipt = (
            self.store.root
            / "demo"
            / run_id
            / "attempts"
            / ("2" * 16)
            / "result.json"
        )
        receipt.unlink()

        snapshot = self.model.snapshot(run_id, qualification="demo")
        artifacts = self.model.artifacts(run_id, qualification="demo")

        self.assertTrue(snapshot["truncated"])
        self.assertIn(
            "v2_attempt_receipt_unavailable",
            snapshot["warnings"],
        )
        self.assertEqual(artifacts["artifacts"], [])
        self.assertIn(
            "v2_attempt_receipt_unavailable",
            {item["reasonCode"] for item in artifacts["rejected"]},
        )

    def test_v2_batch_result_attribution_mismatch_fails_closed(self):
        run_id, _active_attempt, saved_attempt = self._write_v2_run()
        state_path = (
            self.store.root
            / "demo"
            / run_id
            / "questions"
            / f"{hashlib.sha256(b'q-1').hexdigest()}.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["attemptArtifacts"][saved_attempt]["batchQuestionResults"][0][
            "questionId"
        ] = "other-question"
        self._write_v2_question_state(run_id, state)

        snapshot = self.model.snapshot(run_id, qualification="demo")
        artifacts = self.model.artifacts(run_id, qualification="demo")

        self.assertIn(
            "v2_attempt_result_attribution_mismatch",
            snapshot["warnings"],
        )
        self.assertEqual(artifacts["artifacts"], [])
        self.assertIn(
            "v2_attempt_result_attribution_mismatch",
            {item["reasonCode"] for item in artifacts["rejected"]},
        )

    def test_v2_optional_compact_question_fields_may_be_absent(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        fields = (
            "questionKey",
            "reviewQuestionId",
            "sourceQuestionKey",
            "sourceRecordRef",
            "listGroupId",
        )
        plan_path = self.store.root / "demo" / run_id / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        for execution in plan["plan"]["questionExecutions"]:
            for field in fields:
                execution.pop(field, None)
        plan_without_hash = {
            key: value for key, value in plan.items() if key != "planHash"
        }
        plan_hash = hashlib.sha256(
            json.dumps(
                plan_without_hash,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        plan["planHash"] = plan_hash
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        parent = dict(self.store.manifests[run_id])
        parent["planHash"] = plan_hash
        self.store.write(parent)
        summary_path = (
            self.store.root / "demo" / run_id / "question_summary.json"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for question in summary["questions"]:
            for field in fields:
                question.pop(field, None)
        summary["planHash"] = plan_hash
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        for state_path in (
            self.store.root / "demo" / run_id / "questions"
        ).glob("*.json"):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["planHash"] = plan_hash
            for field in fields:
                state["execution"].pop(field, None)
            self._write_v2_question_state(run_id, state)

        snapshot = self.model.snapshot(run_id, qualification="demo")
        artifacts = self.model.artifacts(run_id, qualification="demo")

        self.assertFalse(snapshot["truncated"])
        self.assertEqual(len(snapshot["lanes"]), 2)
        self.assertEqual(artifacts["artifacts"], [])
        self.assertEqual(
            {item["reasonCode"] for item in artifacts["rejected"]},
            {"record_resolution_failed"},
        )

    def test_v2_tampered_plan_is_explicit(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        plan_path = self.store.root / "demo" / run_id / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["plan"]["qualification"] = "tampered"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        snapshot = self.model.snapshot(run_id, qualification="demo")

        self.assertTrue(snapshot["truncated"])
        self.assertIn("v2_plan_hash_invalid", snapshot["warnings"])

    def test_v2_run_local_plan_symlink_is_rejected_before_resolution(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        plan_path = self.store.root / "demo" / run_id / "plan.json"
        cross_run_plan = self.store.root / "demo" / "cross-run-plan.json"
        plan_path.replace(cross_run_plan)
        plan_path.symlink_to(cross_run_plan)

        snapshot = self.model.snapshot(run_id, qualification="demo")

        self.assertEqual(snapshot["lanes"], [])
        self.assertFalse(snapshot["artifactFingerprintComplete"])
        self.assertIn("v2_plan_unavailable", snapshot["warnings"])

    def test_v2_state_cannot_inject_record_identity_omitted_by_plan(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        omitted = (
            "reviewQuestionId",
            "sourceQuestionKey",
            "sourceRecordRef",
            "listGroupId",
        )
        plan_path = self.store.root / "demo" / run_id / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        for execution in plan["plan"]["questionExecutions"]:
            if execution["questionId"] == "q-1":
                for field in omitted:
                    execution.pop(field, None)
        material = {
            key: value for key, value in plan.items() if key != "planHash"
        }
        plan_hash = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        plan["planHash"] = plan_hash
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        parent = dict(self.store.manifests[run_id])
        parent["planHash"] = plan_hash
        self.store.write(parent)
        summary_path = (
            self.store.root / "demo" / run_id / "question_summary.json"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for question in summary["questions"]:
            if question["questionId"] == "q-1":
                for field in omitted:
                    question.pop(field, None)
        summary["planHash"] = plan_hash
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        for state_path in (
            self.store.root / "demo" / run_id / "questions"
        ).glob("*.json"):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["planHash"] = plan_hash
            # Deliberately retain the state-only source identity. It must not
            # become an artifact record binding.
            self._write_v2_question_state(run_id, state)

        payload = self.model.artifacts(run_id, qualification="demo")

        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            {item["reasonCode"] for item in payload["rejected"]},
            {"record_resolution_failed"},
        )

    def test_v2_terminal_without_validated_attempt_is_explicit(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        state_path = (
            self.store.root
            / "demo"
            / run_id
            / "questions"
            / f"{hashlib.sha256(b'q-1').hexdigest()}.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["execution"]["stages"][0]["validationAttempts"] = []
        self._write_v2_question_state(run_id, state)

        snapshot = self.model.snapshot(run_id, qualification="demo")

        self.assertFalse(snapshot["artifactFingerprintComplete"])
        self.assertIn("v2_attempt_unavailable", snapshot["warnings"])

    def test_v2_missing_terminal_fingerprint_is_visible_and_changes_on_terminal(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        summary_path = (
            self.store.root / "demo" / run_id / "question_summary.json"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        stage = summary["questions"][1]["stages"][0]
        stage.pop("outputFingerprint")
        queued = json.loads(json.dumps(summary))
        queued["questions"][1]["stages"][0]["status"] = "queued"
        parent = self.store.manifests[run_id]
        self.assertNotEqual(
            self.model._v2_summary_artifact_fingerprint(parent, queued),
            self.model._v2_summary_artifact_fingerprint(parent, summary),
        )
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        state_path = (
            self.store.root
            / "demo"
            / run_id
            / "questions"
            / f"{hashlib.sha256(b'q-1').hexdigest()}.json"
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["execution"]["stages"][0].pop("outputFingerprint")
        self._write_v2_question_state(run_id, state)

        snapshot = self.model.snapshot(run_id, qualification="demo")
        artifacts = self.model.artifacts(run_id, qualification="demo")

        self.assertIn(
            "v2_output_fingerprint_missing",
            snapshot["warnings"],
        )
        self.assertEqual(len(artifacts["artifacts"]), 1)

    def test_v2_empty_plan_is_rejected_even_when_manifest_count_is_zero(self):
        run_id, _active_attempt, _saved_attempt = self._write_v2_run()
        plan_path = self.store.root / "demo" / run_id / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["plan"]["questionExecutions"] = []
        material = {
            key: value for key, value in plan.items() if key != "planHash"
        }
        plan_hash = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        plan["planHash"] = plan_hash
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        parent = dict(self.store.manifests[run_id])
        parent["planHash"] = plan_hash
        parent["questionStateCount"] = 0
        self.store.write(parent)

        snapshot = self.model.snapshot(run_id, qualification="demo")

        self.assertIn("v2_plan_identity_mismatch", snapshot["warnings"])

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
            "eventCount": 0,
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

    def test_snapshot_drops_non_finite_manifest_numbers(self):
        self.parent["targetCount"] = float("nan")
        self.child["batchIndex"] = float("inf")
        self.store.write(self.parent)
        self.store.write(self.child)

        payload = self.model.snapshot("run-1", qualification="demo")

        self.assertNotIn("targetCount", payload["run"])
        self.assertNotIn("batchIndex", payload["lanes"][0])
        json.dumps(payload, allow_nan=False)

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

    def test_snapshot_response_has_a_hard_byte_limit_with_explicit_warning(self):
        child_ids = [f"large-child-{index:03d}" for index in range(128)]
        self.parent["childRunIds"] = child_ids
        self.store.write(self.parent)
        for index, child_id in enumerate(child_ids):
            self.store.write(
                {
                    "runId": child_id,
                    "parentRunId": "run-1",
                    "qualification": "demo",
                    "status": "running",
                    "listGroupIds": [
                        f"group-{index}-{item}-" + ("x" * 300)
                        for item in range(100)
                    ],
                    "targetGroupIds": [
                        f"target-{index}-{item}-" + ("y" * 300)
                        for item in range(100)
                    ],
                }
            )

        payload = self.model.snapshot("run-1", qualification="demo")
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertTrue(payload["truncated"])
        self.assertIn(
            "snapshot_response_bytes_limit",
            payload["warnings"],
        )
        self.assertLess(len(payload["lanes"]), 128)
        self.assertEqual(payload["lanes"][-1]["runId"], "large-child-127")
        self.assertLessEqual(len(encoded), MAX_MONITOR_RESPONSE_BYTES)

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
        self.assertEqual(payload["observationHealth"]["eventCount"], 6)
        self.assertEqual(
            payload["events"][0]["occurredAt"],
            "2026-07-27T00:00:01Z",
        )
        text = payload["events"][0]["payload"]["text"]
        for formula in ("Q₂/Q₁", "Cu2+/Cu", "/h", "/600=40kPa"):
            self.assertIn(formula, text)
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
        self.assertNotIn("dXNlcjpwYXNz", serialized)
        self.assertNotIn("equal-secret-value", serialized)
        self.assertNotIn("github_pat_", serialized)
        self.assertNotIn("/workspace/project", serialized)
        self.assertNotIn("/tmp/private", serialized)
        self.assertNotIn("/var/private", serialized)
        self.assertNotIn("command", serialized)
        self.assertNotIn("promptPath", serialized)
        self.assertNotIn("rawManifest", serialized)

    def test_gap_payload_preserves_exact_drop_counts(self):
        event = self.model._public_event(
            {
                "eventId": "server:9",
                "serverInstanceId": "server",
                "sequence": 9,
                "observedAt": 9.0,
                "type": "observationGap",
                "correlation": {
                    "qualification": "demo",
                    "runId": "run-1",
                    "affectedRunIds": ["run-1", "child-1"],
                },
                "payload": {
                    "fromSequence": 7,
                    "toSequence": 8,
                    "droppedNotifications": 2,
                    "totalDroppedNotifications": 5,
                    "scopeTruncated": True,
                    "private": "hidden",
                },
            }
        )

        self.assertEqual(
            event["payload"],
            {
                "fromSequence": 7,
                "toSequence": 8,
                "droppedNotifications": 2,
                "totalDroppedNotifications": 5,
                "scopeTruncated": True,
            },
        )
        self.assertEqual(
            event["correlation"]["affectedRunIds"],
            ["run-1", "child-1"],
        )

    def test_event_drops_non_scalar_public_fields_and_non_finite_time(self):
        event = self.model._public_event(
            {
                "eventId": {"private": "NEVER-PUBLIC"},
                "serverInstanceId": ["NEVER-PUBLIC"],
                "sequence": 1,
                "observedAt": float("nan"),
                "type": "agentMessage",
                "correlation": {
                    "runId": {"rawPrompt": "NEVER-PUBLIC"},
                    "threadId": "thread-public",
                },
                "payload": {
                    "text": {"rawReasoning": "NEVER-PUBLIC"},
                    "delta": "公開差分",
                },
            }
        )

        self.assertEqual(event["eventId"], "")
        self.assertEqual(event["serverInstanceId"], "")
        self.assertEqual(event["observedAt"], 0)
        self.assertNotIn("runId", event["correlation"])
        self.assertNotIn("text", event["payload"])
        self.assertEqual(event["payload"]["delta"], "公開差分")
        serialized = json.dumps(event, allow_nan=False)
        self.assertNotIn("NEVER-PUBLIC", serialized)

    def test_events_response_has_aggregate_byte_limit_and_resumable_cursor(self):
        class LargeHub:
            def events(self, *_args, **_kwargs):
                return {
                    "events": [
                        {
                            "eventId": f"server:{index}",
                            "serverInstanceId": "server",
                            "sequence": index,
                            "observedAt": float(index),
                            "type": "plan",
                            "correlation": {
                                "runId": "run-1",
                                "questionIds": [
                                    f"q-{item}-" + ("q" * 300)
                                    for item in range(200)
                                ],
                                "workItemKeys": [
                                    f"w-{item}-" + ("w" * 300)
                                    for item in range(200)
                                ],
                                "listGroupIds": [
                                    f"g-{item}-" + ("g" * 300)
                                    for item in range(200)
                                ],
                                "affectedRunIds": [
                                    f"r-{item}-" + ("r" * 300)
                                    for item in range(200)
                                ],
                            },
                            "payload": {
                                "plan": [
                                    {
                                        "step": "x" * 4096,
                                        "status": "inProgress",
                                    }
                                    for _item in range(64)
                                ]
                            },
                        }
                        for index in range(1, 21)
                    ],
                    "cursor": "server:20",
                    "observation": {
                        "eventCount": 20,
                        "droppedNotifications": 0,
                        "diskFailures": 0,
                    },
                }

        model = MonitorReadModel(self.root, self.store, LargeHub())
        payload = model.events(
            "run-1",
            qualification="demo",
            limit=500,
        )
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertTrue(payload["truncated"])
        self.assertLess(len(payload["events"]), 20)
        self.assertNotEqual(payload["cursor"], "server:20")
        self.assertLessEqual(len(encoded), MAX_EVENT_TOTAL_BYTES)

    def test_non_mapping_event_iterable_is_consumed_only_to_limit_plus_one(self):
        class GeneratorHub:
            consumed = 0

            def read_events(self, *_args, **_kwargs):
                def generate():
                    for index in range(10_000):
                        self.consumed += 1
                        yield {
                            "eventId": f"server:{index + 1}",
                            "serverInstanceId": "server",
                            "sequence": index + 1,
                            "observedAt": float(index),
                            "type": "agentMessage",
                            "payload": {"text": f"message-{index}"},
                        }

                return generate()

        hub = GeneratorHub()
        model = MonitorReadModel(self.root, self.store, hub)

        payload = model.events(
            "run-1",
            qualification="demo",
            limit=3,
        )

        self.assertEqual(hub.consumed, 4)
        self.assertEqual(len(payload["events"]), 3)
        self.assertEqual(payload["cursor"], "server:3")
        self.assertTrue(payload["truncated"])

    def test_list_fallback_is_strictly_read_only(self):
        manifest_paths = sorted(self.store.root.rglob("manifest.json"))
        before = {
            path: (path.stat().st_mtime_ns, path.read_bytes())
            for path in manifest_paths
        }

        payload = self.model.runs("demo")

        self.assertEqual(self.store.dashboard_calls, 0)
        self.assertEqual(payload["runs"][0]["runId"], "run-1")
        self.assertFalse(
            (self.store.root / "demo" / "dashboard_runs.json").exists()
        )
        self.assertFalse(
            any(self.store.root.rglob("list_summary.json"))
        )
        after = {
            path: (path.stat().st_mtime_ns, path.read_bytes())
            for path in manifest_paths
        }
        self.assertEqual(after, before)

    def test_manifest_swap_cannot_change_projection_after_secure_open(self):
        manifest_path = (
            self.store.root / "demo" / "run-1" / "manifest.json"
        )
        evil_path = self.root / "evil-manifest.json"
        evil_path.write_text(
            json.dumps(
                {
                    "runId": "run-1",
                    "qualification": "demo",
                    "status": "running",
                    "changedFiles": ["output/demo/not-declared.txt"],
                    "private": "TOP-SECRET",
                }
            ),
            encoding="utf-8",
        )
        real_open = os.open
        swapped = False

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "list_summary.json" and not swapped:
                swapped = True
                manifest_path.unlink()
                manifest_path.symlink_to(evil_path)
            return real_open(path, flags, *args, **kwargs)

        with patch(
            "tools.question_review_console.monitor_service.os.open",
            side_effect=swapping_open,
        ):
            with self.assertRaises(ValueError):
                self.model._read_manifest_projection(manifest_path)

        self.assertTrue(swapped)

    def test_manifest_internal_identity_must_match_requested_path(self):
        manifest_path = (
            self.store.root / "demo" / "run-1" / "manifest.json"
        )
        manifest_path.write_text(
            json.dumps(
                {
                    **self.parent,
                    "runId": "different",
                    "qualification": "other",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            self.model.snapshot("run-1", qualification="demo")

    def test_child_identity_mismatch_is_omitted_and_explicit(self):
        child_path = (
            self.store.root / "demo" / "child-1" / "manifest.json"
        )
        child_path.write_text(
            json.dumps({**self.child, "runId": "different-child"}),
            encoding="utf-8",
        )

        snapshot = self.model.snapshot("run-1", qualification="demo")
        artifacts = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(snapshot["lanes"], [])
        self.assertIn(
            "child_manifest_identity_mismatch",
            snapshot["warnings"],
        )
        self.assertIn(
            "child_manifest_identity_mismatch",
            {item["reasonCode"] for item in artifacts["rejected"]},
        )
        self.assertTrue(artifacts["truncated"])

    def test_invalid_child_manifest_list_and_ids_are_explicit(self):
        self.parent["childRunIds"] = {"child-1": True}
        self.store.write(self.parent)

        invalid_schema = self.model.snapshot(
            "run-1",
            qualification="demo",
        )
        self.assertIn(
            "child_manifest_schema_invalid",
            invalid_schema["warnings"],
        )
        self.assertTrue(invalid_schema["truncated"])

        self.parent["childRunIds"] = ["../outside", "child-1"]
        self.store.write(self.parent)
        invalid_id = self.model.artifacts(
            "run-1",
            qualification="demo",
        )
        self.assertIn(
            "child_manifest_id_invalid",
            {item["reasonCode"] for item in invalid_id["rejected"]},
        )
        self.assertTrue(invalid_id["truncated"])

    def test_oversized_dashboard_index_falls_back_without_parsing_it(self):
        index_path = self.store.root / "demo" / "dashboard_runs.json"
        index_path.write_text("x" * 1024, encoding="utf-8")

        with patch(
            "tools.question_review_console.monitor_service."
            "MAX_DASHBOARD_INDEX_BYTES",
            64,
        ):
            payload = self.model.runs("demo")

        self.assertEqual(payload["runs"][0]["runId"], "run-1")
        self.assertEqual(self.store.dashboard_calls, 0)

    def test_dashboard_index_ghost_run_is_rejected_and_fallback_is_explicit(self):
        index_path = self.store.root / "demo" / "dashboard_runs.json"
        index_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "qualification-dashboard-run-index/v1",
                    "qualification": "demo",
                    "complete": True,
                    "runs": [
                        {
                            "runId": "ghost-run",
                            "qualification": "demo",
                            "status": "completed",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        payload = self.model.runs("demo")

        run_ids = {run["runId"] for run in payload["runs"]}
        self.assertNotIn("ghost-run", run_ids)
        self.assertIn("run-1", run_ids)
        self.assertTrue(payload["truncated"])

    def test_dashboard_response_has_a_hard_byte_limit(self):
        for index in range(80):
            self.store.write(
                {
                    "runId": f"large-run-{index:03d}",
                    "qualification": "demo",
                    "status": "completed",
                    "listGroupIds": [
                        f"group-{index}-{item}-" + ("x" * 300)
                        for item in range(100)
                    ],
                    "targetGroupIds": [
                        f"target-{index}-{item}-" + ("y" * 300)
                        for item in range(100)
                    ],
                }
            )

        payload = self.model.runs("demo", limit=500)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertTrue(payload["truncated"])
        self.assertLessEqual(len(encoded), MAX_MONITOR_RESPONSE_BYTES)

    def test_dashboard_fallback_scan_limit_is_explicit(self):
        for index in range(4):
            self.store.write(
                {
                    "runId": f"run-extra-{index}",
                    "qualification": "demo",
                    "status": "completed",
                }
            )

        with patch(
            "tools.question_review_console.monitor_service."
            "MAX_DASHBOARD_SCAN_ENTRIES",
            2,
        ):
            payload = self.model.runs("demo")

        self.assertTrue(payload["truncated"])

    def test_dashboard_result_limit_is_explicit(self):
        self.store.write(
            {
                "runId": "second-parent",
                "qualification": "demo",
                "status": "completed",
            }
        )

        payload = self.model.runs("demo", limit=1)

        self.assertEqual(len(payload["runs"]), 1)
        self.assertTrue(payload["truncated"])

    def test_dashboard_manifest_reads_have_aggregate_byte_budget(self):
        for index in range(6):
            self.store.write(
                {
                    "runId": f"budget-run-{index}",
                    "qualification": "demo",
                    "status": "completed",
                    "padding": "x" * 180,
                }
            )

        with patch(
            "tools.question_review_console.monitor_service."
            "MAX_MANIFEST_COLLECTION_BYTES",
            512,
        ):
            payload = self.model.runs("demo", limit=100)

        self.assertTrue(payload["truncated"])
        self.assertLess(len(payload["runs"]), 6)

    def test_unqualified_run_lookup_stops_at_aggregate_byte_budget(self):
        for index in range(3):
            path = (
                self.store.root
                / f"qualification-{index}"
                / "shared-run"
                / "manifest.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text("{" + ("x" * 200), encoding="utf-8")

        with patch(
            "tools.question_review_console.monitor_service."
            "MAX_MANIFEST_COLLECTION_BYTES",
            300,
        ):
            with self.assertRaisesRegex(ValueError, "qualification"):
                self.model.snapshot("shared-run")

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

    def test_batch_declaration_after_256_empty_items_is_not_lost(self):
        self.child["batchQuestionResults"] = [
            {"questionId": f"empty-{index}", "changedFiles": []}
            for index in range(257)
        ] + [self.child["batchQuestionResults"][0]]
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(len(payload["artifacts"]), 1)
        self.assertEqual(
            payload["artifacts"][0]["identity"]["questionId"],
            "q-1",
        )

    def test_record_binding_after_256_candidates_is_resolved(self):
        exact = self.parent["targetRecordBindings"][0]
        self.child["targetRecordBindings"] = [
            {
                "uiQuestionId": f"other-{index}",
                "sourceQuestionKey": f"demo:2026:other-{index}",
                "sourceRecordRef": f"source.json#{index + 1}",
            }
            for index in range(257)
        ] + [exact]
        self.child["progressTargets"] = []
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(len(payload["artifacts"]), 1)
        self.assertEqual(
            payload["artifacts"][0]["identity"]["sourceRecordRef"],
            "source.json#0",
        )

    def test_unattributed_shared_json_is_explicitly_rejected(self):
        extra_relative = "output/demo/questions_json/2026/shared.json"
        extra = self.root / extra_relative
        extra.write_text('{"private":"must-not-render"}', encoding="utf-8")
        self.child["result"]["changedFiles"].append(extra_relative)
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        rejection = next(
            item
            for item in payload["rejected"]
            if item["path"] == extra_relative
        )
        self.assertEqual(
            rejection["reasonCode"],
            "question_attribution_required",
        )
        self.assertNotIn("must-not-render", json.dumps(payload))

    def test_malformed_batch_results_reject_shared_json(self):
        self.child["batchQuestionResults"] = {"corrupt": True}
        self.child["result"]["changedFiles"] = [self.artifact_relative]
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "question_attribution_required",
        )
        self.assertNotIn("別問題", json.dumps(payload, ensure_ascii=False))

    def test_child_direct_json_uses_manifest_question_identity(self):
        self.child["batchQuestionResults"] = []
        self.child["result"]["changedFiles"] = [self.artifact_relative]
        self.store.write(self.child)

        payload = self.model.artifacts("child-1", qualification="demo")

        self.assertEqual(len(payload["artifacts"]), 1)
        self.assertIn("保存済み", payload["artifacts"][0]["content"])
        self.assertNotIn("別問題", payload["artifacts"][0]["content"])

    def test_malformed_aliases_fail_closed_without_api_exception(self):
        malformed = dict(self.child["targetRecordBindings"][0])
        malformed["uiQuestionId"] = "different"
        malformed["reviewQuestionId"] = "different-review"
        malformed["aliases"] = {"q-1": True}
        self.child["targetRecordBindings"] = [malformed]
        self.child["progressTargets"] = []
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "record_resolution_failed",
        )

    def test_hardlinked_artifact_is_rejected(self):
        artifact = self.root / self.artifact_relative
        hardlink = self.root / "artifact-hardlink"
        os.link(artifact, hardlink)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "hardlink_not_allowed",
        )

    def test_artifact_changed_in_place_during_read_is_rejected(self):
        relative = "output/demo/questions_json/2026/changing.txt"
        artifact = self.root / relative
        artifact.write_bytes(b"A" * 100_000)
        original_stat = artifact.stat()
        self.child["batchQuestionResults"] = []
        self.child["result"]["changedFiles"] = [relative]
        self.store.write(self.child)
        real_read = os.read
        mutated = False

        def mutating_read(descriptor, size):
            nonlocal mutated
            data = real_read(descriptor, size)
            if (
                data
                and not mutated
                and os.fstat(descriptor).st_ino == original_stat.st_ino
            ):
                mutated = True
                with artifact.open("r+b") as handle:
                    handle.write(b"B" * 100_000)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.utime(
                    artifact,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
            return data

        with patch(
            "tools.question_review_console.monitor_service.os.read",
            side_effect=mutating_read,
        ):
            payload = self.model.artifacts("run-1", qualification="demo")

        self.assertTrue(mutated)
        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "file_changed_during_read",
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

    def test_shared_json_patch_requires_every_declared_record_identity_field(self):
        artifact = self.root / self.artifact_relative
        target = {
            "questionId": "q-1",
            "sourceQuestionKey": "demo:2026:q-1",
            "sourceRecordRef": "source.json#0",
            "reviewQuestionId": "review-q-1",
            "explanationText": "保存済み",
        }
        for field in (
            "sourceQuestionKey",
            "sourceRecordRef",
            "reviewQuestionId",
        ):
            for mutation in ("missing", "mismatch"):
                with self.subTest(field=field, mutation=mutation):
                    record = dict(target)
                    if mutation == "missing":
                        record.pop(field)
                    else:
                        record[field] = f"wrong-{field}"
                    artifact.write_text(
                        json.dumps({"question_bodies": [record]}),
                        encoding="utf-8",
                    )

                    payload = self.model.artifacts(
                        "run-1", qualification="demo"
                    )

                    self.assertEqual(payload["artifacts"], [])
                    self.assertEqual(
                        payload["rejected"][0]["reasonCode"],
                        "record_resolution_failed",
                    )

        artifact.write_text(
            json.dumps({"question_bodies": [target, dict(target)]}),
            encoding="utf-8",
        )
        payload = self.model.artifacts("run-1", qualification="demo")
        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "record_resolution_failed",
        )

    def test_pretty_print_expansion_over_limit_is_rejected(self):
        artifact = self.root / self.artifact_relative
        artifact.write_text(
            json.dumps(
                {
                    "question_bodies": [
                        {
                            "questionId": "q-1",
                            "sourceQuestionKey": "demo:2026:q-1",
                            "sourceRecordRef": "source.json#0",
                            "reviewQuestionId": "review-q-1",
                            "padding": [0] * 160_000,
                        }
                    ]
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        self.assertLess(artifact.stat().st_size, MAX_ARTIFACT_BYTES)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "rendered_record_bytes_limit",
        )

    def test_shared_json_patch_still_requires_source_key_or_record_ref(self):
        review_only = {
            "id": "q-1",
            "uiQuestionId": "q-1",
            "reviewQuestionId": "review-q-1",
        }
        self.child["targetRecordBindings"] = [review_only]
        self.child["progressTargets"] = [review_only]
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertEqual(payload["artifacts"], [])
        self.assertEqual(
            payload["rejected"][0]["reasonCode"],
            "record_resolution_failed",
        )

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

    def test_plain_text_artifact_redacts_credentials_and_paths_not_formulas(self):
        relative = "output/demo/questions_json/2026/public-monitor.txt"
        artifact = self.root / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            "Q₂/Q₁ Cu2+/Cu /h /600=40kPa "
            "/Users/person/private /tmp/private /var/private "
            "cwd:/workspace/project "
            "Authorization: Basic dXNlcjpwYXNz "
            "Authorization = Bearer equal-secret-value "
            "github_pat_abcdefghijklmnop",
            encoding="utf-8",
        )
        self.child["batchQuestionResults"] = []
        self.child["result"]["changedFiles"] = [relative]
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        content = payload["artifacts"][0]["content"]
        for formula in ("Q₂/Q₁", "Cu2+/Cu", "/h", "/600=40kPa"):
            self.assertIn(formula, content)
        for private in (
            "/Users/person/private",
            "/tmp/private",
            "/var/private",
            "/workspace/project",
            "dXNlcjpwYXNz",
            "equal-secret-value",
            "github_pat_",
        ):
            self.assertNotIn(private, content)
        self.assertIn("<absolute-path>", content)
        self.assertIn("<redacted>", content)

    def test_cookie_header_is_redacted_to_line_end_only(self):
        public = MonitorReadModel._text(
            "Cookie: a=secret, b=second-secret\nQ₂/Q₁ Cu2+/Cu",
            1000,
        )

        self.assertNotIn("secret", public)
        self.assertIn("<redacted>", public)
        self.assertIn("Q₂/Q₁", public)
        self.assertIn("Cu2+/Cu", public)

    def test_text_redacts_complete_secret_before_applying_display_limit(self):
        source = "prefix github_pat_abcdefghijklmnop suffix"
        limit = len("prefix github_pat_abc")

        public = MonitorReadModel._text(source, limit)

        self.assertNotIn("github_pat_", public)
        self.assertNotIn("abcdefghijklmnop", public)
        self.assertLessEqual(len(public), limit)

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
            (self.root / relative).write_text(
                "/tmp/" + ("x" * (chunk_bytes - len("/tmp/"))),
                encoding="utf-8",
            )
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
        self.assertTrue(payload["truncated"])
        self.assertTrue(
            all(
                item.get("truncated") is True
                for item in payload["rejected"]
                if item.get("reasonCode") == "total_bytes_limit"
            )
        )
        self.assertLessEqual(
            len(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            MAX_ARTIFACT_TOTAL_BYTES,
        )

    def test_duplicate_declarations_are_truncated_to_compact_http_payload_limit(self):
        relative = "output/demo/questions_json/2026/repeated.txt"
        repeated = self.root / relative
        repeated.parent.mkdir(parents=True, exist_ok=True)
        repeated.write_text("x" * 700_000, encoding="utf-8")
        self.child["batchQuestionResults"] = [
            {
                "questionId": "q-1",
                "status": "succeeded",
                "changedFiles": [relative],
            }
            for _index in range(12)
        ]
        self.child["result"]["changedFiles"] = [relative]
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")
        compact = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertTrue(payload["truncated"])
        self.assertLessEqual(len(compact), MAX_ARTIFACT_TOTAL_BYTES)
        self.assertLess(len(payload["artifacts"]), 12)
        compact_rejections = [
            item
            for item in payload["rejected"]
            if item.get("reasonCode") == "response_bytes_limit"
        ]
        self.assertEqual(len(compact_rejections), 1)
        self.assertTrue(compact_rejections[0]["truncated"])
        self.assertEqual(
            {item["size"] for item in payload["artifacts"]},
            {700_000},
        )

    def test_declaration_limit_is_explicit_in_response(self):
        relative = "output/demo/questions_json/2026/repeated-small.txt"
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("公開", encoding="utf-8")
        self.child["batchQuestionResults"] = [
            {
                "questionId": f"q-{index}",
                "status": "succeeded",
                "changedFiles": [relative],
            }
            for index in range(258)
        ]
        self.child["result"]["changedFiles"] = [relative]
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertTrue(payload["truncated"])
        self.assertEqual(len(payload["artifacts"]), 256)
        self.assertIn(
            "declaration_limit",
            {item["reasonCode"] for item in payload["rejected"]},
        )

    def test_artifacts_include_oldest_of_129_children_without_silent_cutoff(self):
        relative = "output/demo/questions_json/2026/oldest-child.txt"
        (self.root / relative).write_text("最古batch成果物", encoding="utf-8")
        child_ids = [f"artifact-child-{index:03d}" for index in range(129)]
        self.parent["childRunIds"] = child_ids
        self.store.write(self.parent)
        for index, child_id in enumerate(child_ids):
            changed_files = [relative] if index == 0 else []
            self.store.write(
                {
                    "runId": child_id,
                    "parentRunId": "run-1",
                    "qualification": "demo",
                    "status": "completed",
                    "receiptValidated": True,
                    "artifactSync": {"status": "succeeded"},
                    "questionId": f"q-{index}",
                    "result": {
                        "status": "succeeded",
                        "changedFiles": changed_files,
                    },
                }
            )

        first_snapshot = self.model.snapshot("run-1", qualification="demo")
        (self.root / relative).write_text(
            "最古batch成果物・更新",
            encoding="utf-8",
        )
        changed_snapshot = self.model.snapshot(
            "run-1",
            qualification="demo",
        )
        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertIn(
            relative,
            {item["path"] for item in payload["artifacts"]},
        )
        self.assertNotEqual(
            first_snapshot["artifactFingerprint"],
            changed_snapshot["artifactFingerprint"],
        )
        self.assertNotIn(
            "child_manifest_limit",
            {item["reasonCode"] for item in payload["rejected"]},
        )

    def test_oversized_child_manifest_is_explicitly_rejected(self):
        self.child["privateBlob"] = "x" * (8 * 1024 * 1024)
        self.store.write(self.child)

        payload = self.model.artifacts("run-1", qualification="demo")

        self.assertTrue(payload["truncated"])
        self.assertIn(
            "child_manifest_bytes_limit",
            {item["reasonCode"] for item in payload["rejected"]},
        )

    def test_non_object_child_manifests_count_toward_aggregate_budget(self):
        child_ids = [f"invalid-child-{index}" for index in range(3)]
        self.parent["childRunIds"] = child_ids
        self.store.write(self.parent)
        for child_id in child_ids:
            path = (
                self.store.root
                / "demo"
                / child_id
                / "manifest.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps("x" * 180),
                encoding="utf-8",
            )

        with patch(
            "tools.question_review_console.monitor_service."
            "MAX_MANIFEST_COLLECTION_BYTES",
            300,
        ):
            payload = self.model.snapshot("run-1", qualification="demo")

        self.assertTrue(payload["truncated"])
        self.assertIn("child_manifest_bytes_limit", payload["warnings"])

    def test_artifact_fingerprint_ignores_lane_progress_but_tracks_content(self):
        first = self.model.snapshot("run-1", qualification="demo")
        self.child["status"] = "waiting"
        self.child["startedAt"] = "2026-07-27T00:00:01Z"
        self.child["finishedAt"] = "2026-07-27T00:00:02Z"
        self.store.write(self.child)
        progressed = self.model.snapshot("run-1", qualification="demo")

        self.assertEqual(
            first["artifactFingerprint"],
            progressed["artifactFingerprint"],
        )

        artifact = self.root / self.artifact_relative
        content = json.loads(artifact.read_text(encoding="utf-8"))
        content["question_bodies"][0]["explanationText"] = "更新済み"
        artifact.write_text(json.dumps(content), encoding="utf-8")
        changed = self.model.snapshot("run-1", qualification="demo")

        self.assertNotEqual(
            progressed["artifactFingerprint"],
            changed["artifactFingerprint"],
        )

    def test_child_fingerprint_tracks_parent_artifact_sync(self):
        first = self.model.snapshot("child-1", qualification="demo")
        self.parent["artifactSync"] = {"status": "failed"}
        self.store.write(self.parent)

        changed = self.model.snapshot("child-1", qualification="demo")

        self.assertNotEqual(
            first["artifactFingerprint"],
            changed["artifactFingerprint"],
        )
        payload = self.model.artifacts("child-1", qualification="demo")
        self.assertEqual(
            payload["artifacts"][0]["artifactSync"]["parentStatus"],
            "failed",
        )

    def test_fingerprint_tracks_record_binding_that_changes_public_record(self):
        first_snapshot = self.model.snapshot(
            "run-1",
            qualification="demo",
        )
        first_artifact = self.model.artifacts(
            "run-1",
            qualification="demo",
        )
        self.assertIn("保存済み", first_artifact["artifacts"][0]["content"])

        rebound = {
            "uiQuestionId": "q-1",
            "sourceQuestionKey": "demo:2026:other-0",
            "sourceRecordRef": "source.json#1",
            "aliases": ["q-1"],
        }
        self.child["targetRecordBindings"] = [rebound]
        self.child["progressTargets"] = [rebound]
        self.store.write(self.child)

        changed_snapshot = self.model.snapshot(
            "run-1",
            qualification="demo",
        )
        changed_artifact = self.model.artifacts(
            "run-1",
            qualification="demo",
        )

        self.assertNotEqual(
            first_snapshot["artifactFingerprint"],
            changed_snapshot["artifactFingerprint"],
        )
        self.assertIn(
            "別問題",
            changed_artifact["artifacts"][0]["content"],
        )

    def test_fingerprint_ignores_private_artifact_sync_message(self):
        first = self.model.snapshot("run-1", qualification="demo")
        self.child["artifactSync"]["message"] = "different private detail"
        self.store.write(self.child)

        changed = self.model.snapshot("run-1", qualification="demo")

        self.assertEqual(
            first["artifactFingerprint"],
            changed["artifactFingerprint"],
        )

    def test_fingerprint_ignores_result_timestamps_without_public_artifact_change(self):
        first = self.model.snapshot("run-1", qualification="demo")
        self.child["result"]["updatedAt"] = "2026-07-27T01:02:03Z"
        self.child["batchQuestionResults"][0][
            "updatedAt"
        ] = "2026-07-27T01:02:03Z"
        self.store.write(self.child)

        changed = self.model.snapshot("run-1", qualification="demo")

        self.assertEqual(
            first["artifactFingerprint"],
            changed["artifactFingerprint"],
        )

    def test_missing_run_and_path_traversal_are_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.model.snapshot("missing", qualification="demo")
        with self.assertRaises(ValueError):
            self.model.snapshot("../run-1")


if __name__ == "__main__":
    unittest.main()
