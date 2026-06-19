from __future__ import annotations

import unittest

from scripts.export import export_firestore_gas_shunin_kou_snapshot as module


class ExportFirestoreGasShuninKouSnapshotTest(unittest.TestCase):
    def test_firestore_value_to_python_decodes_supported_rest_value_types(self) -> None:
        encoded = {
            "mapValue": {
                "fields": {
                    "string": {"stringValue": "text"},
                    "integer": {"integerValue": "42"},
                    "double": {"doubleValue": 1.5},
                    "boolean": {"booleanValue": True},
                    "timestamp": {"timestampValue": "2026-06-20T00:00:00Z"},
                    "null": {"nullValue": None},
                    "array": {
                        "arrayValue": {
                            "values": [
                                {"stringValue": "a"},
                                {"integerValue": "7"},
                            ]
                        }
                    },
                    "nested": {
                        "mapValue": {
                            "fields": {
                                "reference": {
                                    "referenceValue": (
                                        "projects/undefined/databases/(default)"
                                        "/documents/folders/folder-1"
                                    )
                                }
                            }
                        }
                    },
                }
            }
        }

        self.assertEqual(
            module.firestore_value_to_python(encoded),
            {
                "string": "text",
                "integer": 42,
                "double": 1.5,
                "boolean": True,
                "timestamp": "2026-06-20T00:00:00Z",
                "null": None,
                "array": ["a", 7],
                "nested": {
                    "reference": (
                        "projects/undefined/databases/(default)"
                        "/documents/folders/folder-1"
                    )
                },
            },
        )

    def test_raw_document_record_preserves_doc_id_raw_fields_and_decoded_reference(self) -> None:
        document = {
            "name": "projects/sample/databases/(default)/documents/questionSets/qset-1",
            "createTime": "2026-06-20T00:00:00Z",
            "updateTime": "2026-06-20T01:00:00Z",
            "fields": {
                "folderRef": {
                    "referenceValue": (
                        "projects/undefined/databases/(default)"
                        "/documents/folders/folder-1"
                    )
                },
                "questionCount": {"integerValue": "20"},
            },
        }

        record = module.raw_document_record(document)

        self.assertEqual(record["_id"], "qset-1")
        self.assertIs(record["fields"], document["fields"])
        self.assertEqual(
            record["decoded"]["folderRef"],
            "projects/undefined/databases/(default)/documents/folders/folder-1",
        )
        self.assertEqual(record["decoded"]["questionCount"], 20)

    def test_reconstruct_question_uses_doc_id_and_preserves_original_question_id(self) -> None:
        decoded_question = {
            "_id": "firestore-doc-id",
            "questionId": "old-question-id",
            "originalQuestionId": "source-question-id",
            "questionSetId": "qset-1",
            "questionText": "問題文",
            "questionType": "single_choice",
            "qualificationId": "chiefgasengineerlicense",
            "isOfficial": True,
            "isDeleted": False,
            "isChoiceOnly": False,
            "isGroupable": False,
            "questionTags": [],
            "createdById": "system",
            "updatedById": "system",
            "createdAt": "2026-06-20T00:00:00Z",
            "updatedAt": "2026-06-20T01:00:00Z",
        }

        reconstructed = module.reconstruct_question(decoded_question)

        self.assertNotIn("_id", reconstructed)
        self.assertEqual(reconstructed["questionId"], "firestore-doc-id")
        self.assertEqual(reconstructed["originalQuestionId"], "source-question-id")

    def test_reconstruct_category_recounts_active_display_questions(self) -> None:
        category = module.reconstruct_category(
            decoded_folders=[
                {
                    "_id": "chiefgasengineerlicense-A-10",
                    "name": "ガス技術（甲種）",
                    "questionCount": 99,
                    "licenseName": "ガス主任技術者",
                    "qualificationId": "chiefgasengineerlicense",
                    "isPublic": True,
                    "isOfficial": True,
                }
            ],
            decoded_question_sets=[
                {
                    "_id": "chiefgasengineerlicense-A-10-010",
                    "folderId": "chiefgasengineerlicense-A-10",
                    "name": "ガス技術（甲種）",
                    "questionCount": 99,
                    "qualificationId": "chiefgasengineerlicense",
                    "isOfficial": True,
                }
            ],
            decoded_questions=[
                {
                    "_id": "q1",
                    "questionSetId": "chiefgasengineerlicense-A-10-010",
                    "isDeleted": False,
                    "isChoiceOnly": False,
                },
                {
                    "_id": "q2",
                    "questionSetId": "chiefgasengineerlicense-A-10-010",
                    "isDeleted": False,
                    "isChoiceOnly": True,
                },
                {
                    "_id": "q3",
                    "questionSetId": "chiefgasengineerlicense-A-10-010",
                    "isDeleted": True,
                    "isChoiceOnly": False,
                },
            ],
            generated_at="2026-06-20T00:00:00Z",
            license_name="ガス主任技術者",
            folder_prefix="chiefgasengineerlicense-A-",
        )

        self.assertEqual(category["folders"][0]["questionCount"], 1)
        self.assertEqual(category["questionSets"][0]["questionCount"], 1)


if __name__ == "__main__":
    unittest.main()
