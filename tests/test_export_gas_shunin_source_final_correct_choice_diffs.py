import unittest

from scripts.check.export_gas_shunin_source_final_correct_choice_diffs import (
    ROOT_DIR,
    build_report,
)
from scripts.check.check_gas_shunin_law_explanation_publication import (
    DEFAULT_REVIEW_DIR,
    DEFAULT_UPLOAD_DIR,
)


class ExportGasShuninSourceFinalCorrectChoiceDiffsTest(unittest.TestCase):
    def test_current_publication_source_label_exceptions_match_decisions(self) -> None:
        report = build_report(
            review_dir=DEFAULT_REVIEW_DIR,
            upload_dir=DEFAULT_UPLOAD_DIR,
            decision_dir=ROOT_DIR
            / "output"
            / "gas-shunin-all"
            / "review"
            / "law_explanation_refresh"
            / "decisions",
        )

        self.assertEqual(report["sourceQuestionCount"], 261)
        self.assertEqual(report["comparedChoiceCount"], 1289)
        self.assertEqual(report["comparisonErrorCount"], 0)
        self.assertEqual(report["differenceQuestionCount"], 4)
        self.assertEqual(report["differenceChoiceCount"], 17)
        self.assertTrue(report["decisionDiffSetMatchesActualDiffSet"])

        diffs = {diff["sourceQuestionKey"]: diff for diff in report["diffs"]}
        self.assertEqual(
            {key: diff["changedChoiceNumbers"] for key, diff in diffs.items()},
            {
                "gas-shunin:otsu:2020:law:q03": [1, 2, 3, 4, 5],
                "gas-shunin:otsu:2021:law:q04": [1, 2, 3, 4, 5],
                "gas-shunin:otsu:2023:law:q04": [1, 2, 3, 4, 5],
                "gas-shunin:otsu:2024:law:q04": [3, 5],
            },
        )
        diff = diffs["gas-shunin:otsu:2024:law:q04"]
        self.assertEqual(
            diff["sourceCorrectChoiceText"],
            ["間違い", "正しい", "正しい", "間違い", "間違い"],
        )
        self.assertEqual(
            diff["finalCorrectChoiceText"],
            ["間違い", "正しい", "間違い", "間違い", "正しい"],
        )


if __name__ == "__main__":
    unittest.main()
