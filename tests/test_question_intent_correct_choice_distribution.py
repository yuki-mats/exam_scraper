from __future__ import annotations

import unittest
from pathlib import Path

from scripts.check.check_question_intent_correct_choice_text_distribution import (
    validate_question_intent_correct_choice_distribution,
)


class QuestionIntentCorrectChoiceDistributionTests(unittest.TestCase):
    @staticmethod
    def _payload(question_type: str) -> dict:
        return {
            "question_bodies": [
                {
                    "original_question_id": "q1",
                    "questionType": question_type,
                    "questionBodyText": "誤っているものはどれか。",
                    "questionIntent": "select_incorrect",
                    "choiceTextList": ["記述1", "記述2", "記述3"],
                    "correctChoiceText": ["間違い", "正しい", "間違い"],
                    "answer_result_text": "正解は 1 です。",
                }
            ]
        }

    def test_true_false_does_not_reapply_original_single_answer_count(self) -> None:
        violations = validate_question_intent_correct_choice_distribution(
            payload=self._payload("true_false"),
            source_path=Path("merged.json"),
        )

        self.assertEqual(violations, [])

    def test_question_level_format_still_checks_official_answer(self) -> None:
        violations = validate_question_intent_correct_choice_distribution(
            payload=self._payload("flash_card"),
            source_path=Path("merged.json"),
        )

        self.assertEqual(len(violations), 1)
        self.assertIn("official_answer_mismatch", violations[0].reason)


if __name__ == "__main__":
    unittest.main()
