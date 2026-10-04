import copy
from pathlib import Path
import unittest

from scripts.check.run_question_content_evaluation import ContentAuditService, result_progress, select_scope, validated_content_result
from tools.question_review_console.evaluation import EvaluationError


class ContentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.question = {
            "id": "q1", "stateHash": "h1", "originalQuestionId": "original1",
            "questionLabel": "test", "qualification": "sc", "listGroupId": "201001",
            "projected": {"choiceTextList": ["命題A", "命題B"],
                          "correctChoiceText": ["正しい", "間違い"]},
        }
        self.worker = {
            "status": "passed", "explanationScore": 95, "criticalIssues": [],
            "summary": "一次根拠と全選択肢を照合した。", "reworkItems": [],
            "choiceEvaluations": [
                {"choiceIndex": index, "verdict": verdict, "reason": "確認した根拠。",
                 "evidence": [{"source": "公式資料", "locator": "第1章", "summary": "命題の根拠。"}]}
                for index, verdict in ((0, "true"), (1, "false"))
            ],
        }
        self.metadata = {"threadId": "thread1", "sessionId": "session1",
                         "turnId": "turn1", "model": "gpt-5.6-sol"}

    def test_content_pass_does_not_promote_publication_or_change_question(self):
        before = copy.deepcopy(self.question)
        result = validated_content_result(self.question, self.worker, self.metadata)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["verifiedChoiceCount"], 2)
        self.assertFalse(result["publicationReady"])
        self.assertFalse(result["publicationEvaluationPromoted"])
        self.assertEqual(self.question, before)

    def test_missing_choice_cannot_pass(self):
        self.worker["choiceEvaluations"].pop()
        with self.assertRaises(EvaluationError):
            validated_content_result(self.question, self.worker, self.metadata)

    def test_blind_verdict_disagreement_requires_rework(self):
        self.worker["choiceEvaluations"][1]["verdict"] = "true"
        result = validated_content_result(self.question, self.worker, self.metadata)
        self.assertEqual(result["status"], "needs_rework")
        self.assertFalse(result["answerMappingMatched"])

    def test_no_verified_choices_is_inconclusive(self):
        for choice in self.worker["choiceEvaluations"]:
            choice["verdict"] = "insufficient_evidence"
        result = validated_content_result(self.question, self.worker, self.metadata)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reworkItems"], [])

    def test_missing_model_session_receipt_rejected(self):
        del self.metadata["turnId"]
        with self.assertRaises(EvaluationError):
            validated_content_result(self.question, self.worker, self.metadata)

    def test_unexpected_audit_model_rejected(self):
        self.metadata["model"] = "gpt-5.6-luna"
        with self.assertRaises(EvaluationError):
            validated_content_result(self.question, self.worker, self.metadata)

    def test_failed_and_inconclusive_are_not_completed(self):
        progress = result_progress({str(i): {"status": status} for i, status in
                                   [(0, "passed"), (1, "needs_rework"), (2, "failed"), (3, "inconclusive")]})
        self.assertEqual(progress["processedCount"], 4)
        self.assertEqual(progress["completedCount"], 2)

    def test_prompt_includes_supplemental_answer_but_hides_current_answer_mapping(self):
        service = object.__new__(ContentAuditService)
        service.repo_root = Path(__file__).resolve().parents[1]
        self.question["projected"]["suggestedQuestionDetailsByChoice"] = [
            {"choiceIndex": 0, "items": [{"question": "補足の質問", "answer": "補足の回答"}]}
        ]
        prompt = service._build_prompt(self.question)
        self.assertIn("補足の回答", prompt)
        self.assertIn('"suggestedQuestionDetailsByChoice"', prompt)
        self.assertNotIn('"correctChoiceText":', prompt)

    def test_declared_image_without_bytes_receipt_rejected(self):
        self.question["projected"]["questionImageStorageUrls"] = ["https://example.com/diagram.png"]
        with self.assertRaises(EvaluationError):
            validated_content_result(self.question, self.worker, self.metadata)

    def test_recheck_subset_resolves_exact_stable_ids(self):
        second = {**self.question, "id": "q2"}
        self.assertEqual(select_scope([self.question, second], ["q2"]), [second])
        self.assertEqual(select_scope([self.question, second], ["q2", "q1"]), [second, self.question])

    def test_invalid_subset_rejected_before_model_execution(self):
        for ids in ([], ["q1", "q1"], ["other"], [None], "q1"):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                select_scope([self.question], ids)


if __name__ == "__main__":
    unittest.main()
