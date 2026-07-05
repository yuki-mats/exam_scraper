from __future__ import annotations

import unittest
from datetime import datetime

from scripts.check.check_explanation_patch_coverage import compare_entries
from scripts.common.repaso_firestore_schema import validate_question_doc
from scripts.convert.convert_merged_to_firestore import convert_true_false_to_firestore
from scripts.fix.materialize_minimal_patch import materialize_explanation
from scripts.upload import upload_questions_to_firestore as upload_module


def valid_law_revision_facts(audit_status: str = "same_as_current") -> dict:
    return {
        "auditStatus": audit_status,
        "reviewState": "secondary_verified",
        "sourceEvidenceVersionId": "version-1",
        "evidenceBindingHash": "binding-hash",
        "current": {
            "correctChoiceText": "正しい",
            "lawId": "325AC0000000201",
            "lawRevisionId": "current-revision",
            "lawTitle": "建築基準法",
            "article": "2",
            "referenceDate": "2026-07-05",
            "verificationStatus": "verified",
            "articleTextHash": "article-hash",
        },
        "differenceFacts": ["現行法上の根拠条文を確認済み。"],
        "answerImpactFacts": ["出題当時の正答と現行法ベースの正答は同じ。"],
        "evidenceSummary": {
            "verdict": "correct",
            "explanationText": "現行法でも正しいです。",
            "differenceSummary": "正誤に影響する差分はありません。",
            "promptContext": "監査済み根拠に基づいて説明する。",
            "displayRefIds": ["current_basis_Art2"],
            "refs": [
                {
                    "refId": "current_basis_Art2",
                    "lawTimeScope": "current",
                    "relation": "basis",
                    "primaryBasis": True,
                    "lawId": "325AC0000000201",
                    "lawRevisionId": "current-revision",
                    "lawTitle": "建築基準法",
                    "elm": "MainProvision-Article_2",
                    "rootArticleElm": "MainProvision-Article_2",
                    "article": "2",
                    "highlightElms": ["MainProvision-Article_2-Paragraph_1"],
                    "articleTextHash": "article-hash",
                    "textHash": "segment-hash",
                }
            ],
        },
    }


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

    def test_materialize_explanation_preserves_law_revision_facts(self) -> None:
        source_question = {
            "public_question_id": "q123",
            "question_url": "https://example.com/q123",
        }
        facts = valid_law_revision_facts()
        raw_entry = {
            "explanationText": ["条文根拠を踏まえた解説。"],
            "suggestedQuestions": ["現行法ではどう考える？"],
            "suggestedQuestionDetails": [
                {
                    "question": "現行法ではどう考える？",
                    "answer": "監査済みの現行法根拠では正しいです。",
                },
            ],
            "lawRevisionFacts": facts,
        }

        actual = materialize_explanation(source_question, raw_entry)

        self.assertEqual(actual["lawRevisionFacts"], facts)

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

    def test_compare_entries_requires_law_grounded_flag_when_requested(self) -> None:
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
            }
        ]

        errors, _ = compare_entries(
            source_questions,
            patch_entries,
            require_law_grounded_flag=True,
        )

        self.assertTrue(
            any("missing lawGroundedExplanationNotNeeded" in error for error in errors)
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

    def test_convert_true_false_to_firestore_copies_choice_law_revision_facts(self) -> None:
        question_body = {
            "original_question_id": "q123",
            "questionBodyText": "次の記述の正誤を答えよ。",
            "choiceTextList": ["肢1", "肢2"],
            "correctChoiceText": ["正しい", "間違い"],
            "explanationText": ["解説1", "解説2"],
            "examYear": 2026,
            "questionLabel": "問1",
            "qualificationName": "二級建築士",
            "questionSetId": "set1",
            "lawRevisionFacts": [
                valid_law_revision_facts("same_as_current"),
                valid_law_revision_facts("updated_to_current_law"),
            ],
        }

        actual = convert_true_false_to_firestore(question_body)

        self.assertEqual(len(actual), 2)
        self.assertEqual(
            actual[0]["lawRevisionFacts"]["auditStatus"],
            "same_as_current",
        )
        self.assertEqual(
            actual[1]["lawRevisionFacts"]["auditStatus"],
            "updated_to_current_law",
        )

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

    def test_upload_schema_accepts_law_revision_facts(self) -> None:
        now = datetime(2026, 7, 5, 12, 0, 0)
        doc = upload_module.build_doc_data(
            {
                "questionId": "qsample",
                "questionSetId": "qs1",
                "questionText": "本文",
                "questionType": "true_false",
                "qualificationId": "2nd-class-kenchikushi",
                "questionTags": [],
                "originalQuestionBodyText": "元問題文",
                "originalQuestionChoiceText": "選択肢",
                "examYear": 2026,
                "isLawRelated": True,
                "lawRevisionFacts": valid_law_revision_facts(),
            },
            now,
        )

        validate_question_doc(doc, doc_id="qsample")
        self.assertTrue(doc["isLawRelated"])
        self.assertEqual(
            doc["lawRevisionFacts"]["evidenceSummary"]["refs"][0]["lawId"],
            "325AC0000000201",
        )

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
