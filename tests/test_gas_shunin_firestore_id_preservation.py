from __future__ import annotations

import unittest

from scripts.convert.convert_merged_to_firestore import (
    convert_flash_card_to_firestore,
    convert_true_false_to_firestore,
)


class GasShuninFirestoreIdPreservationTest(unittest.TestCase):
    def test_true_false_uses_existing_firestore_ids_by_choice_order(self) -> None:
        question_body = {
            "original_question_id": "gasushunin-koushu-hourei-2020-1",
            "firestoreQuestionIds": ["doc-1", "doc-2"],
            "questionBodyText": "正しいものはどれか。",
            "choiceTextList": ["選択肢1", "選択肢2"],
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["説明1", "説明2"],
            "questionType": "true_false",
            "questionSetId": "qset-1",
            "examYear": 2020,
            "questionLabel": "問1",
        }

        converted = convert_true_false_to_firestore(question_body)

        self.assertEqual([item["questionId"] for item in converted], ["doc-1", "doc-2"])
        self.assertEqual(
            [item["originalQuestionId"] for item in converted],
            ["gasushunin-koushu-hourei-2020-1", "gasushunin-koushu-hourei-2020-1"],
        )

    def test_flash_card_uses_existing_firestore_ids_by_choice_order(self) -> None:
        question_body = {
            "original_question_id": "gasushunin-koushu-kiso-2020-14",
            "firestoreQuestionIds": ["doc-correct", "doc-wrong-1", "doc-wrong-2"],
            "questionBodyText": "該当するものはどれか。",
            "choiceTextList": ["正答", "誤答1", "誤答2"],
            "correctChoiceText": ["正しい", "間違い", "間違い"],
            "explanationText": ["説明1", "説明2", "説明3"],
            "questionType": "flash_card",
            "questionSetId": "qset-2",
            "examYear": 2020,
            "questionLabel": "問14",
        }

        converted = convert_flash_card_to_firestore(question_body)

        self.assertEqual(
            [item["questionId"] for item in converted],
            ["doc-correct", "doc-wrong-1", "doc-wrong-2"],
        )
        self.assertEqual(
            [item["originalQuestionId"] for item in converted],
            [
                "gasushunin-koushu-kiso-2020-14",
                "gasushunin-koushu-kiso-2020-14",
                "gasushunin-koushu-kiso-2020-14",
            ],
        )

    def test_true_false_new_questions_use_source_unique_key_ids(self) -> None:
        question_body = {
            "original_question_id": "site-question-1",
            "sourceUniqueKeys": [
                "gas-shunin:kou:2024:law:q01:s01",
                "gas-shunin:kou:2024:law:q01:s02",
            ],
            "questionBodyText": "正しいものはどれか。",
            "choiceTextList": ["選択肢1", "選択肢2"],
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["説明1", "説明2"],
            "questionType": "true_false",
            "questionSetId": "qset-1",
            "examYear": 2024,
            "questionLabel": "問1",
        }

        converted = convert_true_false_to_firestore(question_body)

        self.assertEqual(
            [item["questionId"] for item in converted],
            [
                "gas-shunin-kou-2024-law-q01-s01",
                "gas-shunin-kou-2024-law-q01-s02",
            ],
        )
        self.assertEqual(
            [item["originalQuestionId"] for item in converted],
            ["site-question-1", "site-question-1"],
        )

    def test_flash_card_new_questions_use_source_unique_key_ids(self) -> None:
        question_body = {
            "original_question_id": "site-question-2",
            "sourceUniqueKeys": [
                "gas-shunin:otsu:2025:kiso:q02:s01",
                "gas-shunin:otsu:2025:kiso:q02:s02",
                "gas-shunin:otsu:2025:kiso:q02:s03",
            ],
            "questionBodyText": "該当するものはどれか。",
            "choiceTextList": ["正答", "誤答1", "誤答2"],
            "correctChoiceText": ["正しい", "間違い", "間違い"],
            "explanationText": ["説明1", "説明2", "説明3"],
            "questionType": "flash_card",
            "questionSetId": "qset-2",
            "examYear": 2025,
            "questionLabel": "問2",
        }

        converted = convert_flash_card_to_firestore(question_body)

        self.assertEqual(
            [item["questionId"] for item in converted],
            [
                "gas-shunin-otsu-2025-kiso-q02-s01",
                "gas-shunin-otsu-2025-kiso-q02-s02",
                "gas-shunin-otsu-2025-kiso-q02-s03",
            ],
        )


if __name__ == "__main__":
    unittest.main()
