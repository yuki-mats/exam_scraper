from __future__ import annotations

from pathlib import Path
import unittest

from scripts.check import prepare_qualification_01_04_manual_review as module
from scripts.merge.patch_views import apply_question_type


class PrepareQualification0104ManualReviewTest(unittest.TestCase):
    def test_question_intent_and_strict_answer_use_separate_patch_layers(self) -> None:
        source = Path("output/sample/questions_json/2026/00_source/question_2026_1.json")

        intent = module.patch_path_for(source, "questionIntent")
        correct = module.patch_path_for(source, "correctChoice")

        self.assertEqual(intent.parent.name, "15_correctChoiceText_fixed")
        self.assertEqual(correct.parent.name, "23_correctChoiceText_fixed")

    def test_question_id_uses_firestore_ids_before_original_question_id(self) -> None:
        question = {
            "original_question_id": "duplicated-original",
            "firestoreQuestionIds": ["doc-1", "doc-2"],
        }

        self.assertEqual(module.question_id(question), "firestore:doc-1,doc-2")

    def test_stage_entry_keeps_source_original_id_separate_from_review_id(self) -> None:
        question = {
            "original_question_id": "duplicated-original",
            "public_question_id": "public-id",
            "firestoreQuestionIds": ["doc-1"],
        }

        entry = module.stage_entry_base(Path("output/sample.json"), question)

        self.assertEqual(entry["original_question_id"], "firestore:doc-1")
        self.assertEqual(entry["source_original_question_id"], "duplicated-original")
        self.assertEqual(entry["public_question_id"], "public-id")

    def test_patch_views_apply_by_firestore_review_id(self) -> None:
        payload = {
            "question_bodies": [
                {
                    "original_question_id": "duplicated-original",
                    "public_question_id": "public-id",
                    "firestoreQuestionIds": ["doc-1"],
                    "questionType": "true_false",
                }
            ]
        }

        updated = apply_question_type(
            payload,
            {
                "firestore:doc-1": {
                    "questionType": "fill_in_blank",
                },
            },
        )

        self.assertEqual(updated, 1)
        self.assertEqual(payload["question_bodies"][0]["questionType"], "fill_in_blank")

    def test_pending_rows_allow_public_question_id_and_empty_question_intent(self) -> None:
        row = {
            "schemaVersion": module.SCHEMA_VERSION,
            "reviewId": "2025:question_2025_gassyunin_site_1:public-id",
            "qualification": "gas-shunin-kou",
            "sourceFile": "output/gas-shunin-kou/questions_json/2025/00_source/question_2025_gassyunin_site_1.json",
            "originalQuestionId": "",
            "publicQuestionId": "public-id",
            "questionUrl": "https://gassyunin.com/exam/kou/kou_2025/#law-q1",
            "questionBodyText": "既存本文",
            "questionType": "true_false",
            "questionIntent": "",
            "review01QuestionType": "pending",
            "review02QuestionIntent": "pending",
            "review02CorrectChoiceText": "pending",
            "review03ExplanationText": "pending",
            "review04QuestionSetId": "pending",
            "reviewDecision": "pending",
            "questionSetId": "",
        }

        summary, errors = module.validate_rows(
            [row],
            expected_total=1,
            allow_pending=True,
            require_stage_files=False,
            category_ids=set(),
        )

        self.assertEqual(errors, [])
        self.assertEqual(summary["rowCount"], 1)


if __name__ == "__main__":
    unittest.main()
