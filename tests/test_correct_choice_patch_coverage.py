import unittest

from scripts.check.check_correct_choice_patch_coverage import compare_entries


class CorrectChoicePatchCoverageTests(unittest.TestCase):
    def setUp(self):
        self.source = [
            {
                "original_question_id": "q1",
                "question_url": "https://example.com/q1",
                "choiceTextList": ["選択肢"],
                "correctChoiceText": ["正しい"],
            }
        ]

    def compare(self, patch):
        return compare_entries(
            self.source,
            [patch],
            require_full=True,
            require_snippets=False,
            require_change_meta=False,
        )

    def test_question_url_can_be_omitted(self):
        errors, warnings = self.compare(
            {
                "original_question_id": "q1",
                "correctChoiceText": ["正しい"],
            }
        )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_question_url_must_match_source_when_present(self):
        errors, _ = self.compare(
            {
                "original_question_id": "q1",
                "question_url": "https://example.com/other",
                "correctChoiceText": ["正しい"],
            }
        )

        self.assertTrue(any("question_url mismatch" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
