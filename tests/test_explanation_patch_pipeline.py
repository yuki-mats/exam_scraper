from __future__ import annotations

import unittest

from scripts.check.check_explanation_patch_coverage import compare_entries
from scripts.convert.convert_merged_to_firestore import convert_true_false_to_firestore
from scripts.fix.materialize_minimal_patch import materialize_explanation


class ExplanationPatchPipelineTests(unittest.TestCase):
    def test_materialize_explanation_preserves_suggested_questions_and_law_references(self) -> None:
        source_question = {
            "public_question_id": "q123",
            "question_url": "https://example.com/q123",
        }
        raw_entry = {
            "explanationText": ["選択肢1の解説", "選択肢2の解説"],
            "suggestedQuestions": ["なぜそうなる？", "関連知識は？", "覚え方は？"],
            "suggestedQuestionDetails": [
                {"question": "なぜそうなる？", "answer": "定義の基準条文を確認すると判断できる。"},
                {"question": "関連知識は？", "answer": "似た定義との境界を合わせて覚える。"},
                {"question": "覚え方は？", "answer": "数値と対象範囲をセットで押さえる。"},
            ],
            "lawReferences": [
                [
                    {
                        "role": "current_basis",
                        "scope": "choice",
                        "choiceIndex": 0,
                        "lawTitle": "ガス事業法",
                        "referenceDate": "current",
                        "verificationStatus": "verified",
                    }
                ],
                [],
            ],
        }

        actual = materialize_explanation(source_question, raw_entry)

        self.assertEqual(actual["original_question_id"], "q123")
        self.assertEqual(actual["question_url"], "https://example.com/q123")
        self.assertEqual(actual["suggestedQuestions"], raw_entry["suggestedQuestions"])
        self.assertEqual(actual["suggestedQuestionDetails"], raw_entry["suggestedQuestionDetails"])
        self.assertEqual(actual["lawReferences"], raw_entry["lawReferences"])

    def test_compare_entries_accepts_valid_law_references(self) -> None:
        source_questions = [
            {
                "original_question_id": "q123",
                "question_url": "https://example.com/q123",
                "choiceTextList": ["肢1", "肢2"],
            }
        ]
        patch_entries = [
            {
                "original_question_id": "q123",
                "question_url": "https://example.com/q123",
                "explanationText": ["解説1", "解説2"],
                "suggestedQuestions": ["なぜそうなる？", "関連知識は？", "覚え方は？"],
                "suggestedQuestionDetails": [
                    {"question": "なぜそうなる？", "answer": "定義条文を確認すると判断できる。"},
                    {"question": "関連知識は？", "answer": "近接概念との境界で整理する。"},
                    {"question": "覚え方は？", "answer": "数値と主体をセットで覚える。"},
                ],
                "lawReferences": [
                    [
                        {
                            "role": "current_basis",
                            "scope": "choice",
                            "choiceIndex": 0,
                            "lawTitle": "ガス事業法",
                            "referenceDate": "current",
                            "verificationStatus": "verified",
                        }
                    ],
                    [],
                ],
            }
        ]

        errors, warnings = compare_entries(source_questions, patch_entries)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_compare_entries_rejects_mismatched_suggested_question_details(self) -> None:
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
                    {"question": "別の質問", "answer": "回答"},
                ],
            }
        ]

        errors, _ = compare_entries(source_questions, patch_entries)

        self.assertTrue(
            any("suggestedQuestionDetails[0].question must match suggestedQuestions[0]" in error for error in errors)
        )

    def test_convert_true_false_to_firestore_attaches_choice_law_references(self) -> None:
        question_body = {
            "original_question_id": "q123",
            "questionBodyText": "次の記述の正誤を答えよ。",
            "choiceTextList": ["肢1", "肢2"],
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["解説1", "解説2"],
            "lawReferences": [
                [
                    {
                        "role": "current_basis",
                        "scope": "choice",
                        "choiceIndex": 0,
                        "lawTitle": "ガス事業法",
                        "referenceDate": "current",
                        "verificationStatus": "verified",
                    }
                ],
                [
                    {
                        "role": "current_basis",
                        "scope": "choice",
                        "choiceIndex": 1,
                        "lawTitle": "ガス事業法施行規則",
                        "referenceDate": "current",
                        "verificationStatus": "verified",
                    }
                ],
            ],
            "examYear": 2025,
            "questionLabel": "問1",
            "qualificationName": "ガス主任技術者乙種",
            "questionSetId": "set1",
            "suggestedQuestionDetails": [
                {"question": "なぜそうなる？", "answer": "定義条文を見ると判断できる。"},
            ],
        }

        actual = convert_true_false_to_firestore(question_body)

        self.assertEqual(actual[0]["lawReferences"][0]["lawTitle"], "ガス事業法")
        self.assertEqual(actual[1]["lawReferences"][0]["lawTitle"], "ガス事業法施行規則")
        self.assertEqual(actual[0]["suggestedQuestionDetails"][0]["question"], "なぜそうなる？")


if __name__ == "__main__":
    unittest.main()
