from __future__ import annotations

import unittest

from scripts.pipeline.build_gas_shunin_otsu_law_explanation_patches import (
    build_choice_explanation,
    build_suggested_questions,
    parse_law_references,
)


class BuildGasShuninOtsuLawExplanationPatchesTest(unittest.TestCase):
    def test_parse_law_references_direct_and_inherited(self) -> None:
        question = {
            "questionBodyText": "次のガス事故のうち、ガス事故速報を報告することが法令で規定されていないものの組合せはどれか。",
        }
        refs = parse_law_references(question, 0, "📌 関連: 一号、五号")
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["lawTitle"], "ガス関係報告規則")
        self.assertEqual(refs[0]["article"], "4条")
        self.assertEqual(refs[0]["item"], "一号")
        self.assertEqual(refs[0]["verificationStatus"], "candidate")
        self.assertEqual(refs[1]["item"], "五号")

    def test_build_choice_explanation_marks_wrong_phrase_and_correction(self) -> None:
        question = {
            "choiceTextList": ["ガス小売事業を営もうとする者は、経済産業大臣の許可を受けなければならない。"],
            "choiceTextMarkedList": ["ガス小売事業を営もうとする者は、経済産業大臣の[wrong]許可[/wrong]を受けなければならない。"],
            "explanation_choice_snippets": [["正しくは: 登録\n📌 関連: 法3条(事業の登録)"]],
        }
        actual = build_choice_explanation(question, 0)
        self.assertIn("この記述は間違いです。", actual)
        self.assertIn("誤りは「許可」", actual)
        self.assertIn("正しくは「登録」です。", actual)
        self.assertIn("ガス事業法第3条", actual)

    def test_build_suggested_questions_accident_theme(self) -> None:
        question = {
            "questionBodyText": "次のガス事故のうち、ガス事故速報を報告することが法令で規定されていないものはどれか。",
            "choiceTextMarkedList": ["[wrong]供給支障戸数が20[/wrong]"],
            "explanation_choice_snippets": [["📌 関連: ガス関係報告規則4条三号"]],
        }
        law_references = [[
            {
                "lawTitle": "ガス関係報告規則",
                "lawAlias": "ガス関係報告規則",
                "referenceDate": "2026-06-01",
                "verificationStatus": "verified",
                "article": "4条",
                "item": "三号",
                "choiceIndex": 0,
                "role": "current_basis",
                "scope": "choice",
            }
        ]]
        questions, details = build_suggested_questions(question, law_references)
        self.assertEqual(len(questions), 3)
        self.assertEqual(questions[0], "どの事故なら報告対象になる？")
        self.assertEqual(details[0]["question"], questions[0])
        self.assertIn("ガス関係報告規則第4条三号", details[0]["answer"])


if __name__ == "__main__":
    unittest.main()
