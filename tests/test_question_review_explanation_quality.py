import unittest
from pathlib import Path

from tools.question_review_console.explanation_quality import (
    explanation_style_issues,
)


class ExplanationQualityTests(unittest.TestCase):
    def test_explanation_count_must_match_choices(self):
        issues = explanation_style_issues(
            ["正しい。定義に一致する。"],
            ["正しい", "間違い"],
            choice_texts=["選択肢1", "選択肢2"],
        )

        self.assertIn("解説の件数が選択肢の件数と一致しません。", issues[0])

    def test_group_choice_accepts_one_question_level_explanation_without_verdict_prefix(self):
        issues = explanation_style_issues(
            ["各組合せを比較すると、条件をすべて満たすのは選択肢3である。"],
            ["間違い", "間違い", "正しい", "間違い"],
            choice_texts=["組合せ1", "組合せ2", "組合せ3", "組合せ4"],
            require_verdict_prefix=False,
            question_type="group_choice",
        )

        self.assertEqual(issues, [])

    def test_canonical_prompt_examples_follow_the_same_style_contract(self):
        prompt = (
            Path(__file__).resolve().parents[1]
            / "prompt/03_prompt_add_explanationText.md"
        ).read_text(encoding="utf-8")
        examples = [
            line
            for line in prompt.splitlines()
            if line.startswith(("正しい。", "間違い。"))
        ]

        self.assertTrue(examples)
        self.assertEqual(explanation_style_issues(examples), [])

    def test_rejects_missing_required_verdict_prefix(self):
        issues = explanation_style_issues(["定義に一致するため正しい。"], ["正しい"])

        self.assertEqual(len(issues), 1)
        self.assertIn("正しい。", issues[0])

    def test_rejects_prefix_that_disagrees_with_correct_choice(self):
        issues = explanation_style_issues(["間違い。定義に一致する。"], ["正しい"])

        self.assertEqual(len(issues), 1)
        self.assertIn("correctChoiceText", issues[0])

    def test_rejects_choice_repetition_without_reason(self):
        issues = explanation_style_issues(
            ["正しい。定義に一致する。"],
            ["正しい"],
            choice_texts=["定義に一致する"],
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("判断理由", issues[0])

    def test_rejects_the_missing_prefix_and_old_closing_seen_in_2019_q12(self):
        issues = explanation_style_issues(
            [
                "渦流式ガスメーターはカルマン渦の発生周波数から瞬時流量を測定する。",
                "高圧では圧力補正が必要となる。不要としている点が誤り。",
                "ガスが通過できない故障は不通であり、不動ではない。",
                "使用公差を外れる故障である。検定公差としている点が誤り。",
                "感震器には自動水平調整を行う機能がある。",
            ],
            ["正しい", "間違い", "間違い", "間違い", "正しい"],
        )

        self.assertEqual(len(issues), 7)
        self.assertEqual(sum("始めてください" in issue for issue in issues), 5)
        self.assertEqual(sum("点が誤り" in issue for issue in issues), 2)

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
