from __future__ import annotations

import unittest
from datetime import datetime

from scripts.check.check_explanation_patch_coverage import compare_entries
from scripts.common.repaso_firestore_schema import validate_question_doc
from scripts.convert.convert_merged_to_firestore import convert_true_false_to_firestore
from scripts.fix.materialize_minimal_patch import materialize_explanation
from scripts.upload import upload_questions_to_firestore as upload_module


class LawGroundedExplanationNotNeededPipelineTests(unittest.TestCase):
    def test_materialize_explanation_preserves_law_grounded_flag(self) -> None:
        source_question = {
            "public_question_id": "q123",
            "question_url": "https://example.com/q123",
        }
        raw_entry = {
            "explanationText": ["医学知識で判断できる。"],
            "suggestedQuestions": ["なぜこの疾患で起こる？"],
            "suggestedQuestionDetails": [
                {
                    "question": "なぜこの疾患で起こる？",
                    "answer": "病態生理から説明でき、条文確認は学習上不要である。",
                },
            ],
            "lawGroundedExplanationNotNeeded": True,
        }

        actual = materialize_explanation(source_question, raw_entry)

        self.assertIs(actual["lawGroundedExplanationNotNeeded"], True)

    def test_compare_entries_rejects_non_bool_law_grounded_flag(self) -> None:
        source_questions = [
            {
                "original_question_id": "q123",
                "question_url": "https://example.com/q123",
                "choiceTextList": ["肢1"],
            }
        ]
        patch_entries = [
            {
                "original_question_id": "q123",
                "question_url": "https://example.com/q123",
                "explanationText": ["解説1"],
                "suggestedQuestions": ["なぜそうなる？"],
                "suggestedQuestionDetails": [
                    {"question": "なぜそうなる？", "answer": "医学知識で判断する。"},
                ],
                "lawGroundedExplanationNotNeeded": "true",
            }
        ]

        errors, _ = compare_entries(source_questions, patch_entries)

        self.assertTrue(
            any("lawGroundedExplanationNotNeeded must be bool" in error for error in errors)
        )

    def test_compare_entries_rejects_true_flag_with_law_references(self) -> None:
        source_questions = [
            {
                "original_question_id": "q123",
                "question_url": "https://example.com/q123",
                "choiceTextList": ["肢1"],
            }
        ]
        patch_entries = [
            {
                "original_question_id": "q123",
                "question_url": "https://example.com/q123",
                "explanationText": ["解説1"],
                "suggestedQuestions": ["なぜそうなる？"],
                "suggestedQuestionDetails": [
                    {"question": "なぜそうなる？", "answer": "条文で確認できる。"},
                ],
                "lawReferences": [
                    [
                        {
                            "role": "current_basis",
                            "scope": "choice",
                            "choiceIndex": 0,
                            "lawId": "323AC0000000205",
                            "lawTitle": "医師法",
                            "article": "19",
                            "referenceDate": "2026-06-08",
                            "verificationStatus": "verified",
                        }
                    ],
                ],
                "lawGroundedExplanationNotNeeded": True,
            }
        ]

        errors, _ = compare_entries(source_questions, patch_entries)

        self.assertTrue(
            any("lawGroundedExplanationNotNeeded cannot be true" in error for error in errors)
        )

    def test_convert_true_false_to_firestore_copies_question_level_flag(self) -> None:
        question_body = {
            "original_question_id": "q123",
            "questionBodyText": "次の記述の正誤を答えよ。",
            "choiceTextList": ["肢1", "肢2"],
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["解説1", "解説2"],
            "examYear": 2026,
            "questionLabel": "問1",
            "qualificationName": "医師国家試験",
            "questionSetId": "set1",
            "lawGroundedExplanationNotNeeded": True,
        }

        actual = convert_true_false_to_firestore(question_body)

        self.assertEqual(len(actual), 2)
        self.assertTrue(all(q["lawGroundedExplanationNotNeeded"] for q in actual))

    def test_upload_schema_accepts_bool_law_grounded_flag(self) -> None:
        now = datetime(2026, 6, 8, 12, 0, 0)
        doc = upload_module.build_doc_data(
            {
                "questionId": "qsample",
                "questionSetId": "qs1",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "mecnet-kokushi",
                "questionTags": [],
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "選択肢",
                "examYear": 2026,
                "lawGroundedExplanationNotNeeded": False,
            },
            now,
        )

        validate_question_doc(doc, doc_id="qsample")
        self.assertIs(doc["lawGroundedExplanationNotNeeded"], False)

    def test_upload_schema_rejects_non_bool_law_grounded_flag(self) -> None:
        now = datetime(2026, 6, 8, 12, 0, 0)
        doc = upload_module.build_doc_data(
            {
                "questionId": "qsample",
                "questionSetId": "qs1",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "mecnet-kokushi",
                "questionTags": [],
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "選択肢",
                "examYear": 2026,
                "lawGroundedExplanationNotNeeded": "false",
            },
            now,
        )

        with self.assertRaisesRegex(
            ValueError,
            "lawGroundedExplanationNotNeeded must be bool",
        ):
            validate_question_doc(doc, doc_id="qsample")


if __name__ == "__main__":
    unittest.main()
