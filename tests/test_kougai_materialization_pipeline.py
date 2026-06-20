from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check import check_kougai_official_question_set_ids as check_module
from scripts.pipeline import materialize_kougai_qualification_uploads as materialize_module


class KougaiMaterializationPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical = materialize_module.load_json(materialize_module.DEFAULT_CANONICAL_CATEGORY_JSON)
        cls.mapping = materialize_module.load_json(materialize_module.DEFAULT_MAPPING_JSON)
        cls.context = materialize_module.category_context(cls.canonical, cls.mapping)

    def test_air_overview_question_is_materialized_to_air_and_dust_qualifications(self) -> None:
        records = [
            {
                "questionId": "source-doc-1",
                "questionSetId": "kougai_qs02_01",
                "questionText": "大気概論の問題",
                "questionType": "true_false",
            }
        ]

        materialized = materialize_module.materialize_records(records, self.context)

        self.assertEqual(
            sorted(materialized),
            [
                "kougai-ippan-funjin",
                "kougai-taiki-1",
                "kougai-taiki-2",
                "kougai-taiki-3",
                "kougai-taiki-4",
                "kougai-tokutei-funjin",
            ],
        )
        taiki_1 = materialized["kougai-taiki-1"][0]
        self.assertEqual(taiki_1["questionId"], "kougai-taiki-1__source-doc-1")
        self.assertEqual(taiki_1["folderId"], "kougai-taiki-1_f02_taiki_gairon")
        self.assertEqual(taiki_1["questionSetId"], "kougai-taiki-1_qs02_01")
        self.assertEqual(taiki_1["canonicalFolderId"], "kougai_f02_taiki_gairon")
        self.assertEqual(taiki_1["canonicalQuestionSetId"], "kougai_qs02_01")
        self.assertEqual(taiki_1["sourceSharedQuestionId"], "source-doc-1")

    def test_invalid_legacy_question_set_id_stops_materialization(self) -> None:
        records = [
            {
                "questionId": "source-doc-1",
                "questionSetId": "kougai_qs08_taigai",
                "questionText": "旧IDの問題",
                "questionType": "true_false",
            }
        ]

        with self.assertRaisesRegex(ValueError, "not found in canonical category"):
            materialize_module.materialize_records(records, self.context)

    def test_official_question_set_check_reports_legacy_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            category_path = root / "category.json"
            category_path.write_text(
                json.dumps({"questionSets": [{"questionSetId": "kougai_qs02_01"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            stage_dir = root / "questions_json" / "2025" / "22_questionSetId_linked"
            stage_dir.mkdir(parents=True)
            (stage_dir / "question_2025_yakutik_1_questionSetId_linked.json").write_text(
                json.dumps(
                    [
                        {"original_question_id": "ok", "questionSetId": "kougai_qs02_01"},
                        {"original_question_id": "legacy", "questionSetId": "kougai_qs08_taigai"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = check_module.scan_question_set_ids(
                questions_root=root / "questions_json",
                category_json=category_path,
                stage="22_questionSetId_linked",
            )

        self.assertEqual(summary["filesScanned"], 1)
        self.assertEqual(summary["recordsScanned"], 2)
        self.assertEqual(summary["invalidQuestionSetIdCounts"], {"kougai_qs08_taigai": 1})
        self.assertEqual(summary["invalidRecordCount"], 1)


if __name__ == "__main__":
    unittest.main()
