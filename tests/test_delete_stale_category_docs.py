from __future__ import annotations

import unittest

from scripts.upload.delete_stale_category_docs import compute_delete_candidates


class DeleteStaleCategoryDocsTests(unittest.TestCase):
    def test_compute_delete_candidates_extracts_only_docs_not_in_category(self) -> None:
        category = {
            "folders": [
                {"folderId": "keep-folder"},
                {"folderId": "missing-folder"},
            ],
            "questionSets": [
                {"questionSetId": "keep-set"},
                {"questionSetId": "missing-set"},
            ],
        }
        firestore_folders = [
            {"_id": "keep-folder", "name": "Keep Folder", "questionCount": 10},
            {"_id": "extra-folder", "name": "Extra Folder", "questionCount": 5},
        ]
        firestore_question_sets = [
            {
                "_id": "keep-set",
                "folderId": "keep-folder",
                "name": "Keep Set",
                "questionCount": 3,
            },
            {
                "_id": "extra-set-under-keep",
                "folderId": "keep-folder",
                "name": "Extra Set Under Keep",
                "questionCount": 1,
            },
            {
                "_id": "extra-set-under-extra",
                "folderId": "extra-folder",
                "name": "Extra Set Under Extra",
                "questionCount": 2,
            },
        ]

        actual = compute_delete_candidates(
            category=category,
            firestore_folders=firestore_folders,
            firestore_question_sets=firestore_question_sets,
        )

        self.assertEqual(actual["summary"]["deleteCandidateFolderCount"], 1)
        self.assertEqual(actual["summary"]["deleteCandidateQuestionSetCount"], 2)
        self.assertEqual(actual["summary"]["missingFolderCount"], 1)
        self.assertEqual(actual["summary"]["missingQuestionSetCount"], 1)
        self.assertEqual(
            actual["deleteCandidateFolders"],
            [
                {
                    "folderId": "extra-folder",
                    "name": "Extra Folder",
                    "questionCount": 5,
                    "isDeleted": None,
                }
            ],
        )
        self.assertEqual(
            [item["questionSetId"] for item in actual["deleteCandidateQuestionSets"]],
            ["extra-set-under-extra", "extra-set-under-keep"],
        )
        self.assertEqual(actual["missingFolders"], ["missing-folder"])
        self.assertEqual(actual["missingQuestionSets"], ["missing-set"])


if __name__ == "__main__":
    unittest.main()
