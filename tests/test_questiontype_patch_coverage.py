import unittest

from scripts.check.check_questiontype_patch_coverage import compare_entries


class QuestionTypePatchCoverageTest(unittest.TestCase):
    def source(self, question_type="group_choice"):
        return {
            "original_question_id": "q1",
            "questionBodyText": "計算結果として最も近い値はどれか。",
            "choiceTextList": ["1", "2"],
            "questionType": question_type,
            "question_url": "https://example.com/q1",
            "sourceQuestionKey": "sample:2026:q1",
            "reviewQuestionId": "q1",
        }

    def patch(self, question_type):
        return {
            "original_question_id": "q1",
            "questionBodyText": "計算結果として最も近い値はどれか。",
            "choiceTextList": ["1", "2"],
            "questionType": question_type,
            "question_url": "https://example.com/q1",
            "isCalculationQuestion": True,
        }

    def test_rejects_new_single_choice_classification(self):
        issues, _warnings = compare_entries(
            [self.source()],
            [self.patch("single_choice")],
        )

        self.assertTrue(any("questionType must be one of" in issue for issue in issues))

    def test_allows_legacy_single_choice_to_remain_unchanged(self):
        issues, warnings = compare_entries(
            [self.source("single_choice")],
            [self.patch("single_choice")],
        )

        self.assertEqual(issues, [])
        self.assertTrue(any("legacy questionType preserved" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
