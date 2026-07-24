from __future__ import annotations

import unittest
from datetime import datetime

from scripts.common.explanation_references import (
    explanation_reference_errors,
    normalize_explanation_references,
)
from scripts.common.repaso_firestore_schema import validate_question_doc
from scripts.convert.convert_merged_to_firestore import (
    convert_flash_card_to_firestore,
    convert_true_false_to_firestore,
)
from scripts.upload import upload_questions_to_firestore as upload_module


class ExplanationReferencesTests(unittest.TestCase):
    def test_contract_accepts_minimal_official_reference(self) -> None:
        references = [
            {
                "title": "AWS Well-Architected Framework",
                "sourceUrl": "https://docs.aws.amazon.com/wellarchitected/latest/framework/",
                "referenceDate": "2026-07-23",
            },
            {
                "title": "Amazon EC2 On-Demand Instances",
                "sourceUrl": "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-on-demand-instances.html",
                "referenceDate": "2026-07-23",
                "choiceIndex": 1,
            },
        ]

        self.assertEqual(explanation_reference_errors(references), [])
        self.assertEqual(
            normalize_explanation_references(references, choice_index=0),
            [references[0]],
        )
        self.assertEqual(
            normalize_explanation_references(references, choice_index=1),
            references,
        )

    def test_contract_rejects_unconfirmed_or_ambiguous_metadata(self) -> None:
        references = [
            {
                "title": "候補資料",
                "sourceUrl": "http://example.com/reference",
                "referenceDate": "2026/07/23",
                "publisher": "追加fieldは保存しない",
            },
            {
                "title": "重複資料",
                "sourceUrl": "http://example.com/reference",
                "referenceDate": "2026-07-23",
            },
        ]

        errors = explanation_reference_errors(references)

        self.assertTrue(any("未定義field" in error for error in errors))
        self.assertTrue(any("HTTPS URL" in error for error in errors))
        self.assertTrue(any("YYYY-MM-DD" in error for error in errors))
        self.assertTrue(any("重複" in error for error in errors))
        self.assertEqual(normalize_explanation_references(references), [])

    def test_true_false_conversion_projects_only_matching_choice_references(self) -> None:
        shared_reference = {
            "title": "共通資料",
            "sourceUrl": "https://example.com/common",
            "referenceDate": "2026-07-23",
        }
        choice_reference = {
            "title": "選択肢2の資料",
            "sourceUrl": "https://example.com/choice-2",
            "referenceDate": "2026-07-23",
            "choiceIndex": 1,
        }
        question_body = {
            "original_question_id": "q-reference",
            "questionBodyText": "各記述の正誤を答えよ。",
            "choiceTextList": ["記述1", "記述2"],
            "correctChoiceText": ["正しい", "間違い"],
            "questionIntent": "select_correct",
            "explanationText": ["正しい。理由。", "間違い。理由。"],
            "explanationReferences": [shared_reference, choice_reference],
            "questionLabel": "問1",
            "qualificationName": "試験資格",
            "questionSetId": "set-reference",
            "examYear": 2026,
        }

        actual = convert_true_false_to_firestore(question_body)

        self.assertEqual(actual[0]["explanationReferences"], [shared_reference])
        self.assertEqual(
            actual[1]["explanationReferences"],
            [shared_reference, choice_reference],
        )

    def test_choice_only_documents_omit_explanation_references(self) -> None:
        reference = {
            "title": "公式資料",
            "sourceUrl": "https://example.com/reference",
            "referenceDate": "2026-07-23",
        }
        question_body = {
            "original_question_id": "q-reference-flash-card",
            "questionBodyText": "正しいものを選べ。",
            "choiceTextList": ["正答", "誤答"],
            "correctChoiceText": ["正しい", "間違い"],
            "questionIntent": "select_correct",
            "explanationText": ["共通解説。"],
            "explanationReferences": [reference],
            "questionLabel": "問2",
            "qualificationName": "試験資格",
            "questionSetId": "set-reference",
            "examYear": 2026,
        }

        actual = convert_flash_card_to_firestore(question_body)
        public_document = next(item for item in actual if not item["isChoiceOnly"])
        choice_only_document = next(item for item in actual if item["isChoiceOnly"])

        self.assertEqual(public_document["explanationReferences"], [reference])
        self.assertNotIn("explanationReferences", choice_only_document)

    def test_upload_schema_accepts_reference_contract(self) -> None:
        now = datetime(2026, 7, 23, 12, 0, 0)
        reference = {
            "title": "公式資料",
            "sourceUrl": "https://example.com/reference",
            "referenceDate": "2026-07-23",
        }
        doc = upload_module.build_doc_data(
            {
                "questionId": "q-reference",
                "questionSetId": "set-reference",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "sample-qualification",
                "questionTags": [],
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "記述",
                "explanationReferences": [reference],
            },
            now,
        )

        validate_question_doc(doc, doc_id="q-reference")
        self.assertEqual(doc["explanationReferences"], [reference])


if __name__ == "__main__":
    unittest.main()
