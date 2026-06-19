from __future__ import annotations

import unittest

from scripts.pipeline import build_gas_shunin_source_key_mapping as module


class BuildGasShuninSourceKeyMappingTest(unittest.TestCase):
    def test_parse_firestore_original_question_id_maps_technical_subject(self) -> None:
        self.assertEqual(
            module.parse_firestore_original_question_id("gasushunin-koushu-gizyutsu-2023-10"),
            {
                "qualification": "gas-shunin",
                "grade": "kou",
                "year": 2023,
                "subject": "kyokyu",
                "questionNo": 10,
                "sourceSubject": "gizyutsu",
            },
        )

    def test_build_source_keys_are_deterministic_from_parts(self) -> None:
        parts = {
            "qualification": "gas-shunin",
            "grade": "otsu",
            "year": 2025,
            "subject": "law",
            "questionNo": 1,
            "statementNo": 3,
        }

        self.assertEqual(
            module.build_source_question_key(parts),
            "gas-shunin:otsu:2025:law:q01",
        )
        self.assertEqual(
            module.build_source_unique_key(parts),
            "gas-shunin:otsu:2025:law:q01:s03",
        )

    def test_gassyunin_statement_records_copy_existing_text_only(self) -> None:
        question = {
            "examYear": 2025,
            "category": "法令",
            "questionLabel": "問1",
            "questionType": "true_false",
            "questionBodyText": "既存の問題本文",
            "choiceTextList": ["既存の選択肢1", "既存の選択肢2"],
            "choiceTextMarkedList": ["既存の選択肢1", "既存の選択肢2"],
            "correctChoiceText": ["正しい", "間違い"],
            "questionImageStorageUrls": ["https://example.test/image.png"],
            "question_url": "https://gassyunin.com/exam/otsu/otsu_2025/#law-q1",
            "public_question_id": "public-id",
            "source_question_id": "2025:law:問1",
            "explanation_choice_snippets": [["既存の解説素材1"], ["既存の解説素材2"]],
        }

        questions, statements, invalid = module.build_gassyunin_question_records(
            qualification="gas-shunin-otsu",
            questions=[question],
            empty_choice_slot_count=5,
        )

        self.assertEqual(invalid, [])
        self.assertEqual(questions[0]["sourceQuestionKey"], "gas-shunin:otsu:2025:law:q01")
        self.assertEqual(len(statements), 2)
        self.assertEqual(statements[0]["sourceUniqueKey"], "gas-shunin:otsu:2025:law:q01:s01")
        self.assertEqual(statements[0]["gassyuninSource"]["choiceText"], "既存の選択肢1")
        self.assertEqual(
            statements[0]["gassyuninSource"]["explanationSources"]["gassyuninChoiceSnippet"],
            ["既存の解説素材1"],
        )

    def test_empty_choice_slots_do_not_create_choice_text(self) -> None:
        question = {
            "examYear": 2025,
            "category": "基礎理論",
            "questionLabel": "問2",
            "questionType": "flash_card",
            "questionBodyText": "既存の問題本文",
            "choiceTextList": [],
            "answer_result_inferred_correct_choice_numbers": [3],
            "question_url": "https://gassyunin.com/exam/kou/kou_2025/#kiso-q2",
        }

        _, statements, invalid = module.build_gassyunin_question_records(
            qualification="gas-shunin-kou",
            questions=[question],
            empty_choice_slot_count=5,
        )

        self.assertEqual(invalid, [])
        self.assertEqual(len(statements), 5)
        self.assertFalse(statements[0]["hasSourceChoiceText"])
        self.assertIsNone(statements[0]["gassyuninSource"]["choiceText"])
        self.assertEqual(statements[2]["sourceUniqueKey"], "gas-shunin:kou:2025:kiso:q02:s03")

    def test_firestore_records_preserve_question_ids(self) -> None:
        questions = [
            {
                "questionId": "doc-2",
                "originalQuestionId": "gasushunin-otsushu-hourei-2023-1",
                "questionText": "既存2",
            },
            {
                "questionId": "doc-1",
                "originalQuestionId": "gasushunin-otsushu-hourei-2023-1",
                "questionText": "既存1",
            },
        ]

        records, invalid = module.build_firestore_statement_records(questions)

        self.assertEqual(invalid, [])
        self.assertEqual([record["questionId"] for record in records], ["doc-1", "doc-2"])
        self.assertEqual(records[0]["sourceUniqueKey"], "gas-shunin:otsu:2023:law:q01:s01")
        self.assertEqual(records[1]["sourceUniqueKey"], "gas-shunin:otsu:2023:law:q01:s02")


if __name__ == "__main__":
    unittest.main()
