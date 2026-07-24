from __future__ import annotations

import unittest

from scripts.common.question_answer_contract import (
    asks_for_selected_choice_count,
    official_answer_alignment_issue,
    question_level_answer_cardinality_issue,
)


class QuestionAnswerContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
