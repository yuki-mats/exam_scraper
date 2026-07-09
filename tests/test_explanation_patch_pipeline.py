from __future__ import annotations

import unittest

from scripts.check.check_explanation_patch_coverage import compare_entries
from scripts.convert.convert_merged_to_firestore import convert_true_false_to_firestore
from scripts.fix.auto_assign_correct_choice_text import build_expected_correct_choice_text
from scripts.fix.materialize_minimal_patch import materialize_explanation
from scripts.pipeline.build_tsukanshi_upload_artifacts import build_explanation_patch, build_intent_patch


def valid_law_revision_facts(status: str = "same_as_current") -> dict:
    return {
        "auditStatus": status,
        "reviewState": "secondary_verified" if status != "updated_to_current_law" else "tertiary_verified",
        "current": {
            "correctChoiceText": "正しい",
            "lawId": "325AC0000000201",
            "lawTitle": "建築基準法",
            "article": "6",
            "referenceDate": "2026-07-05",
            "verificationStatus": "verified",
        },
        "examTime": {
            "correctChoiceText": "正しい",
            "verificationStatus": "from_original_answer",
        },
        "evidenceSummary": {
            "verdict": "correct",
            "differenceSummary": "正誤に影響する差分はありません。",
            "refs": [
                {
                    "refId": "current_basis_Art6",
                    "lawTimeScope": "current",
                    "relation": "basis",
                    "primaryBasis": True,
                    "lawId": "325AC0000000201",
                    "lawTitle": "建築基準法",
                    "article": "6",
                }
            ],
        },
    }


class ExplanationPatchPipelineTests(unittest.TestCase):
    def test_build_expected_correct_choice_text_prefers_answer_result_text_over_bad_inferred_numbers(self) -> None:
        question = {
            "questionType": "flash_card",
            "questionIntent": "select_correct",
            "answer_result_text": "正解は 11 です。",
            "answer_result_inferred_correct_choice_numbers": [1],
            "choiceTextList": [f"肢{i}" for i in range(1, 16)],
        }

        actual, reason = build_expected_correct_choice_text(question)

        self.assertIsNone(reason)
        self.assertEqual(actual[10], "正しい")
        self.assertEqual(actual.count("正しい"), 1)
        self.assertEqual(actual[0], "間違い")

    def test_tsukanshi_patch_builder_repairs_correct_choice_text_and_explanation_labels(self) -> None:
        question = {
            "public_question_id": "q123",
            "question_url": "https://example.com/q123",
            "questionType": "flash_card",
            "questionIntent": "select_correct",
            "questionBodyText": "正しい語句を選びなさい。",
            "choiceTextList": [f"肢{i}" for i in range(1, 16)],
            "correctChoiceText": ["正しい"] + ["間違い"] * 14,
            "answer_result_text": "正解は 11 です。",
            "answer_result_inferred_correct_choice_numbers": [1],
            "explanation_choice_snippets": [[] for _ in range(15)],
            "explanation_common_prefix": ["条文で 11 番が正答だと分かる。"],
            "explanation_common_summary": [],
        }

        intent_patch = build_intent_patch([question])[0]
        explanation_patch = build_explanation_patch([question])[0]

        self.assertTrue(intent_patch["correctChoiceText_changed"])
        self.assertEqual(intent_patch["correctChoiceText"][10], "正しい")
        self.assertEqual(intent_patch["correctChoiceText"][0], "間違い")
        self.assertIn("answer_result_text=正解は 11 です。", intent_patch["correctChoiceText_change_detail"])
        self.assertIn("選択肢1は「間違い」です。", explanation_patch["explanationText"][0])
        self.assertIn("選択肢11は「正しい」です。", explanation_patch["explanationText"][10])

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
                        "lawId": "329AC0000000051",
                        "lawTitle": "ガス事業法",
                        "article": "2条",
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
                            "lawId": "329AC0000000051",
                            "lawTitle": "ガス事業法",
                            "article": "2条",
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

    def test_compare_entries_accepts_valid_law_revision_facts(self) -> None:
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
                "suggestedQuestions": ["現行法ではどう考える？"],
                "suggestedQuestionDetails": [
                    {
                        "question": "現行法ではどう考える？",
                        "answer": "監査済みの現行法根拠では正しいです。",
                    }
                ],
                "lawRevisionFacts": {
                    "auditStatus": "same_as_current",
                    "reviewState": "secondary_verified",
                    "current": {
                        "correctChoiceText": "正しい",
                        "lawId": "325AC0000000201",
                        "lawTitle": "建築基準法",
                        "article": "2",
                        "referenceDate": "2026-07-05",
                        "verificationStatus": "verified",
                    },
                    "evidenceSummary": {
                        "verdict": "correct",
                        "displayRefIds": ["current_basis_Art2"],
                        "refs": [
                            {
                                "refId": "current_basis_Art2",
                                "lawTimeScope": "current",
                                "relation": "basis",
                                "primaryBasis": True,
                                "lawId": "325AC0000000201",
                                "lawTitle": "建築基準法",
                                "elm": "MainProvision-Article_2",
                                "articleTextHash": "article-hash",
                            }
                        ],
                    },
                },
            }
        ]

        errors, warnings = compare_entries(source_questions, patch_entries)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_compare_entries_rejects_invalid_law_revision_facts(self) -> None:
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
                "suggestedQuestions": ["現行法ではどう考える？"],
                "suggestedQuestionDetails": [
                    {
                        "question": "現行法ではどう考える？",
                        "answer": "監査済みの現行法根拠では正しいです。",
                    }
                ],
                "lawRevisionFacts": {"auditStatus": "maybe"},
            }
        ]

        errors, _ = compare_entries(source_questions, patch_entries)

        self.assertTrue(
            any("lawRevisionFacts must be a valid object" in error for error in errors)
        )

    def test_compare_entries_requires_law_revision_facts_for_law_related_questions(self) -> None:
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
                "suggestedQuestions": ["現行法ではどう考える？"],
                "suggestedQuestionDetails": [
                    {
                        "question": "現行法ではどう考える？",
                        "answer": "監査済みの現行法根拠では正しいです。",
                    }
                ],
                "isLawRelated": True,
            }
        ]

        errors, _ = compare_entries(
            source_questions,
            patch_entries,
            require_law_revision_facts=True,
        )

        self.assertTrue(
            any("missing lawRevisionFacts for law-related question" in error for error in errors)
        )

    def test_compare_entries_accepts_public_question_id_when_original_id_missing(self) -> None:
        source_questions = [
            {
                "public_question_id": "q123",
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
                    {"question": "なぜそうなる？", "answer": "公開IDを正本として照合できる。"},
                ],
            }
        ]

        errors, warnings = compare_entries(source_questions, patch_entries)

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_compare_entries_rejects_verified_law_reference_without_law_id(self) -> None:
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
                    {"question": "なぜそうなる？", "answer": "定義条文を確認すると判断できる。"},
                ],
                "lawReferences": [
                    [
                        {
                            "role": "current_basis",
                            "scope": "choice",
                            "choiceIndex": 0,
                            "lawTitle": "ガス事業法",
                            "article": "2条",
                            "referenceDate": "current",
                            "verificationStatus": "verified",
                        }
                    ],
                ],
            }
        ]

        errors, _ = compare_entries(source_questions, patch_entries)

        self.assertTrue(
            any(".lawId is required for verified lawReferences" in error for error in errors)
        )

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

    def test_compare_entries_accepts_law_evidence_utilization_when_public_fields_use_facts(self) -> None:
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
                "explanationText": [
                    "正しい。\n\n建築基準法第6条の確認申請の要件に沿って判断する。"
                ],
                "suggestedQuestions": ["建築基準法第6条では何を確認しますか？"],
                "suggestedQuestionDetails": [
                    {
                        "question": "建築基準法第6条では何を確認しますか？",
                        "answer": "建築基準法第6条は確認申請の対象を判断する根拠になる。問題では対象建築物に当たるかを条文の要件で見る。",
                    }
                ],
                "isLawRelated": True,
                "lawGroundedExplanationNotNeeded": False,
                "lawRevisionFacts": valid_law_revision_facts(),
            }
        ]

        errors, warnings = compare_entries(
            source_questions,
            patch_entries,
            require_law_evidence_utilization=True,
        )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_compare_entries_rejects_generic_law_related_suggestions_when_utilization_required(self) -> None:
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
                "explanationText": ["正しい。\n\n条件に合うため正しい。"],
                "suggestedQuestions": ["正誤を判断するポイントはどこですか？"],
                "suggestedQuestionDetails": [
                    {
                        "question": "正誤を判断するポイントはどこですか？",
                        "answer": "問題文の条件、数値、対象、例外の有無を確認します。",
                    }
                ],
                "isLawRelated": True,
                "lawGroundedExplanationNotNeeded": False,
                "lawRevisionFacts": valid_law_revision_facts(),
            }
        ]

        errors, _ = compare_entries(
            source_questions,
            patch_entries,
            require_law_evidence_utilization=True,
        )

        self.assertTrue(
            any("suggestedQuestions must include" in error for error in errors)
        )
        self.assertTrue(
            any("do not mention any concrete law evidence anchor" in error for error in errors)
        )

    def test_compare_entries_requires_current_and_exam_time_words_for_current_law_update(self) -> None:
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
                "explanationText": ["間違い。\n\n建築基準法第6条の要件とは異なる。"],
                "suggestedQuestions": ["建築基準法第6条では何を確認しますか？"],
                "suggestedQuestionDetails": [
                    {
                        "question": "建築基準法第6条では何を確認しますか？",
                        "answer": "建築基準法第6条の要件に当たるかを確認する。",
                    }
                ],
                "isLawRelated": True,
                "lawGroundedExplanationNotNeeded": False,
                "lawRevisionFacts": valid_law_revision_facts("updated_to_current_law"),
            }
        ]

        errors, _ = compare_entries(
            source_questions,
            patch_entries,
            require_law_evidence_utilization=True,
        )

        self.assertTrue(
            any("must distinguish current law from exam-time handling" in error for error in errors)
        )
        self.assertTrue(
            any("must ask about current law and exam-time difference" in error for error in errors)
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
                        "lawId": "329AC0000000051",
                        "lawTitle": "ガス事業法",
                        "article": "2条",
                        "referenceDate": "current",
                        "verificationStatus": "verified",
                    }
                ],
                [
                    {
                        "role": "current_basis",
                        "scope": "choice",
                        "choiceIndex": 1,
                        "lawId": "345M50000400097",
                        "lawTitle": "ガス事業法施行規則",
                        "article": "3条の2",
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
