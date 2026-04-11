from __future__ import annotations

import unittest

from scripts.check.question_set_validation import collect_category_ids


class QuestionSetValidationTests(unittest.TestCase):
    def test_collect_category_ids_includes_folder_ids_by_default(self) -> None:
        category = {
            "folders": [{"folderId": "g1_folder"}],
            "questionSets": [{"questionSetId": "g1_set"}],
        }

        actual = collect_category_ids(category)

        self.assertEqual(actual, {"g1_folder", "g1_set"})

    def test_collect_category_ids_questionset_only_ignores_folder_ids(self) -> None:
        category = {
            "folders": [{"folderId": "g1_folder"}],
            "questionSets": [{"questionSetId": "g1_set"}],
        }

        actual = collect_category_ids(category, questionset_only=True)

        self.assertEqual(actual, {"g1_set"})


if __name__ == "__main__":
    unittest.main()
