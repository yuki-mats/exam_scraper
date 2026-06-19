from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from scripts.pipeline import materialize_gas_shunin_site_source as module


class MaterializeGasShuninSiteSourceTest(unittest.TestCase):
    def test_partial_firestore_overlap_keeps_question_and_marks_site_only_statements(self) -> None:
        question = {
            "examYear": 2025,
            "category": "法令",
            "questionLabel": "問1",
            "questionType": "true_false",
            "questionBodyText": "既存本文",
            "choiceTextList": ["既存選択肢1", "既存選択肢2"],
            "choiceTextMarkedList": ["既存選択肢1", "既存選択肢2"],
            "correctChoiceText": ["正しい", "間違い"],
            "question_url": "https://gassyunin.com/exam/kou/kou_2025/#law-q1",
            "source_question_id": "2025:law:問1",
            "explanation_choice_snippets": [["既存解説1"], ["既存解説2"]],
        }

        record, invalid = module.build_site_only_record(
            qualification="gas-shunin-kou",
            question=question,
            firestore_keys={"gas-shunin:kou:2025:law:q01:s01"},
            empty_choice_slot_count=5,
        )

        self.assertEqual(invalid, [])
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["questionBodyText"], "既存本文")
        self.assertEqual(record["choiceTextList"], ["既存選択肢1", "既存選択肢2"])
        self.assertEqual(record["sourceProvider"], "gassyunin.com")
        self.assertTrue(record["isSiteSourced"])
        self.assertEqual(record["siteOnlyStatementNumbers"], [2])
        self.assertEqual(record["firestoreRegisteredStatementNumbers"], [1])
        self.assertEqual(
            record["statementSourceStatuses"],
            [
                {
                    "statementNo": 1,
                    "sourceUniqueKey": "gas-shunin:kou:2025:law:q01:s01",
                    "firestoreRegistered": True,
                    "siteOnly": False,
                },
                {
                    "statementNo": 2,
                    "sourceUniqueKey": "gas-shunin:kou:2025:law:q01:s02",
                    "firestoreRegistered": False,
                    "siteOnly": True,
                },
            ],
        )

    def test_full_firestore_overlap_excludes_question(self) -> None:
        question = {
            "examYear": 2025,
            "category": "基礎理論",
            "questionLabel": "問2",
            "questionType": "true_false",
            "questionBodyText": "既存本文",
            "choiceTextList": ["既存選択肢"],
            "question_url": "https://gassyunin.com/exam/kou/kou_2025/#kiso-q2",
        }

        record, invalid = module.build_site_only_record(
            qualification="gas-shunin-kou",
            question=question,
            firestore_keys={"gas-shunin:kou:2025:kiso:q02:s01"},
            empty_choice_slot_count=5,
        )

        self.assertEqual(invalid, [])
        self.assertIsNone(record)

    def test_materialize_archives_raw_files_and_writes_site_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "output" / "gas-shunin-kou" / "questions_json" / "2025" / "00_source"
            source_dir.mkdir(parents=True)
            (source_dir / "question_2025_1.json").write_text(
                json.dumps(
                    {
                        "list_group_id": "2025",
                        "question_bodies": [
                            {
                                "examYear": 2025,
                                "category": "法令",
                                "questionLabel": "問1",
                                "questionType": "true_false",
                                "questionBodyText": "既存本文",
                                "choiceTextList": ["既存選択肢"],
                                "question_url": "https://gassyunin.com/exam/kou/kou_2025/#law-q1",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            snapshot_dir = root / "snapshot"
            (snapshot_dir / "reconstructed").mkdir(parents=True)
            (snapshot_dir / "reconstructed" / "questions.json").write_text(
                json.dumps({"questions": []}),
                encoding="utf-8",
            )

            args = Namespace(
                qualification="gas-shunin-kou",
                output_root=root / "output",
                firestore_snapshot_dir=snapshot_dir,
                report_root=root / "report",
                timestamp="test",
                years=[2025],
                empty_choice_slot_count=5,
                chunk_size=25,
                archive_raw_site=True,
                dry_run=False,
            )

            module.materialize_site_only(args)

            self.assertFalse((source_dir / "question_2025_1.json").exists())
            self.assertTrue((source_dir / module.ARCHIVE_DIR_NAME / "question_2025_1.json").exists())
            written = source_dir / "question_2025_gassyunin_site_1.json"
            self.assertTrue(written.exists())
            payload = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(payload["sourceProvider"], "gassyunin.com")
            self.assertEqual(len(payload["question_bodies"]), 1)


if __name__ == "__main__":
    unittest.main()
