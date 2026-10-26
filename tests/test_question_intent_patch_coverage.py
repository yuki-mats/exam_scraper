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
                "choiceTextList": [
                    "条件Aを満たす。",
                    "条件Bを満たさない。",
                ],
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
                "choiceTextList": ["設備A", "設備B"],
            }
        ]
        patch = [
            {
                "original_question_id": "q1",
                "questionIntent": "select_incorrect",
            }
        ]

        self.assertEqual(compare_entries(source, patch), [])

    def test_accepts_condition_membership_for_action_statement_choices(self):
        source = [{
            "original_question_id": "q1",
            "questionBodyText": (
                "施設の地震対策に関する次の記述のうち、"
                "いずれも該当しないものの組合せはどれか。"
            ),
            "choiceTextList": [
                "容器に転倒防止措置を講ずる。",
                "配管の継手をネジ接合とする。",
            ],
        }]
        patch = [{"original_question_id": "q1", "questionIntent": "select_correct"}]

        self.assertEqual(compare_entries(source, patch), [])

    def test_condition_topic_does_not_disable_explicit_truth_direction_check(self):
        for request, reversed_intent, expected in (
            ("誤っているもの", "select_correct", "select_incorrect"),
            ("正しいもの", "select_incorrect", "select_correct"),
        ):
            with self.subTest(request=request):
                source = [{
                    "original_question_id": "q1",
                    "questionBodyText": (
                        "この規定に該当しないものの取扱いに関する"
                        f"次の記述のうち、{request}はどれか。"
                    ),
                    "choiceTextList": ["措置Aを講ずる。", "措置Bを講じない。"],
                }]
                patch = [{"original_question_id": "q1", "questionIntent": reversed_intent}]

                errors = compare_entries(source, patch)

                self.assertTrue(any(f"expected '{expected}'" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
