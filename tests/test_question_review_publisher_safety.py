from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.upload.upload_questions_to_firestore import build_doc_data_base
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


if __name__ == "__main__":
    unittest.main()
