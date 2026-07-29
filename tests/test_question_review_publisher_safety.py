from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from scripts.upload.upload_questions_to_firestore import build_doc_data_base
from tools.question_review_console.jobs import (
    REPOSITORY_OPERATION_KEY,
    JobManager,
)
from tools.question_review_console.publisher import PublicationError, QuestionPublisher


def upload_document(question_id: str, choice: str, verdict: str) -> dict:
    return {
        "questionId": question_id,
        "originalQuestionId": "original-1",
        "originalQuestionBodyText": "問題1",
        "originalQuestionChoiceText": choice,
        "questionBodyText": "問題1",
        "questionSetId": "set-1",
        "questionText": f"問題1 {choice}",
        "questionType": "true_false",
        "qualificationId": "sample",
        "listGroupId": "2026",
        "correctChoiceText": verdict,
        "explanationText": f"{choice}の解説",
        "examYear": 2026,
        "examSource": "サンプル資格 2026年",
        "questionTags": [],
        "isOfficial": True,
        "isDeleted": False,
        "isChoiceOnly": False,
        "isGroupable": True,
    }


def question_payload() -> dict:
    documents = [
        upload_document("doc-1", "選択肢A", "正しい"),
        upload_document("doc-2", "選択肢B", "間違い"),
    ]
    return {
        "id": "api-q1",
        "reviewKey": "sample:2026:question_1:api-q1",
        "sourceQuestionKey": "sample:api-q1",
        "qualification": "sample",
        "publicationQualificationId": "sample",
        "listGroupId": "2026",
        "originalQuestionId": "original-1",
        "questionLabel": "問題1",
        "stateHash": "state-1",
        "issueCodes": [],
        "workflow": {"merge": "match", "convert": "match", "upload": "match"},
        "uploadReadyDocs": documents,
        "paths": {
            "source": "output/sample/questions_json/2026/00_source/question_1.json",
            "uploadReady": (
                "output/sample/questions_json/upload_to_firestore/"
                "2026_firestore_20260715_120000.json"
            ),
        },
    }


class FakeInventory:
    def __init__(self, question: dict) -> None:
        self.question = question

    def group(self, qualification: str, list_group_id: str) -> dict:
        return {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "questions": [self.question],
        }


class FakeEvaluationService:
    def status_for(self, _question: dict, *, failed_delta_paths=None) -> dict:
        return {
            "status": "passed",
            "publishReady": True,
            "resultHash": "evaluation-hash",
            "machineReady": True,
            "blockingIssues": [],
        }


class FakeFirestore:
    def __init__(self, documents: dict | None = None) -> None:
        self.documents = documents or {}
        self.read_count = 0

    def read_documents(self, document_ids, *, fields=None):
        self.read_count += 1
        return {
            question_id: copy.deepcopy(self.documents[question_id])
            for question_id in document_ids
            if question_id in self.documents
        }


def write_inputs(root: Path, question: dict) -> Path:
    source = root / question["paths"]["source"]
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"question":"source"}\n', encoding="utf-8")
    artifact = root / question["paths"]["uploadReady"]
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps({"questions": question["uploadReadyDocs"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return source


class QuestionPublisherSafetyTests(unittest.TestCase):
    def publisher(
        self,
        root: Path,
        question: dict,
        firestore: FakeFirestore,
        *,
        command_runner=None,
    ) -> QuestionPublisher:
        return QuestionPublisher(
            root,
            FakeInventory(question),
            firestore,
            FakeEvaluationService(),
            "secret",
            command_runner=command_runner,
        )

    def test_preview_blocks_deleted_candidate_before_firestore_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            question["uploadReadyDocs"][0]["isDeleted"] = True
            write_inputs(root, question)
            firestore = FakeFirestore()

            preview = self.publisher(root, question, firestore).preview(question)

        self.assertFalse(preview["canPublish"])
        self.assertEqual(preview["status"], "blocked")
        self.assertEqual(preview["deletedDocumentIds"], ["doc-1"])
        self.assertIn("isDeleted=true", preview["reason"])
        self.assertEqual(firestore.read_count, 0)

    def test_preview_blocks_when_source_directory_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            source = write_inputs(root, question)
            source.unlink()

            with self.assertRaisesRegex(PublicationError, "00_source"):
                self.publisher(root, question, FakeFirestore()).preview(question)

    def test_preview_separates_missing_and_update_counts_and_optional_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            candidate = question["uploadReadyDocs"][0]
            candidate.update(
                {
                    "knowledgeText": "新しい知識",
                    "questionImageUrls": ["https://example.test/new.png"],
                    "importKey": "new-import-key",
                }
            )
            write_inputs(root, question)
            existing = build_doc_data_base(candidate)
            existing.update(
                {
                    "knowledgeText": "古い知識",
                    "questionImageUrls": ["https://example.test/old.png"],
                    "importKey": "old-import-key",
                }
            )
            firestore = FakeFirestore({"doc-1": existing})

            preview = self.publisher(root, question, firestore).preview(question)

        self.assertEqual(preview["changedCount"], 2)
        self.assertEqual(preview["updateCount"], 1)
        self.assertEqual(preview["missingCount"], 1)
        changes = {item["questionId"]: item["fields"] for item in preview["changes"]}
        self.assertEqual(
            changes["doc-1"],
            ["knowledgeText", "questionImageUrls", "importKey"],
        )
        self.assertEqual(changes["doc-2"], ["document"])

    def test_preview_uses_publication_original_id_from_upload_ready_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            for document in question["uploadReadyDocs"]:
                document["originalQuestionId"] = "legacy-firestore-original-1"
            write_inputs(root, question)

            preview = self.publisher(root, question, FakeFirestore()).preview(question)

        self.assertTrue(preview["canPublish"])
        self.assertEqual(preview["missingCount"], 2)

    def test_preview_rejects_mixed_publication_original_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            question["uploadReadyDocs"][1]["originalQuestionId"] = "another-original"
            write_inputs(root, question)

            with self.assertRaisesRegex(PublicationError, "別の元問題"):
                self.publisher(root, question, FakeFirestore()).preview(question)

    def test_preview_blocks_live_identity_conflicts_and_deleted_document(self) -> None:
        conflicts = {
            "qualificationId": "another-qualification",
            "listGroupId": "2025",
            "originalQuestionId": "another-original",
            "isDeleted": True,
        }
        for field, value in conflicts.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                question = question_payload()
                write_inputs(root, question)
                existing = build_doc_data_base(question["uploadReadyDocs"][0])
                existing[field] = value
                preview = self.publisher(
                    root,
                    question,
                    FakeFirestore({"doc-1": existing}),
                ).preview(question)

                self.assertFalse(preview["canPublish"])
                self.assertEqual(preview["status"], "blocked")
                self.assertEqual(preview["blockingIssues"], ["live_document_conflict"])
                self.assertEqual(preview["liveConflicts"][0]["questionId"], "doc-1")
                self.assertIn(field, preview["liveConflicts"][0]["fields"])

    def test_run_stops_before_upload_when_source_changed_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            source = write_inputs(root, question)
            firestore = FakeFirestore()
            commands = []
            publisher = self.publisher(
                root,
                question,
                firestore,
                command_runner=lambda *args, **kwargs: commands.append(args) or 0,
            )
            preview = publisher.preview(question)
            source.write_text('{"question":"changed"}\n', encoding="utf-8")

            with self.assertRaisesRegex(PublicationError, "00_source"):
                publisher.run(question, preview, lambda _line: None)

            run_dir = next(
                (root / "output/question_review_console/publish_runs/sample").iterdir()
            )
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))

        self.assertEqual(commands, [])
        self.assertEqual(result["status"], "failed")

    def test_run_stops_when_live_value_changes_inside_same_difference_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            write_inputs(root, question)
            existing = build_doc_data_base(question["uploadReadyDocs"][0])
            existing["explanationText"] = "古い解説A"
            firestore = FakeFirestore({"doc-1": existing})
            commands = []
            publisher = self.publisher(
                root,
                question,
                firestore,
                command_runner=lambda *args, **kwargs: commands.append(args) or 0,
            )
            preview = publisher.preview(question)
            firestore.documents["doc-1"]["explanationText"] = "別の古い解説B"

            with self.assertRaisesRegex(PublicationError, "Firestore"):
                publisher.run(question, preview, lambda _line: None)

        self.assertEqual(commands, [])

    def test_run_fails_receipt_when_source_changes_during_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            source = write_inputs(root, question)
            firestore = FakeFirestore()

            def run(command, *, cwd, env, emit):
                candidate = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
                for document in candidate["questions"]:
                    firestore.documents[document["questionId"]] = build_doc_data_base(
                        document
                    )
                source.write_text('{"question":"changed"}\n', encoding="utf-8")
                return 0

            publisher = self.publisher(
                root, question, firestore, command_runner=run
            )
            preview = publisher.preview(question)

            with self.assertRaisesRegex(PublicationError, "00_source"):
                publisher.run(question, preview, lambda _line: None)

            run_dir = next(
                (root / "output/question_review_console/publish_runs/sample").iterdir()
            )
            result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
            readback = json.loads(
                (run_dir / "readback.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(readback["sourceUnchanged"])

    def test_queue_preview_preserves_order_deduplicates_and_binds_item_tokens(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions = []
            for index in range(1, 4):
                question = copy.deepcopy(question_payload())
                question["id"] = f"api-q{index}"
                question["reviewKey"] = f"sample:2026:question_{index}:api-q{index}"
                question["questionLabel"] = f"問題{index}"
                questions.append(question)
            publisher = self.publisher(root, questions[0], FakeFirestore())

            def preview(question):
                question_id = str(question["id"])
                return {
                    "questionId": question_id,
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "questionLabel": question["questionLabel"],
                    "projectId": "repaso-rbaqy4",
                    "documentCount": 2,
                    "updateCount": 1,
                    "missingCount": 1,
                    "canPublish": True,
                    "preflightToken": f"token-{question_id}",
                }

            publisher.preview = preview
            first = publisher.preview_many(
                [questions[1], questions[0], questions[1], questions[2]]
            )
            second = publisher.preview_many(
                [questions[0], questions[1], questions[2]]
            )

        self.assertEqual(
            first["questionIds"],
            ["api-q2", "api-q1", "api-q3"],
        )
        self.assertEqual(first["selectedCount"], 3)
        self.assertEqual(first["documentCount"], 6)
        self.assertTrue(first["canStart"])
        self.assertTrue(
            publisher.queue_token_matches(first, first["previewToken"])
        )
        self.assertFalse(
            publisher.queue_token_matches(first, second["previewToken"])
        )
        self.assertNotEqual(first["previewToken"], second["previewToken"])

    def test_queue_preview_blocks_the_whole_selection_without_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = copy.deepcopy(question_payload())
            second = copy.deepcopy(first)
            second["id"] = "api-q2"
            second["reviewKey"] = "sample:2026:question_2:api-q2"
            second["questionLabel"] = "問題2"
            publisher = self.publisher(root, first, FakeFirestore())

            def preview(question):
                blocked = question["id"] == "api-q2"
                return {
                    "questionId": question["id"],
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "questionLabel": question["questionLabel"],
                    "canPublish": not blocked,
                    "preflightToken": "" if blocked else "token-api-q1",
                    "reason": "公開不可" if blocked else "",
                }

            publisher.preview = preview
            queue = publisher.preview_many([first, second])
            publish_calls = []
            publisher.run = lambda *args, **kwargs: publish_calls.append(args)
            with self.assertRaisesRegex(PublicationError, "一致しません"):
                publisher.run_many(
                    [first, second],
                    queue,
                    lambda _line: None,
                )

        self.assertFalse(queue["canStart"])
        self.assertEqual(queue["blockedCount"], 1)
        self.assertEqual(queue["previewToken"], "")
        self.assertEqual(
            [item["questionId"] for item in queue["items"]],
            ["api-q1", "api-q2"],
        )
        self.assertEqual(publish_calls, [])

    def test_queue_preview_requires_one_to_one_hundred_same_qualification_questions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = copy.deepcopy(question_payload())
            other_qualification = copy.deepcopy(first)
            other_qualification["id"] = "other-q"
            other_qualification["qualification"] = "other"
            publisher = self.publisher(root, first, FakeFirestore())
            publisher.preview = lambda question: {
                "questionId": question["id"],
                "qualification": question["qualification"],
                "canPublish": True,
                "preflightToken": f"token-{question['id']}",
            }

            with self.assertRaisesRegex(PublicationError, "1問以上"):
                publisher.preview_many([])
            with self.assertRaisesRegex(PublicationError, "同じ資格"):
                publisher.preview_many([first, other_qualification])
            with self.assertRaisesRegex(PublicationError, "最大100問"):
                publisher.preview_many(
                    [
                        {
                            **copy.deepcopy(first),
                            "id": f"api-q{index}",
                        }
                        for index in range(101)
                    ]
                )

    def test_queue_run_is_serial_and_continues_after_one_question_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions = []
            for index in range(1, 4):
                question = copy.deepcopy(question_payload())
                question["id"] = f"api-q{index}"
                question["reviewKey"] = f"sample:2026:question_{index}:api-q{index}"
                question["questionLabel"] = f"問題{index}"
                questions.append(question)
            publisher = self.publisher(root, questions[0], FakeFirestore())

            def preview(question):
                question_id = str(question["id"])
                return {
                    "questionId": question_id,
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "questionLabel": question["questionLabel"],
                    "documentCount": 2,
                    "updateCount": 1,
                    "missingCount": 1,
                    "canPublish": True,
                    "preflightToken": f"token-{question_id}",
                }

            publisher.preview = preview
            queue_preview = publisher.preview_many(questions)
            call_order = []
            callback_order = []
            active = 0
            peak_active = 0

            def run(question, _preflight, _emit):
                nonlocal active, peak_active
                question_id = str(question["id"])
                call_order.append(question_id)
                active += 1
                peak_active = max(peak_active, active)
                try:
                    if question_id == "api-q2":
                        error = PublicationError("2問目の公開失敗")
                        error.publish_run_id = "publish-failed-q2"
                        raise error
                    return {
                        "runId": f"publish-{question_id}",
                        "status": "succeeded",
                        "publishedCount": 2,
                        "documentCount": 2,
                        "readback": {
                            "status": "match",
                            "sourceUnchanged": True,
                            "documentCount": 2,
                            "documents": [{"questionId": f"doc-{question_id}"}],
                        },
                    }
                finally:
                    active -= 1

            publisher.run = run
            result = publisher.run_many(
                questions,
                queue_preview,
                lambda _line: None,
                on_success=lambda question, _result: callback_order.append(
                    question["id"]
                ),
            )
            queue_dir = next(
                (
                    root
                    / "output"
                    / "question_review_console"
                    / "publish_queue_runs"
                    / "sample"
                ).iterdir()
            )
            manifest = json.loads(
                (queue_dir / "manifest.json").read_text(encoding="utf-8")
            )
            stored_preflight = json.loads(
                (queue_dir / "preflight.json").read_text(encoding="utf-8")
            )
            stored_result = json.loads(
                (queue_dir / "result.json").read_text(encoding="utf-8")
            )

        self.assertEqual(call_order, ["api-q1", "api-q2", "api-q3"])
        self.assertEqual(callback_order, ["api-q1", "api-q3"])
        self.assertEqual(peak_active, 1)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["succeededCount"], 2)
        self.assertEqual(result["failedCount"], 1)
        self.assertEqual(
            [item["status"] for item in result["items"]],
            ["succeeded", "failed", "succeeded"],
        )
        self.assertEqual(
            result["items"][1]["publishRunId"],
            "publish-failed-q2",
        )
        self.assertNotIn("documents", result["items"][0]["readback"])
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(
            stored_preflight["questionIds"],
            ["api-q1", "api-q2", "api-q3"],
        )
        self.assertEqual(stored_result, result)

    def test_queue_level_failure_writes_terminal_state_for_every_question(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            questions = []
            for index in range(1, 4):
                question = copy.deepcopy(question_payload())
                question["id"] = f"api-q{index}"
                question["reviewKey"] = f"sample:2026:question_{index}:api-q{index}"
                question["questionLabel"] = f"問題{index}"
                questions.append(question)
            publisher = self.publisher(root, questions[0], FakeFirestore())
            publisher.preview = lambda question: {
                "questionId": question["id"],
                "qualification": "sample",
                "listGroupId": "2026",
                "questionLabel": question["questionLabel"],
                "documentCount": 2,
                "updateCount": 1,
                "missingCount": 1,
                "canPublish": True,
                "preflightToken": f"token-{question['id']}",
            }
            queue_preview = publisher.preview_many(questions)
            call_order = []

            def run(question, _preflight, _emit):
                question_id = str(question["id"])
                call_order.append(question_id)
                return {
                    "runId": f"publish-{question_id}",
                    "status": "succeeded",
                    "publishedCount": 2,
                    "documentCount": 2,
                    "readback": {
                        "status": "match",
                        "sourceUnchanged": True,
                        "documentCount": 2,
                    },
                }

            publisher.run = run
            write_count = 0
            write_json = publisher._write_json

            def fail_once_while_starting_second_question(path, payload):
                nonlocal write_count
                write_count += 1
                if write_count == 5:
                    raise OSError("q2開始manifestの一時書込み失敗")
                write_json(path, payload)

            publisher._write_json = fail_once_while_starting_second_question
            jobs = JobManager()
            started_job = jobs.start(
                kind="question-publish-queue",
                key=REPOSITORY_OPERATION_KEY,
                worker=lambda emit: publisher.run_many(
                    questions,
                    queue_preview,
                    emit,
                ),
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started_job["jobId"])
            while job["status"] in {"queued", "running"}:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
                job = jobs.get(started_job["jobId"])

            queue_dir = next(
                (
                    root
                    / "output"
                    / "question_review_console"
                    / "publish_queue_runs"
                    / "sample"
                ).iterdir()
            )
            manifest = json.loads(
                (queue_dir / "manifest.json").read_text(encoding="utf-8")
            )
            stored_result = json.loads(
                (queue_dir / "result.json").read_text(encoding="utf-8")
            )

        self.assertEqual(call_order, ["api-q1"])
        self.assertEqual(job["status"], "succeeded")
        self.assertIsNone(job["error"])
        result = job["result"]
        self.assertEqual(stored_result, result)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["selectedCount"], 3)
        self.assertEqual(result["succeededCount"], 1)
        self.assertEqual(result["failedCount"], 2)
        self.assertEqual(result["notRunCount"], 2)
        self.assertEqual(
            [item["status"] for item in result["items"]],
            ["succeeded", "not_run", "not_run"],
        )
        self.assertEqual(len(result["items"]), result["selectedCount"])
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["items"], result["items"])
        self.assertEqual(manifest["succeededCount"], 1)
        self.assertEqual(manifest["failedCount"], 2)
        self.assertEqual(manifest["notRunCount"], 2)
        self.assertEqual(
            manifest["queueError"],
            "q2開始manifestの一時書込み失敗",
        )
        self.assertIn("残る2問は反映せず", result["message"])
        self.assertFalse(
            {"queued", "running"}
            & {item["status"] for item in manifest["items"]}
        )

    def test_queue_level_failure_is_raised_when_terminal_receipts_cannot_save(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = copy.deepcopy(question_payload())
            publisher = self.publisher(root, question, FakeFirestore())
            publisher.preview = lambda current: {
                "questionId": current["id"],
                "qualification": "sample",
                "listGroupId": "2026",
                "questionLabel": current["questionLabel"],
                "documentCount": 2,
                "updateCount": 1,
                "missingCount": 1,
                "canPublish": True,
                "preflightToken": "token-api-q1",
            }
            queue_preview = publisher.preview_many([question])
            write_count = 0
            write_json = publisher._write_json

            def fail_from_question_start(path, payload):
                nonlocal write_count
                write_count += 1
                if write_count >= 3:
                    raise OSError("receipt storage unavailable")
                write_json(path, payload)

            publisher._write_json = fail_from_question_start
            with self.assertRaisesRegex(
                OSError,
                "receipt storage unavailable",
            ):
                publisher.run_many(
                    [question],
                    queue_preview,
                    lambda _line: None,
                )

    def test_queue_rejects_changed_order_before_creating_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = copy.deepcopy(question_payload())
            second = copy.deepcopy(first)
            second["id"] = "api-q2"
            second["reviewKey"] = "sample:2026:question_2:api-q2"
            publisher = self.publisher(root, first, FakeFirestore())
            publisher.preview = lambda question: {
                "questionId": question["id"],
                "qualification": "sample",
                "listGroupId": "2026",
                "questionLabel": question["id"],
                "canPublish": True,
                "preflightToken": f"token-{question['id']}",
            }
            queue_preview = publisher.preview_many([first, second])

            with self.assertRaisesRegex(PublicationError, "一致しません"):
                publisher.run_many(
                    [second, first],
                    queue_preview,
                    lambda _line: None,
                )
            tampered = copy.deepcopy(queue_preview)
            tampered["items"][0]["preflightToken"] = "changed-token"
            with self.assertRaisesRegex(PublicationError, "token"):
                publisher.run_many(
                    [first, second],
                    tampered,
                    lambda _line: None,
                )

            queue_root = (
                root
                / "output"
                / "question_review_console"
                / "publish_queue_runs"
            )
            self.assertFalse(queue_root.exists())


if __name__ == "__main__":
    unittest.main()
