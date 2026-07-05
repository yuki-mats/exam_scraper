from __future__ import annotations

import unittest

from scripts.convert.convert_merged_to_firestore import (
    convert_flash_card_to_firestore,
    convert_true_false_to_firestore,
    get_exam_name,
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

    def test_true_false_preserves_existing_statement_level_question_set_ids(self) -> None:
        question_body = {
            "original_question_id": "gasushunin-koushu-gizyutsu-2019-1",
            "firestoreQuestionIds": ["doc-1", "doc-2"],
            "firestoreSourceQuestions": [
                {"questionId": "doc-1", "questionSetId": "qset-statement-1"},
                {"questionId": "doc-2", "questionSetId": "qset-statement-2"},
            ],
            "questionBodyText": "誤っているものはいくつあるか。",
            "choiceTextList": ["記述1", "記述2"],
            "correctChoiceText": ["間違い", "正しい"],
            "explanationText": ["説明1", "説明2"],
            "questionType": "true_false",
            "questionSetId": "",
            "examYear": 2019,
            "questionLabel": "問1",
        }

        converted = convert_true_false_to_firestore(question_body)

        self.assertEqual(
            [item["questionSetId"] for item in converted],
            ["qset-statement-1", "qset-statement-2"],
        )

    def test_true_false_uses_explicit_choice_question_set_ids_for_new_site_docs(self) -> None:
        question_body = {
            "original_question_id": None,
            "public_question_id": "site-public-1",
            "sourceUniqueKeys": [
                "gas-shunin:kou:2024:law:q03:s01",
                "gas-shunin:kou:2024:law:q03:s02",
            ],
            "choiceQuestionSetIds": ["qset-choice-1", "qset-choice-2"],
            "questionBodyText": "誤っているものはいくつあるか。",
            "choiceTextList": ["記述1", "記述2"],
            "correctChoiceText": ["間違い", "正しい"],
            "explanationText": ["説明1", "説明2"],
            "questionType": "true_false",
            "questionSetId": "",
            "examYear": 2024,
            "questionLabel": "問3",
        }

        converted = convert_true_false_to_firestore(question_body)

        self.assertEqual(
            [item["questionId"] for item in converted],
            [
                "gas-shunin-kou-2024-law-q03-s01",
                "gas-shunin-kou-2024-law-q03-s02",
            ],
        )
        self.assertEqual(
            [item["originalQuestionId"] for item in converted],
            ["site-public-1", "site-public-1"],
        )
        self.assertEqual(
            [item["questionSetId"] for item in converted],
            ["qset-choice-1", "qset-choice-2"],
        )

    def test_exam_name_is_inferred_from_gas_shunin_source_key(self) -> None:
        self.assertEqual(
            get_exam_name({"sourceQuestionKey": "gas-shunin:kou:2024:law:q01"}),
            "ガス主任技術者（甲種）",
        )
        self.assertEqual(
            get_exam_name({"sourceUniqueKeys": ["gas-shunin:otsu:2025:kiso:q02:s01"]}),
            "ガス主任技術者（乙種）",
        )

    def test_true_false_ignores_misaligned_statement_level_question_set_ids(self) -> None:
        question_body = {
            "original_question_id": "gasushunin-koushu-gizyutsu-2019-2",
            "firestoreQuestionIds": ["doc-1", "doc-2"],
            "firestoreSourceQuestions": [
                {"questionId": "other-doc", "questionSetId": "qset-statement-1"},
                {"questionId": "doc-2", "questionSetId": "qset-statement-2"},
            ],
            "questionBodyText": "正しいものはどれか。",
            "choiceTextList": ["記述1", "記述2"],
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["説明1", "説明2"],
            "questionType": "true_false",
            "questionSetId": "qset-problem",
            "examYear": 2019,
            "questionLabel": "問2",
        }

        converted = convert_true_false_to_firestore(question_body)

        self.assertEqual(
            [item["questionSetId"] for item in converted],
            ["qset-problem", "qset-statement-2"],
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

    def test_flash_card_falls_back_to_group_law_references_for_law_related_wrong_choices(self) -> None:
        question_body = {
            "original_question_id": "site-law-question-1",
            "firestoreQuestionIds": ["doc-correct", "doc-wrong-1"],
            "questionBodyText": "現行法上、正しい数値はどれか。",
            "choiceTextList": ["正答", "誤答"],
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["説明1", "説明2"],
            "questionType": "flash_card",
            "questionSetId": "qset-law",
            "examYear": 2025,
            "questionLabel": "問10",
            "isLawRelated": True,
            "lawReferences": [
                [
                    {
                        "role": "current_basis",
                        "lawId": "325AC0000000201",
                        "lawTitle": "建築基準法",
                        "article": "52条",
                        "paragraph": "2項",
                    }
                ],
                [],
            ],
        }

        converted = convert_flash_card_to_firestore(question_body)

        self.assertEqual(converted[0]["lawReferences"][0]["article"], "52条")
        self.assertEqual(converted[1]["lawReferences"][0]["article"], "52条")

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
