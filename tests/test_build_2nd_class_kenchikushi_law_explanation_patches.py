from __future__ import annotations

import unittest

from scripts.pipeline.build_2nd_class_kenchikushi_law_explanation_patches import (
    build_choice_explanation,
    build_suggested_questions,
    choose_best_snippet,
    parse_law_references,
)


class Build2ndClassKenchikushiLawExplanationPatchesTest(unittest.TestCase):
    def test_parse_law_references_supports_inherited_segments(self) -> None:
        refs = parse_law_references(
            0,
            [
                "選択肢1. 該当条文は法第7条 第1項、第2項及び法第7条の2第1項になります。"
            ],
        )
        self.assertEqual(len(refs), 3)
        self.assertEqual(refs[0]["lawTitle"], "建築基準法")
        self.assertEqual(refs[0]["article"], "7条")
        self.assertEqual(refs[0]["paragraph"], "1項")
        self.assertEqual(refs[1]["article"], "7条")
        self.assertEqual(refs[1]["paragraph"], "2項")
        self.assertEqual(refs[2]["article"], "7条の2")

    def test_parse_law_references_supports_architect_act_alias(self) -> None:
        refs = parse_law_references(
            0,
            [
                "該当条文は士法第22条の2、士法施工規則第17条の36になります。"
            ],
        )
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["lawTitle"], "建築士法")
        self.assertEqual(refs[1]["lawTitle"], "建築士法施行規則")
        self.assertEqual(refs[1]["article"], "17条の36")

    def test_parse_law_references_ignores_generic_alias_without_locator(self) -> None:
        refs = parse_law_references(
            0,
            [
                "外壁の延焼のおそれのある部分を準防火性能の技術的基準に合う構造（告示の仕様や大臣認定など）にする。"
            ],
        )
        self.assertEqual(refs, [])

    def test_choose_best_snippet_prefers_legal_reasoning(self) -> None:
        selected = choose_best_snippet(
            [
                "選択肢1. 〇 正しいです。",
                "選択肢1. 該当条文は法第28条 第3項、令第20条の3 第1項 第二号になります。よって誤りとなります。",
            ]
        )
        self.assertIn("法第28条", selected)

    def test_build_choice_explanation_cleans_prefixes(self) -> None:
        question = {
            "choiceTextList": ["発熱量の合計が10kWの火を使用する器具のみを設けた調理室"],
            "correctChoiceText": ["間違い"],
            "explanation_choice_snippets": [[
                "選択肢1. 該当条文は法第28条 第3項、令第20条の3 第1項 第二号になります。\nよって誤りとなります。"
            ]],
        }
        actual = build_choice_explanation(question, 0)
        self.assertNotIn("選択肢1.", actual)
        self.assertIn("法第28条", actual)
        self.assertIn("誤り", actual)

    def test_build_suggested_questions_returns_saved_answers(self) -> None:
        question = {
            "questionBodyText": "用語に関する次の記述のうち、建築基準法上、正しいものはどれか。",
            "choiceTextList": ["床が地盤面下にある階で..."],
            "correctChoiceText": ["間違い"],
        }
        refs = [[
            {
                "lawTitle": "建築基準法施行令",
                "lawAlias": "令",
                "referenceDate": "2026-06-01",
                "verificationStatus": "verified",
                "article": "1条",
                "paragraph": "2項",
                "choiceIndex": 0,
                "role": "current_basis",
                "scope": "choice",
            }
        ]]
        questions, details = build_suggested_questions(question, refs, ["地階の定義が違うため誤りです。"])
        self.assertEqual(len(questions), 3)
        self.assertEqual(details[0]["question"], questions[0])
        self.assertIn("建築基準法施行令第1条第2項", details[0]["answer"])


if __name__ == "__main__":
    unittest.main()
