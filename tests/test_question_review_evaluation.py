import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from scripts.upload.upload_questions_to_firestore import build_doc_data_base
from tools.question_review_console.evaluation import (
    EvaluationError,
    QuestionEvaluationService,
)
from tools.question_review_console.publisher import PublicationError, QuestionPublisher


def question_payload(*, question_id="api-q1", body="問題1", state_hash="state-1"):
    documents = [
        upload_document("doc-1", "original-1", "選択肢A", "正しい"),
        upload_document("doc-2", "original-1", "選択肢B", "間違い"),
    ]
    return {
        "id": question_id,
        "reviewKey": f"sample:2026:question_1:{question_id}",
        "sourceQuestionKey": f"sample:{question_id}",
        "qualification": "sample",
        "publicationQualificationId": "sample",
        "listGroupId": "2026",
        "originalQuestionId": "original-1",
        "questionLabel": body,
        "body": body,
        "choiceCount": 2,
        "stateHash": state_hash,
        "issueCodes": [],
        "workflow": {"merge": "match", "convert": "match", "upload": "match"},
        "projected": {
            "questionBodyText": body,
            "questionIntent": "select_correct",
            "choiceTextList": ["選択肢A", "選択肢B"],
            "correctChoiceText": ["正しい", "間違い"],
            "answer_result_text": "正解は1",
            "explanationText": ["Aの解説", "Bの解説"],
        },
        "uploadReadyDocs": documents,
        "paths": {
            "source": "output/sample/questions_json/2026/00_source/question_1.json",
            "uploadReady": (
                "output/sample/questions_json/upload_to_firestore/"
                "2026_firestore_20260714_120000.json"
            ),
        },
    }


def evaluation_result(*, first_verdict="true", status="passed"):
    return {
        "status": status,
        "answerMappingMatched": True,
        "explanationScore": 94,
        "criticalIssues": [],
        "summary": "全選択肢の正誤と解説を確認した。",
        "choiceEvaluations": [
            {
                "choiceIndex": 0,
                "verdict": first_verdict,
                "reason": "一次資料と一致する。",
                "evidence": [
                    {
                        "source": "公式資料",
                        "locator": "第1章 1頁",
                        "summary": "選択肢Aを裏付ける。",
                    }
                ],
            },
            {
                "choiceIndex": 1,
                "verdict": "false",
                "reason": "定義と一致しない。",
                "evidence": [
                    {
                        "source": "公式資料",
                        "locator": "第1章 2頁",
                        "summary": "選択肢Bの誤りを示す。",
                    }
                ],
            },
        ],
        "reworkItems": [],
    }


def upload_document(question_id, original_id, choice, verdict):
    return {
        "questionId": question_id,
        "originalQuestionId": original_id,
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


class QuestionEvaluationServiceTests(unittest.TestCase):
    def test_saves_passed_result_and_marks_it_stale_after_question_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = QuestionEvaluationService(
                root,
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            question = question_payload()
            preview = service.preview(question)
            result = service.run(
                question, preview["previewToken"], lambda _line: None
            )["evaluation"]

            current = service.status_for(question)
            changed = copy.deepcopy(question)
            changed["stateHash"] = "state-2"
            stale = service.status_for(changed)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["verifiedChoiceCount"], 2)
        self.assertTrue(current["publishReady"])
        self.assertEqual(stale["status"], "stale")
        self.assertFalse(stale["publishReady"])

    def test_server_recomputes_failure_when_reported_pass_disagrees_with_current_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(
                    first_verdict="false", status="passed"
                ),
            )
            question = question_payload()
            preview = service.preview(question)
            result = service.run(
                question, preview["previewToken"], lambda _line: None
            )["evaluation"]

        self.assertEqual(result["reportedStatus"], "passed")
        self.assertEqual(result["status"], "needs_rework")
        self.assertFalse(result["answerMappingMatched"])
        self.assertFalse(result["choiceEvaluations"][0]["matchesCurrent"])

    def test_tampered_evaluation_result_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = QuestionEvaluationService(
                root,
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            question = question_payload()
            preview = service.preview(question)
            service.run(question, preview["previewToken"], lambda _line: None)
            path = service.store.evaluation_path(question)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["explanationScore"] = 0
            path.write_text(json.dumps(payload), encoding="utf-8")

            status = service.status_for(question)

        self.assertEqual(status["status"], "not_started")
        self.assertFalse(status["publishReady"])

    def test_batch_uses_a_separate_runner_call_per_question_and_continues_after_failure(self):
        calls = []

        def runner(prompt):
            calls.append(prompt)
            if "問題2" in prompt:
                raise RuntimeError("evaluation failed")
            return evaluation_result()

        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory), "secret", result_runner=runner
            )
            first = question_payload()
            second = question_payload(
                question_id="api-q2", body="問題2", state_hash="state-2"
            )
            second["reviewKey"] = "sample:2026:question_2:api-q2"
            preview = service.preview_many([first, second])
            result = service.run_many(
                [first, second], preview["previewToken"], lambda _line: None
            )

        self.assertEqual(preview["sessionCount"], 2)
        self.assertEqual(preview["qualification"], "sample")
        self.assertEqual(preview["listGroupIds"], ["2026"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["completedCount"], 1)
        self.assertEqual(result["failedCount"], 1)
        self.assertEqual(result["passedCount"], 1)

    def test_batch_rejects_questions_from_different_qualifications(self):
        with tempfile.TemporaryDirectory() as directory:
            service = QuestionEvaluationService(
                Path(directory),
                "secret",
                result_runner=lambda _prompt: evaluation_result(),
            )
            first = question_payload()
            second = question_payload(question_id="api-q2", body="問題2")
            second["qualification"] = "other"
            second["reviewKey"] = "other:2026:question_2:api-q2"

            with self.assertRaisesRegex(EvaluationError, "同じ資格"):
                service.preview_many([first, second])


class FakeInventory:
    def __init__(self, question):
        self.question = question

    def group(self, qualification, list_group_id):
        return {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "questions": [self.question],
        }


class FakeEvaluationService:
    def status_for(self, _question):
        return {
            "status": "passed",
            "publishReady": True,
            "resultHash": "evaluation-hash",
            "machineReady": True,
            "blockingIssues": [],
        }


class FakeFirestore:
    def __init__(self):
        self.documents = {}

    def read_documents(self, document_ids, *, fields=None):
        return {
            question_id: copy.deepcopy(self.documents[question_id])
            for question_id in document_ids
            if question_id in self.documents
        }


class QuestionPublisherTests(unittest.TestCase):
    def test_uploads_only_documents_for_the_selected_original_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            question["uploadReadyDocs"][0].pop("isDeleted")
            artifact = root / question["paths"]["uploadReady"]
            artifact.parent.mkdir(parents=True)
            other = upload_document("doc-other", "original-other", "他の選択肢", "正しい")
            artifact.write_text(
                json.dumps(
                    {"questions": [*question["uploadReadyDocs"], other]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_hash = artifact.read_bytes()
            firestore = FakeFirestore()
            commands = []

            def run(command, *, cwd, env, emit):
                commands.append(command)
                candidate = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
                self.assertEqual(
                    [item["questionId"] for item in candidate["questions"]],
                    ["doc-1", "doc-2"],
                )
                for document in candidate["questions"]:
                    firestore.documents[document["questionId"]] = build_doc_data_base(
                        document
                    )
                emit("uploaded")
                return 0

            publisher = QuestionPublisher(
                root,
                FakeInventory(question),
                firestore,
                FakeEvaluationService(),
                "secret",
                command_runner=run,
            )
            preview = publisher.preview(question)
            result = publisher.run(question, preview, lambda _line: None)

            self.assertEqual(preview["documentCount"], 2)
            self.assertEqual(preview["missingCount"], 2)
            self.assertEqual(result["status"], "succeeded")
            self.assertNotEqual(Path(commands[0][-1]).resolve(), artifact.resolve())
            self.assertEqual(artifact.read_bytes(), original_hash)
            self.assertNotIn("doc-other", firestore.documents)
            result_path = next(
                (root / "output" / "question_review_console" / "publish_runs").glob(
                    "sample/*/result.json"
                )
            )
            self.assertEqual(
                json.loads(result_path.read_text(encoding="utf-8"))["status"],
                "succeeded",
            )

    def test_rejects_documents_for_a_different_publication_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = question_payload()
            question["publicationQualificationId"] = "published-sample"
            artifact = root / question["paths"]["uploadReady"]
            artifact.parent.mkdir(parents=True)
            artifact.write_text(
                json.dumps({"questions": question["uploadReadyDocs"]}),
                encoding="utf-8",
            )
            publisher = QuestionPublisher(
                root,
                FakeInventory(question),
                FakeFirestore(),
                FakeEvaluationService(),
                "secret",
            )

            with self.assertRaisesRegex(PublicationError, "別資格"):
                publisher.preview(question)


if __name__ == "__main__":
    unittest.main()
