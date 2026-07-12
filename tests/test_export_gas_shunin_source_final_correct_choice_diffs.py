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
    def test_current_publication_has_one_source_label_exception(self) -> None:
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
        self.assertEqual(report["differenceQuestionCount"], 1)
        self.assertEqual(report["differenceChoiceCount"], 2)
        self.assertTrue(report["decisionDiffSetMatchesActualDiffSet"])

        [diff] = report["diffs"]
        self.assertEqual(diff["sourceQuestionKey"], "gas-shunin:otsu:2024:law:q04")
        self.assertEqual(diff["changedChoiceNumbers"], [3, 5])
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
