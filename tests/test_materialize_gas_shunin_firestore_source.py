from __future__ import annotations

import unittest

from scripts.pipeline import materialize_gas_shunin_firestore_source as module


class MaterializeGasShuninFirestoreSourceTest(unittest.TestCase):
    def test_build_source_question_groups_choice_and_correct_text_without_rewriting(self) -> None:
        group = [
            {
                "questionId": "doc-2",
                "originalQuestionId": "gasushunin-koushu-hourei-2023-1",
                "originalQuestionBodyText": "既存本文",
                "originalQuestionChoiceText": "既存選択肢ロ",
                "correctChoiceText": "間違い",
                "questionType": "true_false",
                "examYear": 2023,
                "examSource": "ガス主任技術者, 2023年, 問番号：1, 設問番号：ロ",
                "explanationText": "既存解説ロ",
                "isChoiceOnly": False,
                "isDeleted": False,
                "qualificationId": "chiefgasengineerlicense",
                "questionSetId": "set-1",
            },
            {
                "questionId": "doc-1",
                "originalQuestionId": "gasushunin-koushu-hourei-2023-1",
                "originalQuestionBodyText": "既存本文",
                "originalQuestionChoiceText": "既存選択肢イ",
                "correctChoiceText": "正しい",
                "questionType": "true_false",
                "examYear": 2023,
                "examSource": "ガス主任技術者, 2023年, 問番号：1, 設問番号：イ",
                "explanationText": "既存解説イ",
                "isChoiceOnly": False,
                "isDeleted": False,
                "qualificationId": "chiefgasengineerlicense",
                "questionSetId": "set-1",
            },
        ]

        record = module.build_source_question(group)

        self.assertEqual(record["questionBodyText"], "既存本文")
        self.assertEqual(record["choiceTextList"], ["既存選択肢イ", "既存選択肢ロ"])
        self.assertEqual(record["correctChoiceText"], ["正しい", "間違い"])
        self.assertEqual(record["explanation_choice_snippets"], [["既存解説イ"], ["既存解説ロ"]])
        self.assertEqual(record["firestoreQuestionIds"], ["doc-1", "doc-2"])
        self.assertEqual(record["questionLabel"], "問1")
        self.assertEqual(record["category"], "法令")

    def test_question_image_urls_are_preserved_as_storage_urls(self) -> None:
        record = module.build_source_question(
            [
                {
                    "questionId": "doc-1",
                    "originalQuestionId": "gasushunin-koushu-gizyutsu-2023-10",
                    "originalQuestionBodyText": "既存本文",
                    "originalQuestionChoiceText": "既存選択肢",
                    "correctChoiceText": "正しい",
                    "questionType": "flash_card",
                    "questionIntent": "select_correct",
                    "isChoiceOnly": False,
                    "examYear": 2023,
                    "questionImageUrls": ["https://example.test/a.png"],
                }
            ]
        )

        self.assertEqual(record["sourceSubject"], "kyokyu")
        self.assertEqual(record["category"], "供給")
        self.assertEqual(record["questionImageStorageUrls"], ["https://example.test/a.png"])

    def test_select_incorrect_split_documents_restore_intrinsic_verdicts(self) -> None:
        record = module.build_source_question(
            [
                {
                    "questionId": "doc-3",
                    "originalQuestionId": "gasushunin-koushu-hourei-2023-1",
                    "originalQuestionBodyText": "誤っているものはどれか。",
                    "originalQuestionChoiceText": "正しい肢C",
                    "correctChoiceText": "間違い",
                    "questionType": "group_choice",
                    "questionIntent": "select_incorrect",
                    "isChoiceOnly": True,
                    "examYear": 2023,
                },
                {
                    "questionId": "doc-1",
                    "originalQuestionId": "gasushunin-koushu-hourei-2023-1",
                    "originalQuestionBodyText": "誤っているものはどれか。",
                    "originalQuestionChoiceText": "正しい肢A",
                    "correctChoiceText": "間違い",
                    "questionType": "group_choice",
                    "questionIntent": "select_incorrect",
                    "isChoiceOnly": True,
                    "examYear": 2023,
                },
                {
                    "questionId": "doc-2",
                    "originalQuestionId": "gasushunin-koushu-hourei-2023-1",
                    "originalQuestionBodyText": "誤っているものはどれか。",
                    "originalQuestionChoiceText": "誤った肢B",
                    "correctChoiceText": "正しい",
                    "questionType": "group_choice",
                    "questionIntent": "select_incorrect",
                    "isChoiceOnly": False,
                    "examYear": 2023,
                },
            ]
        )

        self.assertEqual(
            record["choiceTextList"],
            ["正しい肢A", "誤った肢B", "正しい肢C"],
        )
        self.assertEqual(
            record["correctChoiceText"],
            ["正しい", "間違い", "正しい"],
        )
        self.assertEqual(record["questionIntent"], "select_incorrect")
        self.assertEqual(
            record["answer_result_inferred_correct_choice_numbers"],
            [2],
        )

    def test_question_level_restore_requires_explicit_question_intent(self) -> None:
        with self.assertRaisesRegex(ValueError, "questionIntent"):
            module.build_source_question(
                [
                    {
                        "questionId": "doc-1",
                        "originalQuestionId": "gasushunin-koushu-hourei-2023-1",
                        "originalQuestionBodyText": "正しいものはどれか。",
                        "originalQuestionChoiceText": "選択肢A",
                        "correctChoiceText": "正しい",
                        "questionType": "group_choice",
                        "isChoiceOnly": False,
                        "examYear": 2023,
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
