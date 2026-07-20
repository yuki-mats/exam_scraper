from __future__ import annotations

import unittest

from scripts.check.audit_calculation_explanations import is_calculation_candidate


class AuditCalculationExplanationsTests(unittest.TestCase):
    def test_explicit_true_classifies_without_heuristic_words(self) -> None:
        self.assertTrue(
            is_calculation_candidate(
                {"isCalculationQuestion": True, "questionBodyText": "次の値を選べ。"}
            )
        )

    def test_explicit_false_overrides_legacy_heuristic(self) -> None:
        self.assertFalse(
            is_calculation_candidate(
                {
                    "isCalculationQuestion": False,
                    "questionBodyText": "流量を計算して最も近い値を求めよ。",
                    "choiceTextList": ["1 m3", "2 m3"],
                }
            )
        )

    def test_missing_flag_keeps_legacy_data_auditable(self) -> None:
        self.assertTrue(
            is_calculation_candidate(
                {
                    "questionBodyText": "流量を計算して最も近い値を求めよ。",
                    "choiceTextList": ["1 m3", "2 m3"],
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
