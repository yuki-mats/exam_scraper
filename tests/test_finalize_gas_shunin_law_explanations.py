from __future__ import annotations

import unittest

from scripts.pipeline.finalize_gas_shunin_law_explanations import (
    BasisContext,
    build_explanation,
    ensure_basis_chip,
    resolve_basis,
)


class FinalizeGasShuninLawExplanationsTest(unittest.TestCase):
    def test_wrong_explanation_uses_safe_generic_lead_for_non_explicit_correction(self) -> None:
        actual = build_explanation(
            verdict="間違い",
            existing_explanation="間違い。経済産業大臣の許可を受けるものではない。",
            display="ガス事業法第64条第1項",
            basis_text="保安規程は事業の開始前に届け出なければならない。",
        )

        self.assertEqual(
            actual,
            "間違い。選択肢の記載が誤り。ガス事業法第64条第1項は、"
            "保安規程は事業の開始前に届け出なければならない。",
        )

    def test_basis_chip_drops_unpaired_questions_and_keeps_valid_details(self) -> None:
        entry = {
            "suggestedQuestions": ["回答のない質問", "既存の根拠条文は？"],
            "suggestedQuestionDetails": [
                {"question": "既存の根拠条文は？", "answer": "古い根拠。"},
            ],
        }

        ensure_basis_chip(entry, ["ガス事業法第64条第1項"])

        self.assertEqual(entry["suggestedQuestions"], ["既存の根拠条文は？"])
        self.assertEqual(
            entry["suggestedQuestionDetails"],
            [
                {
                    "question": "既存の根拠条文は？",
                    "answer": "根拠は、ガス事業法第64条第1項。",
                }
            ],
        )

    def test_compound_basis_switches_from_act_to_its_enforcement_rule(self) -> None:
        actual = resolve_basis(
            "ガス事業法第69条第3項、同規則第104条第2項",
            context=BasisContext(),
            existing_refs=[],
        )

        self.assertEqual(
            [(ref["lawTitle"], ref["article"], ref["paragraph"]) for ref in actual],
            [
                ("ガス事業法", "69", "3"),
                ("ガス事業法施行規則", "104", "2"),
            ],
        )

    def test_appendix_item_is_not_misclassified_as_article_item(self) -> None:
        actual = resolve_basis(
            "ガス事業法施行令第15条・別表第二第一号",
            context=BasisContext(),
            existing_refs=[],
        )

        self.assertEqual(len(actual), 1)
        self.assertIsNone(actual[0]["item"])
        self.assertEqual(actual[0]["display"], "ガス事業法施行令第15条・別表第二第一号")


if __name__ == "__main__":
    unittest.main()
