from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.check.check_gas_shunin_upload_gate import check_upload_json


class GasShuninUploadGateTests(unittest.TestCase):
    def write_upload_json(self, directory: Path, questions: list[dict]) -> Path:
        path = directory / "upload.json"
        path.write_text(
            json.dumps({"questions": questions}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_accepts_existing_id_and_source_unique_key_derived_new_id(self) -> None:
        with TemporaryDirectory() as tmp:
            upload_path = self.write_upload_json(
                Path(tmp),
                [
                    {
                        "originalQuestionId": "existing-original",
                        "questionId": "doc-1",
                        "questionSetId": "qset-existing",
                    },
                    {
                        "originalQuestionId": "new-original",
                        "questionId": "gas-shunin-kou-2024-law-q01-s01",
                        "questionSetId": "qset-new",
                    },
                ],
            )

            issues, summary, issue_counts = check_upload_json(
                [upload_path],
                {"existing-original": {"doc-1"}},
                {"new-original": {"gas-shunin-kou-2024-law-q01-s01"}},
                {"doc-1"},
                {"doc-1": "qset-existing"},
                max_samples=10,
            )

        self.assertEqual(issues, [])
        self.assertEqual(issue_counts, {})
        self.assertEqual(summary["existingFirestoreOriginalQuestionCount"], 1)
        self.assertEqual(summary["newOriginalQuestionCount"], 1)

    def test_rejects_existing_id_change_and_new_id_collision(self) -> None:
        with TemporaryDirectory() as tmp:
            upload_path = self.write_upload_json(
                Path(tmp),
                [
                    {
                        "originalQuestionId": "existing-original",
                        "questionId": "replacement",
                        "questionSetId": "qset-existing",
                    },
                    {
                        "originalQuestionId": "new-original",
                        "questionId": "doc-1",
                        "questionSetId": "qset-new",
                    },
                ],
            )

            _, _, issue_counts = check_upload_json(
                [upload_path],
                {"existing-original": {"doc-1"}},
                {"new-original": {"gas-shunin-kou-2024-law-q01-s01"}},
                {"doc-1"},
                {"doc-1": "qset-existing"},
                max_samples=10,
            )

        self.assertEqual(issue_counts["existing_firestore_question_id_would_change"], 1)
        self.assertEqual(issue_counts["new_question_id_not_source_unique_key_derived"], 1)
        self.assertEqual(issue_counts["new_question_id_collides_with_existing_firestore_id"], 1)

    def test_rejects_missing_and_changed_question_set_id(self) -> None:
        with TemporaryDirectory() as tmp:
            upload_path = self.write_upload_json(
                Path(tmp),
                [
                    {"originalQuestionId": "existing-original", "questionId": "doc-1"},
                    {
                        "originalQuestionId": "existing-original",
                        "questionId": "doc-2",
                        "questionSetId": "qset-replacement",
                    },
                ],
            )

            _, _, issue_counts = check_upload_json(
                [upload_path],
                {"existing-original": {"doc-1", "doc-2"}},
                {},
                {"doc-1", "doc-2"},
                {"doc-1": "qset-existing-1", "doc-2": "qset-existing-2"},
                max_samples=10,
            )

        self.assertEqual(issue_counts["missing_question_set_id"], 1)
        self.assertEqual(issue_counts["existing_firestore_question_set_id_would_change"], 2)


if __name__ == "__main__":
    unittest.main()
