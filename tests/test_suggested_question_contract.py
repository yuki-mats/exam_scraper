from __future__ import annotations

import unittest

from scripts.common.suggested_question_contract import public_choice_indexes


class SuggestedQuestionContractTests(unittest.TestCase):
    def test_question_level_type_does_not_guess_first_choice(self) -> None:
        for question_type in ("flash_card", "group_choice"):
            with self.subTest(question_type=question_type):
                self.assertEqual(
                    public_choice_indexes(
                        question_type,
                        ["間違い", "間違い"],
                        2,
                        "select_correct",
                    ),
                    set(),
                )

    def test_question_level_type_does_not_guess_when_multiple_choices_are_correct(
        self,
    ) -> None:
        for question_type in ("flash_card", "group_choice"):
            with self.subTest(question_type=question_type):
                self.assertEqual(
                    public_choice_indexes(
                        question_type,
                        ["正しい", "間違い", "正解"],
                        3,
                        "select_correct",
                    ),
                    set(),
                )

    def test_question_level_type_uses_the_only_correct_choice(self) -> None:
        self.assertEqual(
            public_choice_indexes(
                "group_choice",
                ["間違い", "正しい", "間違い"],
                3,
                "select_correct",
            ),
            {1},
        )

    def test_question_level_type_uses_selected_incorrect_choice(self) -> None:
        self.assertEqual(
            public_choice_indexes(
                "group_choice",
                ["正しい", "間違い", "正しい"],
                3,
                "select_incorrect",
            ),
            {1},
        )

    def test_question_level_type_does_not_guess_without_intent(self) -> None:
        self.assertEqual(
            public_choice_indexes(
                "group_choice",
                ["間違い", "正しい", "間違い"],
                3,
                None,
            ),
            set(),
        )


if __name__ == "__main__":
    unittest.main()
