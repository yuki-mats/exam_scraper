from __future__ import annotations

import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.pipeline import rebuild_gas_shunin_all_year_artifacts as module


class RebuildGasShuninAllYearArtifactsTest(unittest.TestCase):
    def test_normal_apply_reads_canonical_and_only_regenerates_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            bundle={'list_group_id':'2021','questions':[{'questionId':'q'}],'total_count':1}
            with mock.patch.object(module,'ROOT_DIR',root), \
                 mock.patch.object(module,'QUALIFICATIONS',('gas-shunin-otsu',)), \
                 mock.patch.object(module,'YEARS',(2021,)), \
                 mock.patch.object(module,'EXPECTED_COUNTS',{'gas-shunin-otsu':1}), \
                 mock.patch.object(module,'verify_official_pdf_evidence',return_value={}), \
                 mock.patch.object(module,'official_index_by_id',return_value=({},{})), \
                 mock.patch.object(module,'load_canonical_bundles',return_value={'gas-shunin-otsu':{2021:bundle}}), \
                 mock.patch.object(module,'save_initial_canonical') as creation, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(['--apply','--receipt',str(root/'receipt.json')]),0)
                creation.assert_not_called()
            self.assertEqual(len(list((root/'output/gas-shunin-otsu/questions_json/2021/40_convert').glob('2021_firestore_*.json'))),1)

    def test_normalize_publication_document_keeps_first_three_complete_details(self) -> None:
        question = {
            "questionId": "q1",
            "isDeleted": False,
            "isChoiceOnly": False,
            "createdAt": "server metadata",
            "questionSetRef": "legacy ref",
            "suggestedQuestions": ["old"],
            "suggestedQuestionDetails": [
                {"question": f"質問{index}", "answer": f"回答{index}"}
                for index in range(1, 6)
            ],
        }

        actual = module.normalize_publication_document(question)

        self.assertNotIn("createdAt", actual)
        self.assertNotIn("questionSetRef", actual)
        self.assertEqual(actual["suggestedQuestions"], ["質問1", "質問2", "質問3"])
        self.assertEqual(len(actual["suggestedQuestionDetails"]), 3)

    def test_build_canonical_documents_requires_official_index_for_every_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = Path(temporary_directory)
            (snapshot / "reconstructed").mkdir()
            (snapshot / "reconstructed/questions.json").write_text(
                json.dumps(
                    {
                        "questions": [
                            {
                                "questionId": "q1",
                                "isDeleted": False,
                                "isChoiceOnly": False,
                                "examYear": 2025,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(module.EXPECTED_COUNTS, {"sample": 1}, clear=False):
                with self.assertRaisesRegex(ValueError, "公式PDFに対応しない"):
                    module.build_canonical_documents(
                        qualification="sample",
                        snapshot_dir=snapshot,
                        official_documents={},
                    )

    def test_compare_with_snapshot_reports_only_real_publication_differences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = Path(temporary_directory)
            (snapshot / "reconstructed").mkdir()
            (snapshot / "reconstructed/questions.json").write_text(
                json.dumps(
                    {
                        "questions": [
                            {
                                "questionId": "q1",
                                "isDeleted": False,
                                "isChoiceOnly": False,
                                "explanationText": "同じ",
                                "updatedAt": "server metadata",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            report = module.compare_with_snapshot(
                canonical_questions=[
                    {
                        "questionId": "q1",
                        "isDeleted": False,
                        "isChoiceOnly": False,
                        "explanationText": "同じ",
                    }
                ],
                snapshot_dir=snapshot,
            )

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["differentQuestionCount"], 0)

    def test_build_suggestion_patch_is_existing_document_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            snapshot = Path(temporary_directory)
            (snapshot / "reconstructed").mkdir()
            live = {
                "questionId": "q1",
                "questionText": "問題文",
                "isDeleted": False,
                "isChoiceOnly": False,
                "suggestedQuestions": ["質問1", "質問2", "質問3", "質問4"],
                "suggestedQuestionDetails": [
                    {"question": f"質問{index}", "answer": f"回答{index}"}
                    for index in range(1, 5)
                ],
            }
            (snapshot / "reconstructed/questions.json").write_text(
                json.dumps({"questions": [live]}),
                encoding="utf-8",
            )
            local = module.normalize_publication_document(live)

            patch = module.build_suggestion_patch(
                canonical_questions=[local],
                snapshot_dir=snapshot,
            )

            self.assertEqual(
                patch["writeFields"],
                ["suggestedQuestions", "suggestedQuestionDetails"],
            )
            self.assertEqual(patch["total_count"], 1)
            self.assertEqual(len(patch["questions"][0]["suggestedQuestionDetails"]), 3)

            rollback = module.build_suggestion_rollback(
                canonical_questions=[local],
                snapshot_dir=snapshot,
            )
            self.assertEqual(rollback["total_count"], 1)
            self.assertEqual(
                len(rollback["questions"][0]["suggestedQuestionDetails"]), 4
            )


if __name__ == "__main__":
    unittest.main()
