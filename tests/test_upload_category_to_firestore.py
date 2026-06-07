from __future__ import annotations

import unittest

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

    def test_mecnet_kokushi_license_name_is_official_exam_name(self) -> None:
        path = "output/mecnet-kokushi/category/category.json"

        self.assertEqual(module.resolve_license_name(path, None), "医師国家試験")


if __name__ == "__main__":
    unittest.main()
