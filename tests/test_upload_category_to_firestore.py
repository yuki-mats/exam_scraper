from __future__ import annotations

import unittest
from datetime import datetime

from scripts.common.repaso_firestore_schema import (
    validate_folder_doc,
    validate_question_set_doc,
)
from scripts.upload import upload_category_to_firestore as module


class UploadCategoryToFirestoreTests(unittest.TestCase):
    def test_zero_question_set_is_marked_deleted(self) -> None:
        qset = {
            "questionSetId": "set-zero",
            "name": "Zero Count",
            "questionCount": 0,
        }

        self.assertTrue(module.resolve_question_set_is_deleted(qset))

    def test_positive_question_set_is_not_marked_deleted(self) -> None:
        qset = {
            "questionSetId": "set-one",
            "name": "One Count",
            "questionCount": 1,
        }

        self.assertFalse(module.resolve_question_set_is_deleted(qset))

    def test_explicit_is_deleted_true_is_preserved(self) -> None:
        qset = {
            "questionSetId": "set-manual",
            "name": "Manual Delete",
            "questionCount": 3,
            "isDeleted": True,
        }

        self.assertTrue(module.resolve_question_set_is_deleted(qset))

    def test_explicit_is_deleted_false_is_preserved_for_empty_official_sets(self) -> None:
        qset = {
            "questionSetId": "set-official-empty",
            "name": "Official Empty Set",
            "questionCount": 0,
            "isDeleted": False,
        }

        self.assertFalse(module.resolve_question_set_is_deleted(qset))

    def test_mecnet_kokushi_license_name_matches_repaso_license_name(self) -> None:
        path = "output/mecnet-kokushi/category/category.json"

        self.assertEqual(module.resolve_license_name(path, None), "医師")

    def test_license_name_prefers_category_metadata(self) -> None:
        path = "output/kougai-taiki-1/category/category.json"
        category = {"metadata": {"licenseName": "大気関係第1種公害防止管理者"}}

        self.assertEqual(
            module.resolve_license_name(path, None, category),
            "大気関係第1種公害防止管理者",
        )

    def test_copy_reference_fields_keeps_only_supported_keys(self) -> None:
        doc_data = {"name": "01_公害総論"}
        source_data = {
            "canonicalFolderId": "kougai_f01_kougai_soron",
            "sourceSharedFolderId": "kougai_f01_kougai_soron",
            "ignored": "value",
        }

        module.copy_reference_fields(doc_data, source_data, module.FOLDER_REFERENCE_FIELDS)

        self.assertEqual(
            doc_data,
            {
                "name": "01_公害総論",
                "canonicalFolderId": "kougai_f01_kougai_soron",
                "sourceSharedFolderId": "kougai_f01_kougai_soron",
            },
        )

    def test_schema_allows_canonical_folder_and_question_set_references(self) -> None:
        now = datetime.now()

        validate_folder_doc(
            {
                "name": "01_公害総論",
                "isDeleted": False,
                "isPublic": True,
                "isOfficial": True,
                "aggregatedQuestionTags": [],
                "licenseName": "公害防止管理者",
                "qualificationId": "kougai-taiki-1",
                "licenseNames": [
                    "大気関係第1種公害防止管理者",
                    "大気関係第2種公害防止管理者",
                ],
                "qualificationIds": [
                    "kougai-taiki-1",
                    "kougai-taiki-2",
                ],
                "questionCount": 0,
                "createdById": module.CREATED_BY_ID,
                "updatedById": module.UPDATED_BY_ID,
                "createdAt": now,
                "updatedAt": now,
                "canonicalFolderId": "kougai_f01_kougai_soron",
                "sourceSharedFolderId": "kougai_f01_kougai_soron",
            },
            doc_id="kougai-taiki-1_f01_kougai_soron",
        )

        validate_question_set_doc(
            {
                "name": "1-1 環境基本法",
                "folderId": "kougai-taiki-1_f01_kougai_soron",
                "qualificationId": "kougai-taiki-1",
                "questionCount": 0,
                "isDeleted": False,
                "isOfficial": True,
                "createdById": module.CREATED_BY_ID,
                "updatedById": module.UPDATED_BY_ID,
                "createdAt": now,
                "updatedAt": now,
                "canonicalFolderId": "kougai_f01_kougai_soron",
                "canonicalQuestionSetId": "kougai_qs01_01_kankyo",
                "sourceSharedQuestionSetId": "kougai_qs01_01_kankyo",
            },
            doc_id="kougai-taiki-1_qs01_01_kankyo",
        )


if __name__ == "__main__":
    unittest.main()
