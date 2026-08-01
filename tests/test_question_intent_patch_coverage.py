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

    def test_rejects_intent_that_reverses_explicit_statement_request(self):
        source = [
            {
                "original_question_id": "q1",
                "questionBodyText": (
                    "次の記述のうち、誤っているものはいくつあるか。"
                ),
            }
        ]
        patch = [
            {
                "original_question_id": "q1",
                "questionIntent": "select_correct",
            }
        ]

        errors = compare_entries(source, patch)

        self.assertTrue(
            any(
                "expected 'select_incorrect'" in error
                for error in errors
            ),
            errors,
        )

    def test_does_not_reverse_negative_predicate_for_fragment_choices(self):
        source = [
            {
                "original_question_id": "q1",
                "questionBodyText": (
                    "次の設備のうち、この規定に該当しないものはどれか。"
                ),
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
