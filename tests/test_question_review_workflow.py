import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from scripts.upload.upload_questions_to_firestore import build_doc_data_base
from tools.question_review_console.jobs import JobConflictError, JobManager
from tools.question_review_console.publisher import GroupPublisher
from tools.question_review_console.workflow_runner import (
    ArtifactSynchronizer,
    WorkflowError,
    sync_after_patch_update,
)
from tools.question_review_console.work_versions import QuestionWorkVersionStore


class FakeInventory:
    def __init__(self, group):
        self.payload = group

    def group(self, qualification, list_group_id):
        if qualification != self.payload["qualification"]:
            raise FileNotFoundError(qualification)
        if list_group_id != self.payload["listGroupId"]:
            raise FileNotFoundError(list_group_id)
        return self.payload

    def invalidate(self, qualification, list_group_id):
        return None


class FakeFirestore:
    def __init__(self, documents=None):
        self.documents = documents or {}

    def read_documents(self, document_ids, *, fields=None):
        return {
            question_id: copy.deepcopy(self.documents[question_id])
            for question_id in document_ids
            if question_id in self.documents
        }


def group_payload(workflow, *, upload_path=None, issue_codes=None):
    return {
        "qualification": "sample-exam",
        "listGroupId": "2026",
        "fingerprint": "fingerprint-1",
        "questions": [
            {
                "workflow": dict(workflow),
                "issueCodes": list(issue_codes or []),
                "paths": {"uploadReady": upload_path},
            }
        ],
    }


def upload_document():
    return {
        "questionId": "doc-1",
        "questionSetId": "set-1",
        "listGroupId": "2026",
        "originalQuestionId": "source-q1",
        "originalQuestionBodyText": "正しいものはどれか。",
        "originalQuestionChoiceText": "選択肢A",
        "questionText": "選択肢A",
        "questionType": "true_false",
        "qualificationId": "sample-exam",
        "correctChoiceText": "正しい",
        "explanationText": "正しい。根拠がある。",
        "questionTags": [],
        "examYear": 2026,
        "examSource": "サンプル試験",
        "isOfficial": True,
        "isDeleted": False,
        "isChoiceOnly": False,
        "isGroupable": False,
    }


def sync_group_fixture(root: Path, *, is_law_related: bool):
    source_dir = (
        root
        / "output"
        / "sample-exam"
        / "questions_json"
        / "2026"
        / "00_source"
    )
    source_dir.mkdir(parents=True)
    (source_dir / "question.json").write_text("{}", encoding="utf-8")
    group = group_payload(
        {"merge": "stale", "convert": "stale", "upload": "missing"}
    )
    group["questions"][0]["projected"] = {
        "answer_result_text": "正しい",
        "choiceTextList": ["A"],
        "correctChoiceText": ["正しい"],
        "isLawRelated": is_law_related,
    }
    if is_law_related:
        group["questions"][0]["projected"]["lawRevisionFacts"] = [
            {"current": {"correctChoiceText": "正しい"}}
        ]
    group["questions"][0].update(
        {
            "id": "question-1",
            "reviewKey": "sample-exam:2026:question-1",
            "qualification": "sample-exam",
            "listGroupId": "2026",
            "originalQuestionId": "source-q1",
        }
    )
    return group


def record_law_audit_version(root: Path, group, version: str, *, questions=None):
    QuestionWorkVersionStore(root).record_stage(
        list(questions or group["questions"]),
        {
            "id": "law_audit",
            "policyVersion": "2.0",
            "policyFingerprint": "law-audit-policy",
        },
        run_id="law-audit-run" if version != "0.0" else None,
        source=(
            "validated_run"
            if version != "0.0"
            else "firestore_published_backfill"
        ),
        version=version,
    )


def mark_group_synced(group):
    for question in group["questions"]:
        for stage in ("merge", "convert", "upload"):
            question["workflow"][stage] = "match"
    group["fingerprint"] = "fingerprint-2"


class ArtifactSynchronizerTests(unittest.TestCase):
    def test_automatically_syncs_stale_artifacts_after_patch_update(self):
        class Synchronizer:
            def __init__(self):
                self.calls = []

            def preview(self, qualification, list_group_id, *, force=False):
                return {
                    "qualification": qualification,
                    "listGroupId": list_group_id,
                    "needsSync": force,
                    "canSync": True,
                    "requiredFieldWarnings": [],
                    "failedDeltaPaths": [],
                    "previewToken": "token",
                }

            def run(
                self, qualification, list_group_id, token, emit, *, force=False
            ):
                self.calls.append((qualification, list_group_id, token, force))
                emit("pipeline complete")
                return {"message": "同期しました。"}

        synchronizer = Synchronizer()
        logs = []

        result = sync_after_patch_update(
            synchronizer, "sample-exam", "2026", logs.append
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            synchronizer.calls,
            [("sample-exam", "2026", "token", True)],
        )
        self.assertIn("2026: 最新patchから公開用データを自動更新します。", logs)

    def test_keeps_validated_patch_when_automatic_sync_is_blocked(self):
        class Synchronizer:
            def preview(self, qualification, list_group_id, *, force=False):
                return {
                    "needsSync": True,
                    "canSync": False,
                    "requiredFieldWarnings": [{"questionId": "q1"}],
                    "failedDeltaPaths": [],
                    "previewToken": "token",
                }

            def run(self, *args, **kwargs):
                raise AssertionError("blocked sync must not run")

        result = sync_after_patch_update(
            Synchronizer(), "sample-exam", "2026", lambda _: None
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("必須field不足", result["message"])

    def test_does_not_run_when_preview_disallows_automatic_sync(self):
        class Synchronizer:
            def preview(self, qualification, list_group_id, *, force=False):
                return {
                    "needsSync": True,
                    "canSync": False,
                    "requiredFieldWarnings": [],
                    "failedDeltaPaths": [],
                    "previewToken": "token",
                }

            def run(self, *args, **kwargs):
                raise AssertionError("blocked sync must not run")

        result = sync_after_patch_update(
            Synchronizer(), "sample-exam", "2026", lambda _: None
        )

        self.assertEqual(result["status"], "blocked")

    def test_blocks_sync_when_failed_run_left_an_unverified_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            source_dir.mkdir(parents=True)
            (source_dir / "question.json").write_text("{}", encoding="utf-8")
            changed_path = Path(
                "output/sample-exam/questions_json/2026/"
                "21_explanationText_added/partial.json"
            )
            absolute_changed_path = root / changed_path
            absolute_changed_path.parent.mkdir(parents=True)
            absolute_changed_path.write_text("{}", encoding="utf-8")
            manifest_path = (
                root
                / "output"
                / "question_review_console"
                / "workflow_runs"
                / "sample-exam"
                / "20260101-run"
                / "manifest.json"
            )
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "result": {"changedFiles": [changed_path.as_posix()]},
                    }
                ),
                encoding="utf-8",
            )
            group = group_payload(
                {"merge": "stale", "convert": "stale", "upload": "missing"}
            )
            group["questions"][0]["projected"] = {
                "answer_result_text": "正しい",
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
            }
            commands = []
            synchronizer = ArtifactSynchronizer(
                root,
                FakeInventory(group),
                "secret",
                command_runner=lambda *args, **kwargs: commands.append(args) or 0,
            )

            preview = synchronizer.preview("sample-exam", "2026")

            self.assertFalse(preview["canSync"])
            self.assertEqual(preview["failedDeltaPaths"], [changed_path.as_posix()])
            with self.assertRaisesRegex(WorkflowError, "未確定patch"):
                synchronizer.run(
                    "sample-exam",
                    "2026",
                    preview["previewToken"],
                    lambda _: None,
                )
            self.assertEqual(commands, [])

    def test_blocks_patch_propagation_when_required_fields_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            source_dir.mkdir(parents=True)
            (source_dir / "question.json").write_text("{}", encoding="utf-8")
            group = group_payload(
                {"merge": "stale", "convert": "stale", "upload": "missing"}
            )
            group["questions"][0].update(
                {
                    "id": "review-1",
                    "sourceQuestionKey": "sample:2026:q01",
                    "issues": [
                        {
                            "code": "required_field_missing",
                            "detail": "questionTypeがありません。",
                            "fields": ["questionType"],
                        }
                    ],
                    "projected": {
                        "answer_result_text": "正しい",
                        "choiceTextList": ["A"],
                        "correctChoiceText": ["正しい"],
                    },
                }
            )
            synchronizer = ArtifactSynchronizer(root, FakeInventory(group), "secret")
            preview = synchronizer.preview("sample-exam", "2026")

            with self.assertRaises(WorkflowError):
                synchronizer.run(
                    "sample-exam",
                    "2026",
                    preview["previewToken"],
                    lambda _: None,
                )

        self.assertFalse(preview["canSync"])
        self.assertEqual(len(preview["requiredFieldWarnings"]), 1)

    def test_missing_answer_result_is_not_allowed_with_incomplete_verdicts(self):
        group = group_payload(
            {"merge": "stale", "convert": "stale", "upload": "missing"}
        )
        group["questions"][0]["projected"] = {
            "answer_result_text": "",
            "choiceTextList": ["A", "B"],
            "correctChoiceText": ["正しい", None],
        }

        self.assertFalse(ArtifactSynchronizer._allow_missing_answer_result(group))

    def test_runs_scoped_pipeline_with_upload_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            source_dir.mkdir(parents=True)
            (source_dir / "question.json").write_text("{}", encoding="utf-8")
            group = group_payload(
                {"merge": "stale", "convert": "stale", "upload": "missing"}
            )
            group["questions"][0]["projected"] = {
                "answer_result_text": "",
                "choiceTextList": ["A", "B"],
                "correctChoiceText": ["正しい", "間違い"],
            }
            inventory = FakeInventory(group)
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                emit("pipeline complete")
                for stage in ("merge", "convert", "upload"):
                    group["questions"][0]["workflow"][stage] = "match"
                group["fingerprint"] = "fingerprint-2"
                return 0

            synchronizer = ArtifactSynchronizer(
                root, inventory, "secret", command_runner=run
            )
            preview = synchronizer.preview("sample-exam", "2026")
            result = synchronizer.run(
                "sample-exam", "2026", preview["previewToken"], lambda _: None
            )

        self.assertTrue(result["localReady"])
        self.assertEqual(commands[0][2], "2026")
        self.assertIn("--skip-update-category-counts", commands[0])
        self.assertIn("--upload-dry-run", commands[0])
        self.assertIn("--allow-missing-answer-result", commands[0])
        self.assertEqual(len(commands), 1)

    def test_runs_strict_law_validation_after_upload_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = sync_group_fixture(root, is_law_related=True)
            record_law_audit_version(root, group, "2.0")
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                if command[1].endswith("prepare_firestore_upload.py"):
                    mark_group_synced(group)
                return 0

            synchronizer = ArtifactSynchronizer(
                root, FakeInventory(group), "secret", command_runner=run
            )
            preview = synchronizer.preview("sample-exam", "2026")
            result = synchronizer.run(
                "sample-exam", "2026", preview["previewToken"], lambda _: None
            )

        self.assertTrue(result["localReady"])
        self.assertEqual(len(commands), 3)
        self.assertTrue(commands[0][1].endswith("prepare_firestore_upload.py"))
        self.assertTrue(
            commands[1][1].endswith("check_law_revision_fact_coverage.py")
        )
        self.assertEqual(
            [
                commands[1][commands[1].index("--stage") + 1],
                commands[2][commands[2].index("--stage") + 1],
            ],
            ["merged", "firestore"],
        )
        for command in commands[1:]:
            self.assertIn("--require-all-law-related", command)
            self.assertIn("--fail-on-hold", command)
            self.assertIn("--require-evidence-summary", command)
            self.assertIn("--require-law-references", command)
            self.assertIn("--require-current-correct-choice", command)
            self.assertIn("--require-verified-law-references", command)
            self.assertIn("--require-public-law-evidence", command)
            self.assertEqual(
                command[command.index("--original-question-id") + 1],
                "source-q1",
            )

    def test_blocks_current_law_audit_mismatch_before_artifact_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = sync_group_fixture(root, is_law_related=True)
            record_law_audit_version(root, group, "2.0")
            projected = group["questions"][0]["projected"]
            projected["correctChoiceText"] = ["間違い"]
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                return 0

            synchronizer = ArtifactSynchronizer(
                root, FakeInventory(group), "secret", command_runner=run
            )

            result = sync_after_patch_update(
                synchronizer,
                "sample-exam",
                "2026",
                lambda _: None,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("一致しません", result["message"])
        self.assertEqual(commands, [])

    def test_blocks_missing_facts_for_current_law_audit_before_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = sync_group_fixture(root, is_law_related=True)
            record_law_audit_version(root, group, "2.0")
            group["questions"][0]["projected"].pop("lawRevisionFacts")
            synchronizer = ArtifactSynchronizer(
                root,
                FakeInventory(group),
                "secret",
                command_runner=lambda *_args, **_kwargs: 0,
            )

            result = sync_after_patch_update(
                synchronizer,
                "sample-exam",
                "2026",
                lambda _: None,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("lawRevisionFacts", result["message"])

    def test_legacy_law_audit_keeps_current_verdict_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = sync_group_fixture(root, is_law_related=True)
            record_law_audit_version(root, group, "0.0")
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                if command[1].endswith("prepare_firestore_upload.py"):
                    mark_group_synced(group)
                return 0

            synchronizer = ArtifactSynchronizer(
                root, FakeInventory(group), "secret", command_runner=run
            )
            preview = synchronizer.preview("sample-exam", "2026")
            result = synchronizer.run(
                "sample-exam", "2026", preview["previewToken"], lambda _: None
            )

        self.assertTrue(result["localReady"])
        self.assertFalse(result["requireCurrentLawVerdict"])
        self.assertEqual(result["strictValidationStages"], [])
        self.assertEqual(len(commands), 1)

    def test_mixed_law_audit_versions_validate_modern_without_blocking_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = sync_group_fixture(root, is_law_related=True)
            modern = group["questions"][0]
            legacy = copy.deepcopy(modern)
            legacy.update(
                {
                    "id": "question-2",
                    "reviewKey": "sample-exam:2026:question-2",
                    "originalQuestionId": "source-q2",
                }
            )
            group["questions"].append(legacy)
            record_law_audit_version(root, group, "2.0", questions=[modern])
            record_law_audit_version(root, group, "0.0", questions=[legacy])
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                if command[1].endswith("prepare_firestore_upload.py"):
                    mark_group_synced(group)
                return 0

            synchronizer = ArtifactSynchronizer(
                root, FakeInventory(group), "secret", command_runner=run
            )
            preview = synchronizer.preview("sample-exam", "2026")
            result = synchronizer.run(
                "sample-exam", "2026", preview["previewToken"], lambda _: None
            )

        self.assertEqual(preview["strictValidationWarnings"], [])
        self.assertTrue(result["requireCurrentLawVerdict"])
        self.assertEqual(result["strictValidationStages"], ["merged", "firestore"])
        self.assertEqual(result["strictValidationQuestionIds"], ["source-q1"])
        self.assertTrue(result["localReady"])
        self.assertEqual(len(commands), 3)
        for command in commands[1:]:
            self.assertIn("--original-question-id", command)
            self.assertIn("source-q1", command)
            self.assertNotIn("source-q2", command)

    def test_refreshes_only_merged_views_between_sessions(self):
        class RefreshingInventory(FakeInventory):
            def invalidate(self, qualification, list_group_id):
                self.payload["questions"][0]["workflow"]["merge"] = "match"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            source_dir.mkdir(parents=True)
            (source_dir / "question.json").write_text("{}", encoding="utf-8")
            group = group_payload(
                {"merge": "stale", "convert": "stale", "upload": "missing"}
            )
            group["questions"][0]["projected"] = {
                "answer_result_text": "",
                "choiceTextList": ["A", "B"],
                "correctChoiceText": ["正しい", "間違い"],
            }
            commands = []
            synchronizer = ArtifactSynchronizer(
                root,
                RefreshingInventory(group),
                "secret",
                command_runner=lambda command, **_kwargs: commands.append(command) or 0,
            )

            result = synchronizer.refresh_merged_views(
                "sample-exam", "2026", lambda _message: None
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(commands[0][1].endswith("scripts/merge/00_merge_all.py"))
        self.assertEqual(commands[0][2], "2026")
        self.assertIn("--base-dir", commands[0])
        self.assertIn("--allow-missing-answer-result", commands[0])
        self.assertNotIn("--upload-dry-run", commands[0])

    def test_inter_session_merge_rejects_successful_no_op_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            source_dir.mkdir(parents=True)
            (source_dir / "question.json").write_text("{}", encoding="utf-8")
            group = group_payload(
                {"merge": "stale", "convert": "stale", "upload": "missing"}
            )
            synchronizer = ArtifactSynchronizer(
                root,
                FakeInventory(group),
                "secret",
                command_runner=lambda _command, **_kwargs: 0,
            )

            with self.assertRaisesRegex(WorkflowError, "20_merged_1"):
                synchronizer.refresh_merged_views(
                    "sample-exam", "2026", lambda _message: None
                )

    def test_force_refresh_runs_pipeline_even_when_artifacts_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = (
                root
                / "output"
                / "sample-exam"
                / "questions_json"
                / "2026"
                / "00_source"
            )
            source_dir.mkdir(parents=True)
            (source_dir / "question.json").write_text("{}", encoding="utf-8")
            group = group_payload({"merge": "match", "convert": "match", "upload": "match"})
            group["questions"][0]["projected"] = {
                "answer_result_text": "正しい",
                "choiceTextList": ["A"],
                "correctChoiceText": ["正しい"],
            }
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                return 0

            synchronizer = ArtifactSynchronizer(
                root, FakeInventory(group), "secret", command_runner=run
            )
            preview = synchronizer.preview("sample-exam", "2026", force=True)
            result = synchronizer.run(
                "sample-exam",
                "2026",
                preview["previewToken"],
                lambda _: None,
                force=True,
            )

        self.assertTrue(preview["needsSync"])
        self.assertEqual(len(commands), 1)
        self.assertTrue(result["localReady"])


class GroupPublisherTests(unittest.TestCase):
    def test_failed_delta_blocks_group_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed_path = Path(
                "output/sample-exam/questions_json/2026/"
                "21_explanationText_added/partial.json"
            )
            absolute = root / failed_path
            absolute.parent.mkdir(parents=True)
            absolute.write_text("{}\n", encoding="utf-8")
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample-exam/"
                "20260101-run/manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "result": {"changedFiles": [failed_path.as_posix()]},
                    }
                ),
                encoding="utf-8",
            )
            group = group_payload(
                {"merge": "match", "convert": "match", "upload": "match"}
            )
            publisher = GroupPublisher(
                root,
                FakeInventory(group),
                FakeFirestore(),
                "secret",
            )

            preview = publisher.preview("sample-exam", "2026")

        self.assertFalse(preview["canPublish"])
        self.assertEqual(preview["failedDeltaPaths"], [failed_path.as_posix()])

    def test_law_audit_quality_issue_blocks_publish(self):
        counts = GroupPublisher._blocking_issue_counts(
            {
                "questions": [
                    {"issueCodes": ["law_audit_metadata_incomplete"]},
                    {"issueCodes": ["law_audit_verdict_mismatch"]},
                ]
            }
        )

        self.assertEqual(
            counts,
            {
                "law_audit_metadata_incomplete": 1,
                "law_audit_verdict_mismatch": 1,
            },
        )

    def test_publishes_exact_artifact_and_requires_clean_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative_path = Path(
                "output/sample-exam/questions_json/upload_to_firestore/"
                "2026_firestore_20260712_160000.json"
            )
            artifact = root / relative_path
            artifact.parent.mkdir(parents=True)
            document = upload_document()
            artifact.write_text(
                json.dumps({"questions": [document]}, ensure_ascii=False),
                encoding="utf-8",
            )
            source = (
                root
                / "output/sample-exam/questions_json/2026/00_source/question_1.json"
            )
            source.parent.mkdir(parents=True)
            source.write_text('{"question":"source"}\n', encoding="utf-8")
            group = group_payload(
                {"merge": "match", "convert": "match", "upload": "match"},
                upload_path=str(relative_path),
            )
            inventory = FakeInventory(group)
            firestore = FakeFirestore()
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                firestore.documents["doc-1"] = build_doc_data_base(document)
                emit("upload complete")
                return 0

            publisher = GroupPublisher(
                root,
                inventory,
                firestore,
                "secret",
                command_runner=run,
            )
            preview = publisher.preview("sample-exam", "2026")
            result = publisher.run(
                "sample-exam", "2026", preview, lambda _: None
            )

        self.assertEqual(preview["changedCount"], 1)
        self.assertEqual(preview["missingCount"], 1)
        self.assertEqual(result["changedCount"], 0)
        self.assertEqual(result["publishedCount"], 1)
        self.assertEqual(Path(commands[0][-1]).resolve(), artifact.resolve())
        self.assertNotIn("--dry-run", commands[0])


class JobManagerTests(unittest.TestCase):
    def test_prevents_parallel_jobs_for_same_group(self):
        manager = JobManager()

        def worker(emit):
            time.sleep(0.08)
            emit("done")
            return {"ok": True}

        first = manager.start(kind="sync", key="sample:2026", worker=worker)
        with self.assertRaises(JobConflictError):
            manager.start(kind="publish", key="sample:2026", worker=worker)

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = manager.get(first["jobId"])
            if result["status"] == "succeeded":
                break
            time.sleep(0.02)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["logs"], ["done"])

    def test_structured_logs_dedupe_adjacent_messages_and_track_activity(self):
        manager = JobManager()

        def worker(emit):
            emit("same message")
            emit("same message")
            getattr(emit, "heartbeat")()
            getattr(emit, "event")(
                {
                    "level": "error",
                    "message": "command failed exitCode=1: test",
                    "commandStatus": "failed",
                    "exitCode": 1,
                    "outputTail": "verification failed",
                }
            )
            return {"ok": True}

        started = manager.start(kind="maintenance", key="sample", worker=worker)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = manager.get(started["jobId"])
            if result["status"] == "succeeded":
                break
            time.sleep(0.01)

        self.assertEqual(
            result["logs"],
            ["same message", "command failed exitCode=1: test"],
        )
        self.assertEqual(
            [entry["sequence"] for entry in result["logEntries"]],
            [1, 2],
        )
        self.assertEqual(
            [entry["level"] for entry in result["logEntries"]],
            ["info", "error"],
        )
        self.assertTrue(all(entry["at"] for entry in result["logEntries"]))
        self.assertTrue(
            all(entry["observedAt"] for entry in result["logEntries"])
        )
        self.assertEqual(result["logEntries"][1]["commandStatus"], "failed")
        self.assertEqual(result["logEntries"][1]["exitCode"], 1)
        self.assertEqual(
            result["logEntries"][1]["outputTail"], "verification failed"
        )
        self.assertTrue(result["lastActivityAt"])

    def test_sync_exclusive_work_blocks_background_job(self):
        manager = JobManager()
        entered = threading.Event()
        release = threading.Event()

        def run_sync():
            entered.set()
            release.wait(1)
            return {"ok": True}

        thread = threading.Thread(
            target=lambda: manager.run_exclusive(key="sample:2026", worker=run_sync)
        )
        thread.start()
        self.assertTrue(entered.wait(1))
        with self.assertRaises(JobConflictError):
            manager.start(
                kind="publish",
                key="sample:2026",
                worker=lambda _emit: {"ok": True},
            )
        release.set()
        thread.join(1)

        job = manager.start(
            kind="publish",
            key="sample:2026",
            worker=lambda _emit: {"ok": True},
        )
        self.assertIn(job["status"], {"queued", "running", "succeeded"})


class WorkflowUiContractTests(unittest.TestCase):
    def test_partial_queue_exposes_failed_question_retry_without_hiding_successes(self):
        root = Path(__file__).resolve().parents[1]
        static = root / "tools" / "question_review_console" / "static"
        javascript = (static / "app.js").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        view_state = javascript.split(
            "function qualificationRunViewState", 1
        )[1].split("function renderQualificationRunPhases", 1)[0]
        retry = javascript.split(
            "function retryBlockedQualificationRun", 1
        )[1].split("async function resumeQualificationRun", 1)[0]

        self.assertIn('run?.queueStatus === "partial"', view_state)
        self.assertIn('statusLabel = `${blockedQuestions}問保留`', view_state)
        self.assertIn("理由付きで保留", view_state)
        self.assertIn('run.queueStatus !== "partial"', retry)
        self.assertIn("resumedFrom: run.runId", retry)
        self.assertIn("scopeListGroupIds", retry)
        self.assertIn('id="qualification-active-run-retry"', html)

    def test_manual_artifact_regeneration_remains_reachable_when_current(self):
        root = Path(__file__).resolve().parents[1]
        javascript = (
            root / "tools/question_review_console/static/app.js"
        ).read_text(encoding="utf-8")
        pipeline_actions = javascript.split(
            "function renderPipelineActions", 1
        )[1].split("function openEvaluationRework", 1)[0]

        self.assertIn('label: "成果物を再生成"', pipeline_actions)
        self.assertIn("if (localReady && !maintenanceBlocksPublication(question))", pipeline_actions)
        self.assertIn("actions.append(patchSyncAction({", pipeline_actions)

    def test_succeeded_run_requires_validated_receipt_for_completion_display(self):
        root = Path(__file__).resolve().parents[1]
        javascript = (
            root / "tools/question_review_console/static/app.js"
        ).read_text(encoding="utf-8")
        view_state = javascript.split(
            "function qualificationRunViewState", 1
        )[1].split("function renderQualificationRunPhases", 1)[0]
        history = javascript.split(
            "function renderQualificationActiveRun", 1
        )[1].split("function renderQualificationRunStatusDetail", 1)[0]

        self.assertIn('run?.status === "succeeded" && run?.receiptValidated === true', view_state)
        self.assertIn('const unverified = run?.status === "succeeded" && !verified', view_state)
        self.assertIn('statusLabel = "未承認"', view_state)
        self.assertIn("item.receiptValidated === true", history)
        self.assertIn('? "未承認"', history)

    def test_failed_receipt_message_is_not_hidden_as_invalid_receipt(self):
        root = Path(__file__).resolve().parents[1]
        javascript = (
            root / "tools/question_review_console/static/app.js"
        ).read_text(encoding="utf-8")
        humanizer = javascript.split(
            "function humanizeQualificationRunError", 1
        )[1].split("function artifactSyncNeedsAttention", 1)[0]

        self.assertIn("invalidReceiptMarkers", humanizer)
        self.assertIn('message.includes("最初に失敗した検証:")', humanizer)
        self.assertIn("完了receiptが見つかりません", humanizer)
        self.assertNotIn('message.includes("receipt")', humanizer)

    def test_top_entries_share_one_required_maintenance_flow(self):
        root = Path(__file__).resolve().parents[1]
        javascript = (
            root / "tools/question_review_console/static/app.js"
        ).read_text(encoding="utf-8")
        flow = javascript.split("function openRequiredMaintenance", 1)[1].split(
            "function renderMaintenanceDashboard", 1
        )[0]
        selector = javascript.split("function maintenanceRunStageIds", 1)[1].split(
            "function openRequiredMaintenance", 1
        )[0]

        self.assertIn("requiredMaintenance?.mode", flow)
        self.assertIn("simplified: true", flow)
        self.assertIn(
            '$("#maintenance-start").addEventListener("click", () => openRequiredMaintenance())',
            javascript,
        )
        self.assertIn(
            "openRequiredMaintenance([group.listGroupId])",
            javascript,
        )
        self.assertIn("requiredMaintenance?.stageIds", selector)
        self.assertNotIn('"law_audit"', selector)
        self.assertNotIn('"category_setup"', selector)
        self.assertNotIn('"question_set"', selector)
        self.assertIn('const UI_CONTRACT_VERSION = "question-review-ui/v2"', javascript)
        self.assertIn("session.uiContractVersion !== UI_CONTRACT_VERSION", javascript)
        stage_controls = javascript.split(
            "function renderQualificationRunStages", 1
        )[1].split("function updateQualificationRunHeading", 1)[0]
        self.assertIn("renderQualificationRunGroups(stage, nextGroupIds)", stage_controls)
        self.assertIn(
            "state.qualificationRunDialog.listGroupIds = nextGroupIds",
            stage_controls,
        )

    def test_law_audit_warning_has_no_manual_bulk_request(self):
        root = Path(__file__).resolve().parents[1]
        javascript = (
            root / "tools/question_review_console/static/app.js"
        ).read_text(encoding="utf-8")
        warning = javascript.split("function renderLawAuditQualityWarning", 1)[1].split(
            "function openFindingsReview", 1
        )[0]

        self.assertIn("法令監査メタデータが不完全です", warning)
        self.assertIn("トップ画面の整備", warning)
        self.assertNotIn("actionWithHelp", warning)
        self.assertNotIn("openLawAuditQualityReview", javascript)
        self.assertNotIn("監査パッチをまとめて修正依頼", javascript)

    def test_mobile_dialog_uses_dynamic_viewport_and_scrollable_body(self):
        root = Path(__file__).resolve().parents[1]
        static = root / "tools" / "question_review_console" / "static"
        html = (static / "index.html").read_text(encoding="utf-8")
        css = (static / "styles.css").read_text(encoding="utf-8")
        javascript = (static / "app.js").read_text(encoding="utf-8")
        compact_css = " ".join(css.split())

        self.assertIn(
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            html,
        )
        self.assertIn("max-height: calc(100dvh - 28px)", compact_css)
        self.assertIn(
            "dialog[open] { display: flex; flex-direction: column; }",
            compact_css,
        )
        self.assertIn("dialog > form { width: 100%; flex: 1 1 auto; }", compact_css)
        self.assertIn(
            ".dialog-body { min-height: 0; flex: 1 1 auto;",
            compact_css,
        )
        self.assertIn("-webkit-overflow-scrolling: touch", compact_css)
        self.assertIn(
            "html.workflow-guide-open, body.workflow-guide-open { overflow: hidden; }",
            compact_css,
        )
        self.assertIn(
            "html.audit-view-open, body.audit-view-open { overflow: hidden; }",
            compact_css,
        )
        self.assertIn(
            ".audit-view { position: fixed; inset: 0; z-index: 24;",
            compact_css,
        )
        self.assertIn(".audit-view[hidden] { display: none; }", compact_css)
        self.assertIn(
            'document.documentElement.classList.add("workflow-guide-open")',
            javascript,
        )
        self.assertIn(
            'document.documentElement.classList.remove("workflow-guide-open")',
            javascript,
        )
        self.assertIn(
            ".markdown-document { min-width: 0; overflow: auto; overscroll-behavior: contain;",
            compact_css,
        )

        mobile_css = compact_css.split("@media (max-width: 520px)", 1)[1]
        tablet_css = compact_css.split("@media (max-width: 900px)", 1)[1].split(
            "@media (max-width: 520px)", 1
        )[0]
        self.assertIn(
            ".audit-view-content { display: block; overflow-y: auto;",
            tablet_css,
        )
        self.assertIn(
            "dialog, .wide-dialog, .help-dialog { inset: 0; width: 100vw;",
            mobile_css,
        )
        self.assertIn("height: 100dvh", mobile_css)
        self.assertIn(
            ".run-stage-options { grid-template-columns: repeat(2, minmax(0, 1fr));",
            mobile_css,
        )
        for selector in (
            ".qualification-stage-head strong",
            ".selection-toolbar-copy span",
            ".workflow-guide-footer span",
        ):
            rule = mobile_css.split(f"{selector} {{", 1)[1].split("}", 1)[0]
            self.assertIn("white-space: normal", rule)
            self.assertIn("overflow-wrap: anywhere", rule)

    def test_dialog_controls_have_matching_javascript_handlers(self):
        root = Path(__file__).resolve().parents[1]
        static = root / "tools" / "question_review_console" / "static"
        html = (static / "index.html").read_text(encoding="utf-8")
        javascript = (static / "app.js").read_text(encoding="utf-8")
        css = (static / "styles.css").read_text(encoding="utf-8")

        for control_id in (
            "qualification-workflow",
            "maintenance-dashboard",
            "maintenance-required-count",
            "maintenance-progress-text",
            "maintenance-start",
            "maintenance-year-progress",
            "audit-view-open",
            "audit-view",
            "audit-view-close",
            "audit-view-loading",
            "audit-admin-tools",
            "qualification-workflow-stages",
            "qualification-workflow-action",
            "qualification-active-run",
            "qualification-active-run-eyebrow",
            "qualification-active-run-phases",
            "qualification-active-run-meter-value",
            "qualification-active-run-error",
            "qualification-active-run-updated",
            "qualification-run-history",
            "qualification-run-dialog",
            "qualification-run-stage-fieldset",
            "qualification-run-stages",
            "qualification-run-group-fieldset",
            "qualification-run-groups",
            "qualification-run-groups-all",
            "qualification-run-groups-clear",
            "qualification-run-start",
            "qualification-run-progress-current",
            "qualification-run-progress-title",
            "qualification-run-progress-events",
            "qualification-run-progress-bar",
            "qualification-run-status-detail",
            "progress-question-dialog",
            "progress-question-content",
            "load-more-questions",
            "workflow-dialog",
            "production-confirm",
            "workflow-execute",
            "job-log",
            "bulk-readback-button",
            "bulk-readback-help",
            "evaluation-status-select",
            "work-version-select",
            "work-version-label",
            "select-visible",
            "bulk-evaluate-button",
            "readback-dialog",
            "readback-execute",
            "group-select-label",
            "help-dialog",
            "help-dialog-title",
            "help-dialog-content",
            "confirm-validation",
            "qualification-run-outdated",
        ):
            self.assertIn(f'id="{control_id}"', html)
        for function_name in (
            "loadQualificationWorkflow",
            "renderQualificationWorkflow",
            "renderMaintenanceDashboard",
            "auditViewIsOpen",
            "invalidateAuditView",
            "openAuditView",
            "closeAuditView",
            "maintenanceRunStageIds",
            "openRequiredMaintenance",
            "revealSelectedQualificationStage",
            "executeQualificationWorkflowAction",
            "loadQualificationRuns",
            "openQualificationRunDialog",
            "previewQualificationRun",
            "startQualificationRun",
            "resumeQualificationRun",
            "renderQualificationRunProgress",
            "openProgressQuestion",
            "loadQualificationRunProgress",
            "openSyncDialog",
            "openPublishDialog",
            "openEvaluationDialog",
            "executeWorkflow",
            "pollJob",
            "openReadbackDialog",
            "executeScopedReadback",
            "pollReadbackJob",
            "renderFirestoreDiff",
            "patchSyncAction",
            "openHelp",
            "actionWithHelp",
            "renderRequiredFieldWarning",
            "openRequiredFieldsReview",
            "renderLawAuditQualityWarning",
            "openFindingsReview",
            "helpIcon",
            "renderStructuredValue",
            "renderLawReferences",
            "renderLawRevisionFacts",
            "renderProjectedData",
            "startWorkflowExecution",
            "parseDataPath",
            "installReviewTarget",
            "normalizedReviewSelection",
            "renderSelectionToolbar",
            "openSelectionReview",
            "toggleVisibleQuestionSelection",
            "updateEvaluationSelectionControls",
            "renderEvaluationPanel",
            "renderWorkVersionPanel",
            "renderQuestionAdminDetails",
            "renderPublicationStatus",
            "publicationContent",
            "renderQueuePublicationSummary",
            "renderLoadError",
            "maintenanceBlocksPublication",
            "workVersionBadge",
        ):
            self.assertIn(f"function {function_name}", javascript)
        self.assertIn("progress.questions || []", javascript)
        self.assertIn('includeQuestions: "true"', javascript)
        self.assertIn("questionsIncluded !== true", javascript)
        start_failure = javascript[
            javascript.index("async function startQualificationRun") :
            javascript.index("function setQualificationRunRunning")
        ]
        resume_failure = javascript[
            javascript.index("async function resumeQualificationRun") :
            javascript.index("async function setListMode")
        ]
        for failure_path in (start_failure, resume_failure):
            self.assertLess(
                failure_path.index("await loadQualificationRuns();"),
                failure_path.index(
                    "await loadQualificationRunProgress(failedRun.runId);"
                ),
            )
            self.assertLess(
                failure_path.index(
                    "await loadQualificationRunProgress(failedRun.runId);"
                ),
                failure_path.index(
                    "renderQualificationRunProgress(state.qualificationRunProgress);"
                ),
            )
        self.assertNotIn(".slice(-20)", javascript)
        self.assertNotIn("max-height: 30vh", css)
        self.assertIn('node.id = "firestore-diff-panel"', javascript)
        self.assertIn('"Firestore（取得値）"', javascript)
        self.assertIn("formatReadbackTime", javascript)
        self.assertIn('"資格のFirestoreを確認"', javascript)
        self.assertIn('"パッチ変更を反映"', javascript)
        self.assertIn("actions.append(patchSyncAction())", javascript)
        self.assertIn('applyButton.textContent = "保存・再生成中"', javascript)
        self.assertIn("公開用データまで自動更新しました。", javascript)
        self.assertIn('"Firestoreへ反映"', javascript)
        self.assertIn('"/api/evaluations/preview"', javascript)
        self.assertIn('"/api/evaluations/start"', javascript)
        self.assertIn("selectedQuestionIds", javascript)
        self.assertIn("`反映待ち${pendingCount}`", javascript)
        self.assertIn("一覧の${visibleIds.length}問を選択", javascript)
        self.assertIn('summaryMetric("資格", qualificationDisplayName(preview.qualification))', javascript)
        self.assertIn('summaryMetric("年度", preview.listGroupIds?.join("・") || "-")', javascript)
        self.assertIn("専用の24_questionIssueCorrections契約", html)
        review_fields = javascript.split("const REVIEW_FIELDS = [", 1)[1].split(
            "];", 1
        )[0]
        self.assertNotIn('"questionBodyText"', review_fields)
        self.assertNotIn('"choiceTextList"', review_fields)
        self.assertIn("LAW_REVIEW_REQUIRED_FIELDS", javascript)
        self.assertIn("syncLawReviewFields", javascript)
        law_review_fields = javascript.split(
            "const LAW_REVIEW_REQUIRED_FIELDS = [", 1
        )[1].split("];", 1)[0]
        for field in (
            "explanationText",
            "suggestedQuestions",
            "suggestedQuestionDetails",
            "lawReferences",
            "lawRevisionFacts",
        ):
            self.assertIn(f'"{field}"', law_review_fields)
        self.assertIn('field === "isLawRelated"', javascript)
        self.assertIn('"修正を依頼"', javascript)
        self.assertIn('"直接編集"', javascript)
        self.assertIn("actionWithHelp", javascript)
        self.assertNotIn("selectedReadbackGroupIds", javascript)
        self.assertNotIn("runFirestoreReadback", javascript)
        self.assertIn('"firestore-diff-item-path"', javascript)
        self.assertIn('"firestore-diff-no-change"', javascript)
        self.assertIn('"差分なし"', javascript)
        self.assertNotIn('"firestore-diff-more"', javascript)
        self.assertIn('id="review-selection"', html)
        self.assertIn('id="selection-toolbar"', html)
        self.assertIn('id="review-scope"', html)
        self.assertNotIn('id="advanced-tools-toggle"', html)
        self.assertNotIn('id="audit-details-dialog"', html)
        self.assertIn("公開前の内容を確認", html)
        self.assertIn("patch適用後の正答・解説とFirestore反映状態を見る", html)
        self.assertIn('id="exceptions-button" class="active" type="button">反映待ち</button>', html)
        self.assertIn("工程・評価・Firestoreなどの管理機能", html)
        self.assertNotIn('id="audit-admin-tools" class="audit-admin-tools" open', html)
        self.assertIn('$("#audit-view-open").addEventListener("click", openAuditView)', javascript)
        self.assertIn('$("#audit-view-close").addEventListener("click", closeAuditView)', javascript)
        self.assertIn('$("#audit-admin-tools").addEventListener("toggle"', javascript)
        self.assertIn('.audit-view:not(.admin-tools-open) .queue-select { display: none; }', css)
        self.assertIn("Firestoreへ反映する最終内容", javascript)
        self.assertIn("question.uploadReadyDocs", javascript)
        self.assertIn('"パッチを修正"', javascript)
        self.assertIn("renderLawSection(publication.record)", javascript)
        self.assertIn("lawReferences: documents.map", javascript)
        self.assertIn('evaluation.publishReady === true && question.nextAction === "publish"', javascript)
        self.assertIn("fingerprint.workflowFirestore !== current.workflow?.firestore", javascript)
        self.assertIn('(fingerprint.evaluationResultHash || "") !== (current.evaluation?.resultHash || "")', javascript)
        self.assertIn("state.questionPage.hasMore = false", javascript)
        initialize_source = javascript[
            javascript.index("async function initialize") :
            javascript.index("function bindControls")
        ]
        self.assertNotIn("loadQuestions", initialize_source)
        audit_open_source = javascript[
            javascript.index("async function openAuditView") :
            javascript.index("function closeAuditView")
        ]
        self.assertIn("loadQuestions(preserveSelection)", audit_open_source)
        self.assertIn('$("#maintenance-dashboard").inert = true', audit_open_source)
        detail_source = javascript[
            javascript.index("function renderDetail") :
            javascript.index("function renderQuestionAdminDetails")
        ]
        self.assertLess(
            detail_source.index('section("問題文")'),
            detail_source.index("renderQuestionAdminDetails(question)"),
        )
        self.assertLess(
            detail_source.index("renderPublicationStatus(question, publication)"),
            detail_source.index('section("問題文")'),
        )
        self.assertNotIn('value="all_qualifications"', html)
        self.assertIn('"selectionchange"', javascript)
        self.assertIn("selection: state.reviewSelection", javascript)
        self.assertIn('investigationScope: $("#review-scope").value', javascript)
        self.assertIn('"欠損をまとめて修正依頼"', javascript)
        self.assertNotIn('"監査パッチをまとめて修正依頼"', javascript)
        self.assertNotIn("function openLawAuditQualityReview", javascript)
        self.assertIn("requestKind: state.reviewRequestKind", javascript)
        self.assertIn('requestKind === "qualification_law_audit"', javascript)
        self.assertIn('$("#review-scope-wrap").hidden = qualificationLawAudit', javascript)
        self.assertIn('const ALL_LIST_GROUPS = "__all__"', javascript)
        self.assertIn('`/api/qualification-workflow?${params}`', javascript)
        self.assertIn('"/api/qualification-runs/preview"', javascript)
        self.assertIn('"/api/qualification-runs/start"', javascript)
        self.assertIn("QUALIFICATION_PREVIEW_TIMEOUT_MS", javascript)
        self.assertIn("function cancelQualificationRunPreview", javascript)
        self.assertIn("function setQualificationRunPreviewState", javascript)
        self.assertIn('setQualificationRunPreviewState("error", message)', javascript)
        self.assertIn("await previewQualificationRun()", javascript)
        self.assertIn('value="group_refresh" checked', html)
        self.assertIn("selectedQualificationRunStageIds", javascript)
        self.assertIn("stageIds,", javascript)
        self.assertIn("selectedQualificationRunListGroupIds", javascript)
        self.assertIn("listGroupIds:", javascript)
        self.assertIn("複数選択可", javascript)
        self.assertIn('const questionUnit = ["refresh", "group_refresh"].includes(preview.mode)', javascript)
        self.assertIn("${preview.targetCount}${questionUnit} × ${preview.stageCount}工程", javascript)
        self.assertIn("延べ${preview.workItemCount}工程判定", javascript)
        self.assertIn('api("/api/codex/status")', javascript)
        self.assertIn('id="qualification-active-run"', html)
        self.assertIn('id="qualification-active-run-model"', html)
        self.assertIn("現在実行中の作業はありません", html)
        self.assertIn('invalidated: "無効化済み"', javascript)
        self.assertIn('"この作業での出力（無効化済み）"', javascript)
        self.assertIn(
            "const visibleRun = state.qualificationActiveRun || state.qualificationRuns[0] || null",
            javascript,
        )
        self.assertIn(
            "const activeJobId = state.qualificationActiveRun?.runId === visibleRun.runId",
            javascript,
        )
        self.assertIn('phase = "最終検証で停止"', javascript)
        self.assertIn('progress.status === "failed"', javascript)
        self.assertIn("最終検証は未承認", javascript)
        self.assertIn(".qualification-active-run.failed", css)
        self.assertIn(
            ".qualification-active-run-phases { grid-template-columns: 1fr; }",
            css,
        )
        self.assertIn("function pollSharedRunProgress", javascript)
        self.assertIn("window.setInterval(pollSharedRunProgress, 3000)", javascript)
        self.assertIn("|| state.qualificationRunDialog.running", javascript)
        self.assertIn("const QUALIFICATION_RUN_IDLE_POLL_MS = 30000", javascript)
        self.assertIn("now - state.lastSharedRunPollAt < QUALIFICATION_RUN_IDLE_POLL_MS", javascript)
        self.assertIn("loadQualificationRuns({ includeLatestProgress: false })", javascript)
        self.assertIn("state.qualificationActiveJob?.logs", javascript)
        self.assertIn("state.qualificationRunProgress", javascript)
        self.assertIn("maintenance-year-row${working ? \" working\"", javascript)
        self.assertIn(".maintenance-year-row.working", css)
        self.assertIn("問題ごとの出力", javascript)
        self.assertIn("タップして問題本文を見る", javascript)
        self.assertIn("/progress?${params}", javascript)
        self.assertIn("/summary`", javascript)
        self.assertIn('$("#qualification-run-technical-log").open', javascript)
        self.assertIn("function loadQualificationTechnicalLog", javascript)
        self.assertIn("/technical-log?${params}", javascript)
        self.assertIn("renderQualificationTechnicalLog(payload)", javascript)
        self.assertIn('!("result" in job)', javascript)
        self.assertIn("function progressDisplayLabel", javascript)
        self.assertIn("function progressQuestionApproved", javascript)
        self.assertIn("event?.approvalState", javascript)
        self.assertIn('progressApprovalState(question)', javascript)
        self.assertIn("codexStatus.turnReasoningEffort", javascript)
        self.assertIn('startCodex: state.reviewMode === "awaiting_codex"', javascript)
        self.assertIn('requestKind === "evaluation_rework"', javascript)
        self.assertIn("Codex App Server:", javascript)
        self.assertNotIn('"/api/qualification-runs/resume-prompt"', javascript)
        self.assertIn('offset: String(offset)', javascript)
        self.assertIn('limit: String(state.questionPage.limit)', javascript)
        self.assertIn('params.set("workStageId", state.qualificationWorkflowStageId)', javascript)
        self.assertIn('params.set("workVersionStatus", workVersionStatus)', javascript)
        self.assertIn("workVersionSelect.disabled = !selectedStage?.policyVersion", javascript)
        self.assertIn("outdatedLabel.hidden = !stage.policyVersion", javascript)
        self.assertIn('value="outdated"', html)
        self.assertIn("洗い替え必要・未整備のみ", html)
        self.assertIn('workflow.summary?.requiredMaintenance?.mode || "outdated"', javascript)
        self.assertIn('simplified: true', javascript)
        self.assertIn('`未整備 ${preview.targetCount}問`', javascript)
        self.assertIn('"本番Firestoreには反映せず、ローカルで整備します。"', javascript)
        self.assertIn('workflow.groups.map((group) => group.listGroupId)', javascript)
        self.assertIn('openRequiredMaintenance([group.listGroupId])', javascript)
        self.assertIn(".maintenance-start { width: 100%; min-height: 48px; }", css)
        self.assertIn(".maintenance-year-progress { grid-template-columns: 1fr; }", css)
        self.assertIn('stage.completeCount === stage.targetCount && stage.issueCount > 0', javascript)
        self.assertIn('`次は ${nextStage.code} ${nextStage.label}', javascript)
        self.assertIn('`すべて（${groups.length}件）`', javascript)
        self.assertIn('"パッチ適用後データ"', javascript)
        self.assertIn('summaryMetric("更新", `${preview.updateCount || 0}件`', javascript)
        self.assertIn('summaryMetric("追加", `${preview.missingCount}件`', javascript)
        self.assertIn('preview.reason || "安全条件を満たさないため', javascript)
        self.assertNotIn("references.open = true", javascript)
        self.assertNotIn('"投影後JSON"', javascript)
        self.assertNotIn("function jsonPre", javascript)
        self.assertIn("state.detail?.listGroupId || state.listGroupId", javascript)
        pipeline_source = javascript[
            javascript.index("function renderPipelineActions") :
            javascript.index("function parseDataPath")
        ]
        self.assertGreater(
            pipeline_source.index("actions.append(patchSyncAction())"),
            pipeline_source.index("if (!localReady)"),
        )


if __name__ == "__main__":
    unittest.main()
