from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.common.question_learning_patterns import (
    DEFAULT_CATALOG_PATH,
    QUESTION_LEARNING_PATTERN_FIELD,
    QUESTION_LEARNING_PATTERN_IDS,
    load_question_learning_pattern_catalog,
    question_learning_pattern_id_error,
)
from scripts.setup.sync_question_learning_pattern_catalog import sync_catalog


class QuestionLearningPatternCatalogTests(unittest.TestCase):
    def test_canonical_catalog_has_eight_stable_patterns(self) -> None:
        catalog = load_question_learning_pattern_catalog()

        self.assertEqual(catalog.field_name, QUESTION_LEARNING_PATTERN_FIELD)
        self.assertEqual(
            catalog.ids,
            (
                "terms_basics",
                "differences_relationships",
                "conditions_scope",
                "principles_exceptions",
                "mechanisms_reasons",
                "sequence_flow",
                "scenario_application",
                "calculation",
            ),
        )
        self.assertEqual(catalog.ids, QUESTION_LEARNING_PATTERN_IDS)
        self.assertTrue(all(pattern.display_name for pattern in catalog.patterns))
        self.assertTrue(all(pattern.description for pattern in catalog.patterns))

    def test_validator_accepts_catalog_id_and_rejects_unknown_id(self) -> None:
        self.assertIsNone(
            question_learning_pattern_id_error("terms_basics", required=True)
        )
        self.assertIsNotNone(
            question_learning_pattern_id_error("unknown", required=True)
        )
        self.assertIsNotNone(
            question_learning_pattern_id_error(
                "conditions_exceptions",
                required=True,
            )
        )
        self.assertIsNotNone(question_learning_pattern_id_error(None, required=True))
        self.assertIsNone(question_learning_pattern_id_error(None, required=False))

    def test_loader_rejects_duplicate_ids(self) -> None:
        payload = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
        payload["patterns"][1]["id"] = payload["patterns"][0]["id"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "IDが重複"):
                load_question_learning_pattern_catalog(path)

    def test_app_catalog_sync_and_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repaso_root = Path(directory)
            target = sync_catalog(repaso_root=repaso_root, check=False)
            self.assertEqual(target.read_bytes(), DEFAULT_CATALOG_PATH.read_bytes())
            self.assertEqual(sync_catalog(repaso_root=repaso_root, check=True), target)
            target.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "正本と一致"):
                sync_catalog(repaso_root=repaso_root, check=True)


if __name__ == "__main__":
    unittest.main()
