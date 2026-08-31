from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.pipeline import rebuild_gas_shunin_all_year_artifacts as module


class RebuildGasShuninAllYearArtifactsTest(unittest.TestCase):
    def test_reconcile_convert_uses_readback_fields_and_injects_missing_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "converted.json"
            path.write_text(
                json.dumps(
                    {
                        "questions": [
                            {
                                "questionId": "q1",
                                "isDeleted": False,
                                "isChoiceOnly": False,
                                "explanationText": "old",
                            },
                            {
                                "questionId": "q2",
                                "isDeleted": False,
                                "isChoiceOnly": False,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            live = [
                {
                    "questionId": "q1",
                    "isDeleted": False,
                    "isChoiceOnly": False,
                    "explanationText": "live",
                    "updatedAt": "ignored",
                },
                {
                    "questionId": "q2",
                    "isDeleted": True,
                    "isChoiceOnly": False,
                },
                {
                    "questionId": "q3",
                    "isDeleted": False,
                    "isChoiceOnly": False,
                    "explanationText": "injected",
                },
            ]

            report = module.reconcile_convert_with_readback(
                converted_path=path,
                live_questions=live,
            )
            questions = module.load_json(path)["questions"]

            self.assertEqual(report["readbackReplacedDocumentCount"], 2)
            self.assertEqual(report["readbackInjectedActiveDocumentCount"], 1)
            self.assertEqual([question["questionId"] for question in questions], ["q1", "q2", "q3"])
            self.assertEqual(questions[0]["explanationText"], "live")
            self.assertNotIn("updatedAt", questions[0])
            self.assertTrue(questions[1]["isDeleted"])

    def test_verify_active_display_parity_compares_all_non_metadata_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "converted.json"
            module.write_json(
                path,
                {
                    "questions": [
                        {
                            "questionId": "q1",
                            "isDeleted": False,
                            "isChoiceOnly": False,
                            "explanationText": "same",
                        }
                    ]
                },
            )
            live = [
                {
                    "questionId": "q1",
                    "isDeleted": False,
                    "isChoiceOnly": False,
                    "explanationText": "same",
                    "updatedAt": "server metadata",
                }
            ]

            report = module.verify_active_display_parity(
                converted_paths=[path],
                live_questions=live,
            )

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["generatedActiveDisplayCount"], 1)

    def test_merged_source_stem_removes_timestamped_suffix(self) -> None:
        self.assertEqual(
            module.merged_source_stem(
                Path("question_2023_firestore_2_merged_20260830_2259.json")
            ),
            "question_2023_firestore_2",
        )


if __name__ == "__main__":
    unittest.main()
