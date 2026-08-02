import re
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

    def test_group_choice_requires_correct_verdict_prefix(self):
        issues = explanation_style_issues(
            ["各組合せを比較すると、条件をすべて満たすのは選択肢3である。"],
            ["間違い", "間違い", "正しい", "間違い"],
            choice_texts=["組合せ1", "組合せ2", "組合せ3", "組合せ4"],
            question_type="group_choice",
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("正しい。", issues[0])

        self.assertEqual(
            explanation_style_issues(
                ["正しい。条件をすべて満たすのは選択肢3である。"],
                ["間違い", "間違い", "正しい", "間違い"],
                choice_texts=["組合せ1", "組合せ2", "組合せ3", "組合せ4"],
                question_type="group_choice",
            ),
            [],
        )

    def test_question_level_explanation_rejects_wrong_verdict_prefix(self):
        issues = explanation_style_issues(
            ["間違い。平均値は合計を個数で割って求める。"],
            ["正しい"],
            choice_texts=["合計を個数で割る"],
            question_type="flash_card",
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("問題単位の解説", issues[0])

    def test_question_level_explanation_rejects_repeated_correct_verdict(self):
        issues = explanation_style_issues(
            ["正しい。正しい組合せは1と3である。"],
            ["正しい", "間違い", "正しい"],
            choice_texts=["記述1", "記述2", "記述3"],
            question_type="group_choice",
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("該当するのは1と3", issues[0])

    def test_canonical_prompt_examples_follow_the_same_style_contract(self):
        prompt = (
            Path(__file__).resolve().parents[1]
            / "prompt/03_prompt_add_explanationText.md"
        ).read_text(encoding="utf-8")
        inline_examples = {}
        for line in prompt.splitlines():
            match = re.fullmatch(
                r"\| \*\*(.+?)\*\*・[^|]+ \| [^|]+ \| `(.*)` \|",
                line,
            )
            if match:
                inline_examples[match.group(1)] = match.group(2)

        self.assertEqual(
            set(inline_examples),
            {
                "用語と基本を押さえる",
                "違いを整理する",
                "条件を押さえる",
                "例外まで押さえる",
                "しくみと理由を理解する",
                "順序と流れをつかむ",
                "事例に当てはめて考える",
            },
        )
        examples = list(inline_examples.values())
        verdicts = [
            "正しい" if example.startswith("正しい。") else "間違い"
            for example in examples
        ]
        self.assertTrue(
            all(
                "正しくは、" in example
                for example, verdict in zip(examples, verdicts)
                if verdict == "間違い"
            )
        )
        self.assertEqual(
            explanation_style_issues(
                examples,
                verdicts,
                question_type="true_false",
            ),
            [],
        )

        calculation_block = prompt.split("```text\n", 1)[1].split("\n```", 1)[0]
        calculation = calculation_block.split("\n", 1)[1]
        self.assertEqual(
            explanation_style_issues(
                [calculation],
                ["正しい"],
                is_calculation_question=True,
            ),
            [],
        )

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
        self.assertIn("正しい選択肢の全文", issues[0])
        self.assertIn("正しい。", issues[0])

    def test_accepts_only_verdict_for_self_contained_correct_choice(self):
        issues = explanation_style_issues(
            ["正しい。"],
            ["正しい"],
            choice_texts=["燃料転換が完了している。"],
        )

        self.assertEqual(issues, [])

    def test_rejects_correct_choice_repeated_before_extra_sentence(self):
        issues = explanation_style_issues(
            ["正しい。燃料転換が完了している。これは国内の状況を示す。"],
            ["正しい"],
            choice_texts=["燃料転換が完了している。"],
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("正しい選択肢の全文", issues[0])

    def test_rejects_only_verdict_for_incorrect_choice(self):
        issues = explanation_style_issues(
            ["間違い。"],
            ["間違い"],
            choice_texts=["燃料転換は完了していない。"],
        )

        self.assertEqual(len(issues), 1)
        self.assertIn("正しい内容と判断を分ける差", issues[0])

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

    def test_accepts_flutter_math_calculation_derivation(self):
        issues = explanation_style_issues(
            [
                r"""間違い。正しくは、電界の強さは電圧を電極間の距離で割って求める。
\[
\begin{aligned}
E &= \frac{V}{d} \\
  &= \frac{100}{1 \times 10^{-3}} \\
  &= 1 \times 10^{5}\ \mathrm{V/m}
\end{aligned}
\]"""
            ],
            ["間違い"],
            choice_texts=["電界の強さは別の値となる。"],
            is_calculation_question=True,
        )

        self.assertEqual(issues, [])

    def test_calculation_requires_display_math_derivation(self):
        issues = explanation_style_issues(
            ["正しい。E = V / d = 100 / 0.001 = 100,000 V/m。"],
            ["正しい"],
            choice_texts=["電界の強さを求める。"],
            is_calculation_question=True,
        )

        self.assertTrue(any("flutter_math_fork対応" in issue for issue in issues))

    def test_calculation_rejects_inline_only_math(self):
        issues = explanation_style_issues(
            [r"正しい。\(E = V / d = 100 / 0.001 = 100000\)である。"],
            ["正しい"],
            choice_texts=["電界の強さを求める。"],
            is_calculation_question=True,
        )

        self.assertTrue(any("表示用" in issue for issue in issues))

    def test_rejects_unclosed_math_delimiter(self):
        issues = explanation_style_issues(
            [r"正しい。\[E = V / d = 100 / 0.001"],
            ["正しい"],
        )

        self.assertTrue(any("閉じられていません" in issue for issue in issues))

    def test_rejects_unbalanced_tex_braces(self):
        issues = explanation_style_issues(
            [r"正しい。\[E = \frac{100}{0.001 = 100000\]"],
            ["正しい"],
        )

        self.assertTrue(any("波括弧" in issue for issue in issues))

    def test_rejects_tex_command_outside_math_delimiters(self):
        issues = explanation_style_issues(
            [r"正しい。E = \frac{100}{0.001}。"],
            ["正しい"],
        )

        self.assertTrue(any("内側に置いてください" in issue for issue in issues))

    def test_accepts_lone_dollar_sign_as_currency_text(self):
        issues = explanation_style_issues(
            ["正しい。利用料金は$0.10である。"],
            ["正しい"],
        )

        self.assertEqual(issues, [])

    def test_rejects_math_specific_font_size_changes(self):
        issues = explanation_style_issues(
            [r"正しい。\[\small E = V / d = 100 / 0.001 = 100000\]"],
            ["正しい"],
            is_calculation_question=True,
        )

        self.assertTrue(any("横スクロール" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
