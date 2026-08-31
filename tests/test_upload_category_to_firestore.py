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

    def test_explicit_is_deleted_false_is_overridden_for_empty_sets(self) -> None:
        qset = {
            "questionSetId": "set-official-empty",
            "name": "Official Empty Set",
            "questionCount": 0,
            "isDeleted": False,
        }

        self.assertTrue(module.resolve_question_set_is_deleted(qset))

    def test_empty_category_targets_include_only_zero_count_records(self) -> None:
        actual = module.empty_category_targets(
            {
                "folders": [
                    {"folderId": "f0", "questionCount": 0},
                    {"folderId": "f1", "questionCount": 1},
                ],
                "questionSets": [
                    {"questionSetId": "q0", "questionCount": 0},
                    {"questionSetId": "q1", "questionCount": 2},
                ],
            }
        )

        self.assertEqual([item["folderId"] for item in actual["folders"]], ["f0"])
        self.assertEqual(
            [item["questionSetId"] for item in actual["questionSets"]], ["q0"]
        )

    def test_hide_empty_categories_updates_only_visibility_metadata(self) -> None:
        class FakeSnapshot:
            exists = True
            update_time = datetime.now()

            def to_dict(self):
                return {"questionCount": 0, "isDeleted": False}

        class FakeDocRef:
            def __init__(self):
                self.update_value = None

            def get(self, field_paths=None):
                self.field_paths = field_paths
                return FakeSnapshot()

            def update(self, value, option=None):
                self.update_value = value
                self.option = option

        class FakeCollection:
            def __init__(self, ref):
                self.ref = ref

            def document(self, document_id):
                self.document_id = document_id
                return self.ref

        class FakeDb:
            def __init__(self):
                self.ref = FakeDocRef()
                self.collection_name = None

            def collection(self, name):
                self.collection_name = name
                return FakeCollection(self.ref)

        db = FakeDb()
        result = module.hide_empty_categories(
            db,
            {
                "folders": [],
                "questionSets": [
                    {"questionSetId": "q0", "questionCount": 0}
                ],
            },
            datetime(2026, 8, 31, 16, 0, 0),
        )

        self.assertEqual(result, {"updated": 1, "skipped": 0, "errors": 0})
        self.assertEqual(db.collection_name, "questionSets")
        self.assertEqual(
            set(db.ref.update_value), {"isDeleted", "updatedAt", "updatedById"}
        )
        self.assertTrue(db.ref.update_value["isDeleted"])

    def test_hide_empty_categories_stops_when_live_count_is_positive(self) -> None:
        class FakeSnapshot:
            exists = True
            update_time = datetime.now()

            def to_dict(self):
                return {"questionCount": 1, "isDeleted": False}

        class FakeDocRef:
            def get(self, field_paths=None):
                return FakeSnapshot()

        class FakeDb:
            class Collection:
                def document(self, document_id):
                    return FakeDocRef()

            def collection(self, name):
                return self.Collection()

        with self.assertRaisesRegex(RuntimeError, "questionCountが0ではありません"):
            module.hide_empty_categories(
                FakeDb(),
                {
                    "folders": [],
                    "questionSets": [
                        {"questionSetId": "q0", "questionCount": 0}
                    ],
                },
                datetime.now(),
            )

    def test_mecnet_kokushi_license_name_matches_repaso_license_name(self) -> None:
        path = "output/mecnet-kokushi/category/category.json"

        self.assertEqual(module.resolve_license_name(path, None), "医師")

    def test_readable_local_code_maps_to_stable_publication_id(self) -> None:
        path = "output/2nd-class-doboku-sekou/category/category.json"

        self.assertEqual(
            module.infer_qualification_id_from_path(path),
            "2dobokusekou",
        )

    def test_license_name_prefers_category_metadata(self) -> None:
        path = "output/kougai/category/category.json"
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

    def test_folder_scope_fields_default_to_single_qualification(self) -> None:
        doc_data = {}

        module.apply_folder_scope_fields(
            doc_data,
            {},
            license_name="ガス主任技術者",
            qualification_id="chiefgasengineerlicense",
        )

        self.assertEqual(doc_data["licenseNames"], ["ガス主任技術者"])
        self.assertEqual(doc_data["qualificationIds"], ["chiefgasengineerlicense"])

    def test_folder_scope_fields_preserve_multi_qualification_arrays(self) -> None:
        doc_data = {}

        module.apply_folder_scope_fields(
            doc_data,
            {
                "licenseNames": ["大気関係第1種公害防止管理者", "大気関係第1種公害防止管理者"],
                "qualificationIds": ["kougai-taiki-1", "kougai-taiki-2"],
            },
            license_name="公害防止管理者",
            qualification_id="kougai",
        )

        self.assertEqual(doc_data["licenseNames"], ["大気関係第1種公害防止管理者"])
        self.assertEqual(doc_data["qualificationIds"], ["kougai-taiki-1", "kougai-taiki-2"])

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

    def test_fetch_existing_doc_data_uses_field_mask(self) -> None:
        class FakeSnapshot:
            exists = True

            def to_dict(self) -> dict:
                return {"createdAt": "original-created-at"}

        class FakeDocRef:
            def __init__(self) -> None:
                self.field_paths = None

            def get(self, field_paths=None):
                self.field_paths = field_paths
                return FakeSnapshot()

        ref = FakeDocRef()

        self.assertEqual(
            module.fetch_existing_doc_data(ref, module.EXISTING_QUESTION_SET_FIELD_PATHS),
            {"createdAt": "original-created-at"},
        )
        self.assertEqual(ref.field_paths, module.EXISTING_QUESTION_SET_FIELD_PATHS)

    def test_fetch_existing_doc_data_returns_none_for_missing_doc(self) -> None:
        class FakeSnapshot:
            exists = False

            def to_dict(self) -> dict:
                raise AssertionError("to_dict should not be called")

        class FakeDocRef:
            def get(self, field_paths=None):
                self.field_paths = field_paths
                return FakeSnapshot()

        ref = FakeDocRef()

        self.assertIsNone(module.fetch_existing_doc_data(ref, module.EXISTING_FOLDER_FIELD_PATHS))
        self.assertEqual(ref.field_paths, module.EXISTING_FOLDER_FIELD_PATHS)


if __name__ == "__main__":
    unittest.main()
