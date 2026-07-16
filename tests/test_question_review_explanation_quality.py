import unittest

from tools.question_review_console.explanation_quality import (
    explanation_style_issues,
)


class ExplanationQualityTests(unittest.TestCase):
    def test_rejects_law_name_as_mechanical_sentence_subject(self):
        issues = explanation_style_issues(
            [
                "正しい。ガス事業法第2条第1項は、小売供給を定義している。",
            ]
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("法令名・条文", issues[0])

    def test_rejects_point_is_wrong_closing(self):
        issues = explanation_style_issues(
            [
                "間違い。届出が必要である。選択肢は許可としている点が誤り。",
            ]
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("点が誤り", issues[0])

    def test_accepts_correct_content_then_law_location_and_difference(self):
        issues = explanation_style_issues(
            [
                "間違い。ガス事業は、ガス事業法第2条第11項において"
                "所定の四事業と定義されている。選択肢には「小売供給」と"
                "記載されているため、誤りである。",
            ]
        )

        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
