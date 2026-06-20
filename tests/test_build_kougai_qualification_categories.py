from __future__ import annotations

import unittest

from scripts.category import build_kougai_qualification_categories as module


class BuildKougaiQualificationCategoriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical = module.load_json(module.DEFAULT_CANONICAL_CATEGORY_JSON)
        cls.mapping = module.load_json(module.DEFAULT_MAPPING_JSON)
        cls.categories = module.build_all_categories(cls.canonical, cls.mapping)

    def test_mapping_has_13_qualification_divisions(self) -> None:
        self.assertEqual(len(self.mapping["qualifications"]), 13)
        self.assertEqual(len(self.categories), 13)

    def test_air_qualification_subject_sets_match_exam_divisions(self) -> None:
        expected = {
            "kougai-taiki-1": [
                "公害総論",
                "大気概論",
                "大気特論",
                "ばいじん・粉じん特論",
                "大気有害物質特論",
                "大規模大気特論",
            ],
            "kougai-taiki-2": [
                "公害総論",
                "大気概論",
                "大気特論",
                "ばいじん・粉じん特論",
                "大気有害物質特論",
            ],
            "kougai-taiki-3": [
                "公害総論",
                "大気概論",
                "大気特論",
                "ばいじん・粉じん特論",
                "大規模大気特論",
            ],
            "kougai-taiki-4": [
                "公害総論",
                "大気概論",
                "大気特論",
                "ばいじん・粉じん特論",
            ],
        }

        for qualification_id, folder_names in expected.items():
            with self.subTest(qualification_id=qualification_id):
                self.assertEqual(
                    [folder["name"] for folder in self.categories[qualification_id]["folders"]],
                    folder_names,
                )

    def test_special_divisions_use_their_dedicated_subjects(self) -> None:
        expected = {
            "kougai-tokutei-funjin": ["公害総論", "大気概論", "ばいじん・粉じん特論"],
            "kougai-ippan-funjin": ["公害総論", "大気概論", "ばいじん・一般粉じん特論"],
            "kougai-soon-shindo": ["公害総論", "騒音・振動概論", "騒音・振動特論"],
            "kougai-dioxin": ["公害総論", "ダイオキシン類概論", "ダイオキシン類特論"],
            "kougai-chief": ["公害総論", "大気・水質概論", "大気関係技術特論", "水質関係技術特論"],
        }

        for qualification_id, folder_names in expected.items():
            with self.subTest(qualification_id=qualification_id):
                self.assertEqual(
                    [folder["name"] for folder in self.categories[qualification_id]["folders"]],
                    folder_names,
                )

    def test_materialized_category_keeps_canonical_references(self) -> None:
        category = self.categories["kougai-taiki-1"]
        folder = category["folders"][0]
        qset = category["questionSets"][0]

        self.assertEqual(folder["folderId"], "kougai-taiki-1_f01_kougai_soron")
        self.assertEqual(folder["canonicalFolderId"], "kougai_f01_kougai_soron")
        self.assertEqual(folder["sourceSharedFolderId"], "kougai_f01_kougai_soron")
        self.assertEqual(qset["questionSetId"], "kougai-taiki-1_qs01_01")
        self.assertEqual(qset["folderId"], "kougai-taiki-1_f01_kougai_soron")
        self.assertEqual(qset["canonicalQuestionSetId"], "kougai_qs01_01")
        self.assertEqual(qset["sourceSharedQuestionSetId"], "kougai_qs01_01")

    def test_exam_question_counts_match_sum_of_subject_counts(self) -> None:
        self.assertEqual(self.categories["kougai-taiki-1"]["metadata"]["examQuestionCount"], 75)
        self.assertEqual(self.categories["kougai-suishitsu-4"]["metadata"]["examQuestionCount"], 50)
        self.assertEqual(self.categories["kougai-chief"]["metadata"]["examQuestionCount"], 65)


if __name__ == "__main__":
    unittest.main()
