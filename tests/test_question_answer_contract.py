from __future__ import annotations

import unittest

from scripts.common.question_answer_contract import (
    all_correct_choice_sentinel_number,
    asks_for_selected_choice_count,
    explicit_statement_question_intent,
    official_answer_alignment_issue,
    question_level_answer_cardinality_issue,
    uses_trusted_gassyunin_judge_answers,
)


class QuestionAnswerContractTests(unittest.TestCase):
    def test_explicit_statement_intent_reads_inappropriate_as_incorrect(
        self,
    ) -> None:
        self.assertEqual(
            explicit_statement_question_intent(
                "次の記述のうち、不適切なものはどれか。"
            ),
            "select_incorrect",
        )

    def test_explicit_statement_intent_reads_appropriate_as_correct(
        self,
    ) -> None:
        self.assertEqual(
            explicit_statement_question_intent(
                "次の記述のうち、適切なものはどれか。"
            ),
            "select_correct",
        )

    def test_explicit_statement_intent_ignores_fragment_predicate(
        self,
    ) -> None:
        self.assertIsNone(
            explicit_statement_question_intent(
                "次の設備のうち、この規定に該当しないものはどれか。"
            )
        )

    def test_question_level_cardinality_follows_select_incorrect(self) -> None:
        self.assertIsNone(
            question_level_answer_cardinality_issue(
                "group_choice",
                ["正しい", "間違い", "正しい"],
                "select_incorrect",
            )
        )

    def test_official_answer_mismatch_reports_without_choosing_a_field(self) -> None:
        issue = official_answer_alignment_issue(
            {
                "questionIntent": "select_correct",
                "correctChoiceText": ["正しい", "間違い"],
                "answer_result_text": "正解は 2 です。",
            }
        )

        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("公式=[2]", issue)
        self.assertIn("判定=[1]", issue)
        self.assertIn("どのfieldを変更するか決めません", issue)

    def test_official_answer_alignment_accepts_select_incorrect(self) -> None:
        self.assertIsNone(
            official_answer_alignment_issue(
                {
                    "questionIntent": "select_incorrect",
                    "correctChoiceText": ["正しい", "間違い"],
                    "answer_result_text": "正解は 2 です。",
                }
            )
        )

    def test_official_answer_uses_exam_time_verdicts_after_current_law_update(
        self,
    ) -> None:
        self.assertIsNone(
            official_answer_alignment_issue(
                {
                    "questionBodyText": "誤っているものはどれか。",
                    "questionIntent": "select_incorrect",
                    "correctChoiceText": [
                        "間違い",
                        "正しい",
                        "正しい",
                        "間違い",
                        "正しい",
                    ],
                    "answer_result_text": "正解は 4 です。",
                    "lawRevisionFacts": [
                        {
                            "auditStatus": "updated_to_current_law",
                            "examTime": {"correctChoiceText": "正しい"},
                            "current": {"correctChoiceText": "間違い"},
                        },
                        {
                            "auditStatus": "not_law_related",
                            "examTime": {"correctChoiceText": "正しい"},
                            "current": {"correctChoiceText": "正しい"},
                        },
                        {
                            "auditStatus": "not_law_related",
                            "examTime": {"correctChoiceText": "正しい"},
                            "current": {"correctChoiceText": "正しい"},
                        },
                        {
                            "auditStatus": "not_law_related",
                            "examTime": {"correctChoiceText": "間違い"},
                            "current": {"correctChoiceText": "間違い"},
                        },
                        {
                            "auditStatus": "not_law_related",
                            "examTime": {"correctChoiceText": "正しい"},
                            "current": {"correctChoiceText": "正しい"},
                        },
                    ],
                }
            )
        )

    def test_incomplete_exam_time_verdicts_do_not_hide_official_mismatch(
        self,
    ) -> None:
        issue = official_answer_alignment_issue(
            {
                "questionBodyText": "誤っているものはどれか。",
                "questionIntent": "select_incorrect",
                "correctChoiceText": ["間違い", "正しい"],
                "answer_result_text": "正解は 2 です。",
                "lawRevisionFacts": [
                    {
                        "auditStatus": "updated_to_current_law",
                        "examTime": {"correctChoiceText": "正しい"},
                    },
                    {
                        "auditStatus": "same_as_current",
                        "examTime": {},
                    },
                ],
            }
        )

        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("公式=[2]", issue)
        self.assertIn("判定=[1]", issue)

    def test_trusted_judge_accepts_bound_gassyunin_firestore_snapshot(
        self,
    ) -> None:
        self.assertTrue(
            uses_trusted_gassyunin_judge_answers(
                {
                    "sourceProvider": "gassyunin.com+firestore_snapshot",
                    "sourceOrigin": "firestore_snapshot",
                    "question_url": (
                        "https://gassyunin.com/exam/otsu/otsu_2022/#shohi-q27"
                    ),
                    "choiceMarkerSource": "judge",
                    "markerAlignmentMode": "judge_only",
                    "markerMismatchDetected": False,
                    "answerResultNumbersRemapped": False,
                    "judgeChoiceMarkers": ["1", "2"],
                    "choiceTextList": ["記述1", "記述2"],
                    "correctChoiceText": ["間違い", "正しい"],
                    "sourceStatementCount": 2,
                }
            )
        )

    def test_trusted_judge_rejects_snapshot_without_gassyunin_url(
        self,
    ) -> None:
        self.assertFalse(
            uses_trusted_gassyunin_judge_answers(
                {
                    "sourceProvider": "gassyunin.com+firestore_snapshot",
                    "sourceOrigin": "firestore_snapshot",
                    "question_url": "https://example.com/copied",
                    "choiceMarkerSource": "judge",
                    "markerAlignmentMode": "judge_only",
                    "markerMismatchDetected": False,
                    "answerResultNumbersRemapped": False,
                    "judgeChoiceMarkers": ["1", "2"],
                    "choiceTextList": ["記述1", "記述2"],
                    "correctChoiceText": ["間違い", "正しい"],
                    "sourceStatementCount": 2,
                }
            )
        )

    def test_official_answer_alignment_compares_count_for_ikutsu_question(self) -> None:
        self.assertIsNone(
            official_answer_alignment_issue(
                {
                    "questionBodyText": (
                        "次の記述のうち、正しいものはいくつあるか。"
                    ),
                    "questionIntent": "select_correct",
                    "correctChoiceText": [
                        "正しい",
                        "正しい",
                        "間違い",
                        "正しい",
                    ],
                    "answer_result_text": "正解は 3 です。",
                }
            )
        )

    def test_official_answer_alignment_compares_count_for_nanko_question(self) -> None:
        self.assertIsNone(
            official_answer_alignment_issue(
                {
                    "questionBodyText": (
                        "次の項目のうち、誤っている記述は何個あるか。"
                    ),
                    "questionIntent": "select_incorrect",
                    "correctChoiceText": [
                        "間違い",
                        "正しい",
                        "間違い",
                        "正しい",
                    ],
                    "answer_result_text": "正解は 2 です。",
                }
            )
        )

    def test_all_correct_sentinel_maps_option_number_to_zero_incorrect(self) -> None:
        record = {
            "questionBodyText": (
                "次の記述のうち、誤っているものはいくつあるか"
                "（選択肢（５）はすべて正しい）。"
            ),
            "questionIntent": "select_incorrect",
            "correctChoiceText": ["正しい"] * 5,
            "answer_result_text": "正解は 5 です。",
        }

        self.assertEqual(
            all_correct_choice_sentinel_number(record["questionBodyText"]),
            5,
        )
        self.assertIsNone(official_answer_alignment_issue(record))

    def test_all_correct_sentinel_still_rejects_an_incorrect_statement(self) -> None:
        issue = official_answer_alignment_issue(
            {
                "questionBodyText": (
                    "次の記述のうち、誤っているものはいくつあるか"
                    "（選択肢(5)は全て正しい）。"
                ),
                "questionIntent": "select_incorrect",
                "correctChoiceText": [
                    "正しい",
                    "間違い",
                    "正しい",
                    "正しい",
                    "正しい",
                ],
                "answer_result_text": "正解は 5 です。",
            }
        )

        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("期待する該当肢数=0", issue)
        self.assertIn("判定した該当肢数=1", issue)
        self.assertIn("どのfieldを変更するか決めません", issue)

    def test_official_answer_alignment_compares_count_for_tsugi_no_uchi(self) -> None:
        self.assertIsNone(
            official_answer_alignment_issue(
                {
                    "questionBodyText": (
                        "基準を満たす設備は、次のうちいくつあるか。"
                    ),
                    "questionIntent": "select_correct",
                    "correctChoiceText": [
                        "正しい",
                        "正しい",
                        "間違い",
                        "正しい",
                    ],
                    "answer_result_text": "正解は 3 です。",
                }
            )
        )

    def test_official_answer_count_mismatch_reports_without_choosing_field(self) -> None:
        issue = official_answer_alignment_issue(
            {
                "questionBodyText": "正しい記述の数はいくつか。",
                "questionIntent": "select_correct",
                "correctChoiceText": ["正しい", "正しい", "間違い"],
                "answer_result_text": "正解は 1 です。",
            }
        )

        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("公式の正答数=1", issue)
        self.assertIn("判定した該当肢数=2", issue)
        self.assertIn("どのfieldを変更するか決めません", issue)

    def test_quantity_in_question_body_does_not_imply_choice_count_answer(self) -> None:
        self.assertIsNone(
            official_answer_alignment_issue(
                {
                    "questionBodyText": (
                        "容器はいくつ必要か。最も適切な数値を選べ。"
                    ),
                    "questionIntent": "select_correct",
                    "correctChoiceText": ["間違い", "間違い", "正しい"],
                    "answer_result_text": "正解は 3 です。",
                }
            )
        )

    def test_count_question_with_multiple_official_numbers_is_ambiguous(self) -> None:
        issue = official_answer_alignment_issue(
            {
                "questionBodyText": "正しいものはいくつあるか。",
                "questionIntent": "select_correct",
                "correctChoiceText": ["正しい", "間違い", "正しい"],
                "answer_result_text": "正解は 1, 3 です。",
            }
        )

        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("単一の数として解釈できません", issue)
        self.assertIn("どのfieldを変更するか決めません", issue)

    def test_selected_choice_count_recognizes_number_is_which_wording(self) -> None:
        self.assertTrue(
            asks_for_selected_choice_count(
                "次のうち、不適当なものの数はどれか。"
            )
        )

    def test_combination_answer_requires_verified_mapping(self) -> None:
        issue = official_answer_alignment_issue(
            {
                "questionBodyText": (
                    "次の記述のうち、誤っているものの組合せはどれか。"
                ),
                "questionIntent": "select_incorrect",
                "correctChoiceText": [
                    "正しい",
                    "正しい",
                    "間違い",
                    "間違い",
                ],
                "answer_result_text": "正解は 5 です。",
            }
        )

        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("検証済みmappingがありません", issue)
        self.assertIn("どのfieldを変更するか決めません", issue)

    def test_combination_answer_accepts_direct_choice_alignment(self) -> None:
        self.assertIsNone(
            official_answer_alignment_issue(
                {
                    "questionBodyText": (
                        "ビタミンと欠乏症の組合せで誤っているのはどれか。"
                    ),
                    "questionIntent": "select_incorrect",
                    "correctChoiceText": [
                        "正しい",
                        "正しい",
                        "正しい",
                        "間違い",
                        "正しい",
                    ],
                    "answer_result_text": "正解は 4 です。",
                }
            )
        )

    def test_combination_answer_accepts_trusted_gassyunin_judge_answers(self) -> None:
        self.assertIsNone(
            official_answer_alignment_issue(
                {
                    "questionBodyText": (
                        "次の記述のうち、誤っているものの組合せはどれか。"
                    ),
                    "questionIntent": "select_incorrect",
                    "choiceTextList": ["記述イ", "記述ロ", "記述ハ", "記述ニ"],
                    "correctChoiceText": [
                        "正しい",
                        "正しい",
                        "間違い",
                        "間違い",
                    ],
                    "answer_result_text": "正解は 5 です。",
                    "sourceProvider": "gassyunin.com",
                    "sourceOrigin": "gassyunin_site",
                    "choiceMarkerSource": "judge",
                    "markerAlignmentMode": "judge_only",
                    "markerMismatchDetected": False,
                    "answerResultNumbersRemapped": False,
                    "judgeChoiceMarkers": ["イ", "ロ", "ハ", "ニ"],
                    "sourceStatementCount": 4,
                }
            )
        )

    def test_gassyunin_marker_mismatch_still_requires_mapping(self) -> None:
        issue = official_answer_alignment_issue(
            {
                "questionBodyText": (
                    "次の記述のうち、誤っているものの組合せはどれか。"
                ),
                "questionIntent": "select_incorrect",
                "choiceTextList": ["記述イ", "記述ロ", "記述ハ", "記述ニ"],
                "correctChoiceText": [
                    "正しい",
                    "正しい",
                    "間違い",
                    "間違い",
                ],
                "answer_result_text": "正解は 5 です。",
                "sourceProvider": "gassyunin.com",
                "sourceOrigin": "gassyunin_site",
                "choiceMarkerSource": "judge",
                "markerAlignmentMode": "judge_only",
                "markerMismatchDetected": True,
                "answerResultNumbersRemapped": False,
                "judgeChoiceMarkers": ["イ", "ロ", "ハ", "ニ"],
                "sourceStatementCount": 4,
            }
        )

        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("検証済みmappingがありません", issue)

    def test_gassyunin_cardinality_mismatch_still_requires_mapping(self) -> None:
        issue = official_answer_alignment_issue(
            {
                "questionBodyText": (
                    "次の記述のうち、誤っているものの組合せはどれか。"
                ),
                "questionIntent": "select_incorrect",
                "choiceTextList": ["記述イ", "記述ロ", "記述ハ", "記述ニ"],
                "correctChoiceText": [
                    "正しい",
                    "正しい",
                    "間違い",
                    "間違い",
                ],
                "answer_result_text": "正解は 5 です。",
                "sourceProvider": "gassyunin.com",
                "sourceOrigin": "gassyunin_site",
                "choiceMarkerSource": "judge",
                "markerAlignmentMode": "judge_only",
                "markerMismatchDetected": False,
                "answerResultNumbersRemapped": False,
                "judgeChoiceMarkers": ["イ", "ロ", "ハ"],
                "sourceStatementCount": 4,
            }
        )

        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn("検証済みmappingがありません", issue)


if __name__ == "__main__":
    unittest.main()
