import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from scripts.upload.upload_questions_to_firestore import build_doc_data_base
from tools.question_review_console.jobs import JobConflictError, JobManager
from tools.question_review_console.publisher import GroupPublisher
from tools.question_review_console.workflow_runner import (
    ArtifactSynchronizer,
    WorkflowError,
)


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


class ArtifactSynchronizerTests(unittest.TestCase):
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


class GroupPublisherTests(unittest.TestCase):
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


class WorkflowUiContractTests(unittest.TestCase):
    def test_dialog_controls_have_matching_javascript_handlers(self):
        root = Path(__file__).resolve().parents[1]
        static = root / "tools" / "question_review_console" / "static"
        html = (static / "index.html").read_text(encoding="utf-8")
        javascript = (static / "app.js").read_text(encoding="utf-8")

        for control_id in (
            "workflow-dialog",
            "production-confirm",
            "workflow-execute",
            "job-log",
            "bulk-readback-button",
            "bulk-readback-help",
            "readback-dialog",
            "readback-execute",
            "group-select-label",
            "help-dialog",
            "help-dialog-title",
            "help-dialog-content",
            "confirm-validation",
        ):
            self.assertIn(f'id="{control_id}"', html)
        for function_name in (
            "openSyncDialog",
            "openPublishDialog",
            "executeWorkflow",
            "pollJob",
            "openReadbackDialog",
            "executeScopedReadback",
            "pollReadbackJob",
            "renderFirestoreDiff",
            "scrollToFirestoreDiff",
            "patchSyncAction",
            "openHelp",
            "actionWithHelp",
            "renderRequiredFieldWarning",
            "openRequiredFieldsReview",
            "renderLawAuditQualityWarning",
            "openLawAuditQualityReview",
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
        ):
            self.assertIn(f"function {function_name}", javascript)
        self.assertIn('node.id = "firestore-diff-panel"', javascript)
        self.assertIn('"Firestore（取得値）"', javascript)
        self.assertIn("formatReadbackTime", javascript)
        self.assertIn('"資格のFirestoreを確認"', javascript)
        self.assertIn('"パッチ変更を反映"', javascript)
        self.assertIn("actions.append(patchSyncAction())", javascript)
        self.assertIn("firestoreNeedsAttention", javascript)
        self.assertIn('"Firestoreへ反映"', javascript)
        self.assertIn('"保存済み差分を見る"', javascript)
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
        self.assertIn('"selectionchange"', javascript)
        self.assertIn("selection: state.reviewSelection", javascript)
        self.assertIn('investigationScope: $("#review-scope").value', javascript)
        self.assertIn('"欠損をまとめて修正依頼"', javascript)
        self.assertIn('"監査パッチをまとめて修正依頼"', javascript)
        self.assertIn("資格内の法令監査不備を一問一肢ずつ", javascript)
        self.assertIn('investigationScope: "qualification"', javascript)
        self.assertIn('requestKind: "qualification_law_audit"', javascript)
        self.assertIn("requestKind: state.reviewRequestKind", javascript)
        self.assertIn('requestKind === "qualification_law_audit"', javascript)
        self.assertIn('$("#review-scope-wrap").hidden = qualificationLawAudit', javascript)
        self.assertIn('const ALL_LIST_GROUPS = "__all__"', javascript)
        self.assertIn('`すべて（${groups.length}件）`', javascript)
        self.assertIn('"パッチ適用後データ"', javascript)
        self.assertNotIn('"投影後JSON"', javascript)
        self.assertNotIn("function jsonPre", javascript)
        self.assertIn("state.detail?.listGroupId || state.listGroupId", javascript)
        pipeline_source = javascript[
            javascript.index("function renderPipelineActions") :
            javascript.index("function parseDataPath")
        ]
        self.assertLess(
            pipeline_source.index("actions.append(patchSyncAction())"),
            pipeline_source.index("if (!localReady)"),
        )


if __name__ == "__main__":
    unittest.main()
