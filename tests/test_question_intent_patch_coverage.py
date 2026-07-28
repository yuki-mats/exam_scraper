import unittest

from tools.question_bank.checks.check_question_intent_patch_coverage import (
    compare_entries,
)


class QuestionIntentPatchCoverageTests(unittest.TestCase):
    def test_current_minimal_patch_does_not_require_legacy_change_metadata(self):
        source = [
            {
                "original_question_id": "q1",
                "question_url": "https://example.com/q1",
            }
        ]
        patch = [
            {
                "original_question_id": "q1",
                "questionIntent": "select_incorrect",
            }
        ]

        self.assertEqual(compare_entries(source, patch), [])


if __name__ == "__main__":
    unittest.main()
