from __future__ import annotations

import unittest

from scripts.common.explanation_contract import (
    expected_explanation_count,
    explanation_shape_errors,
    public_explanation_text,
)


class ExplanationContractTests(unittest.TestCase):
    def test_flash_card_uses_one_question_level_explanation(self) -> None:
        self.assertEqual(expected_explanation_count("flash_card", 5), 1)
        self.assertEqual(
            explanation_shape_errors(
                ["式から4.0Qと求まる。"],
                question_type="flash_card",
                choice_count=5,
            ),
            [],
        )

    def test_group_choice_uses_one_question_level_explanation(self) -> None:
        self.assertEqual(expected_explanation_count("group_choice", 5), 1)
        self.assertEqual(
            explanation_shape_errors(
                ["比較基準を順に当てはめると、組合せ3が正答となる。"],
                question_type="group_choice",
                choice_count=5,
            ),
            [],
        )

    def test_non_flash_card_remains_choice_aligned(self) -> None:
        self.assertEqual(expected_explanation_count("true_false", 2), 2)
        self.assertTrue(
            explanation_shape_errors(
                ["正しい。"],
                question_type="true_false",
                choice_count=2,
            )
        )

    def test_public_flash_card_reuses_root_explanation_and_omits_choice_only(self) -> None:
        explanations = ["問題全体の基本解説"]
        self.assertEqual(
            public_explanation_text(
                explanations,
                question_type="flash_card",
                choice_index=3,
                is_choice_only=False,
            ),
            "問題全体の基本解説",
        )
        self.assertIsNone(
            public_explanation_text(
                explanations,
                question_type="flash_card",
                choice_index=0,
                is_choice_only=True,
            )
        )

    def test_public_group_choice_reuses_root_explanation_and_omits_choice_only(self) -> None:
        explanations = ["問題全体の比較・組合せ基準"]
        self.assertEqual(
            public_explanation_text(
                explanations,
                question_type="group_choice",
                choice_index=1,
                is_choice_only=False,
            ),
            "問題全体の比較・組合せ基準",
        )
        self.assertIsNone(
            public_explanation_text(
                explanations,
                question_type="group_choice",
                choice_index=0,
                is_choice_only=True,
            )
        )

    def test_legacy_flash_card_reads_correct_choice_index_during_migration(self) -> None:
        self.assertEqual(
            public_explanation_text(
                ["肢1", "肢2", "肢3"],
                question_type="flash_card",
                choice_index=2,
                is_choice_only=False,
            ),
            "肢3",
        )


if __name__ == "__main__":
    unittest.main()
