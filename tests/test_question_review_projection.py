import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.projection import (
    PatchEntry,
    explanation_prefix_matches,
    project_record,
)


class QuestionReviewProjectionTests(unittest.TestCase):
    def test_later_correct_choice_patch_overrides_intent_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intent = PatchEntry(
                root / "15.json",
                {"original_question_id": "q1", "correctChoiceText": ["正しい", "間違い"]},
            )
            strict = PatchEntry(
                root / "23.json",
                {"original_question_id": "q1", "correctChoiceText": ["間違い", "正しい"]},
            )
            explanation = PatchEntry(
                root / "21.json",
                {
                    "original_question_id": "q1",
                    "explanationText": ["間違い。根拠1", "正しい。根拠2"],
                },
            )
            result = project_record(
                {
                    "original_question_id": "q1",
                    "questionBodyText": "問題",
                    "choiceTextList": ["A", "B"],
                },
                {"q1"},
                {
                    "questionIntent": {"q1": intent},
                    "explanation": {"q1": explanation},
                    "correctChoice": {"q1": strict},
                },
                [],
            )

        self.assertEqual(result.record["correctChoiceText"], ["間違い", "正しい"])
        self.assertEqual(result.record["explanationText"][1], "正しい。根拠2")
        self.assertEqual(result.applied_files, (str(intent.path), str(explanation.path), str(strict.path)))

    def test_explanation_prefix_matches_normalized_verdict(self):
        self.assertTrue(explanation_prefix_matches("○", "正しい。条文の通り。"))
        self.assertTrue(explanation_prefix_matches("誤り", "間違い。文言が異なる。"))
        self.assertTrue(
            explanation_prefix_matches("正しい", "選択肢1は「正しい」です。根拠を説明する。")
        )
        self.assertTrue(
            explanation_prefix_matches("間違い", "この記述は誤りです。根拠を説明する。")
        )
        self.assertFalse(explanation_prefix_matches("正しい", "間違い。文言が異なる。"))


if __name__ == "__main__":
    unittest.main()
