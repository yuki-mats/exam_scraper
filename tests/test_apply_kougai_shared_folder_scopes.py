from __future__ import annotations

import unittest

from scripts.category import apply_kougai_shared_folder_scopes as module


class ApplyKougaiSharedFolderScopesTest(unittest.TestCase):
    def test_applies_qualification_arrays_only_to_folders(self) -> None:
        category = {
            "metadata": {"notes": []},
            "folders": [
                {"folderId": "kougai_f01_kougai_soron", "name": "公害総論"},
                {"folderId": "kougai_f02_taiki_gairon", "name": "大気概論"},
            ],
            "questionSets": [
                {
                    "questionSetId": "kougai_qs01_01",
                    "folderId": "kougai_f01_kougai_soron",
                }
            ],
        }
        mapping = {
            "qualifications": [
                {
                    "qualificationId": "kougai-taiki-1",
                    "licenseName": "大気関係第1種公害防止管理者",
                    "canonicalFolderIds": [
                        "kougai_f01_kougai_soron",
                        "kougai_f02_taiki_gairon",
                    ],
                },
                {
                    "qualificationId": "kougai-suishitsu-1",
                    "licenseName": "水質関係第1種公害防止管理者",
                    "canonicalFolderIds": ["kougai_f01_kougai_soron"],
                },
            ]
        }

        result = module.apply_folder_scopes(category, mapping)

        self.assertEqual(
            result["folders"][0]["qualificationIds"],
            ["kougai-taiki-1", "kougai-suishitsu-1"],
        )
        self.assertEqual(
            result["folders"][0]["licenseNames"],
            ["大気関係第1種公害防止管理者", "水質関係第1種公害防止管理者"],
        )
        self.assertEqual(result["folders"][1]["qualificationIds"], ["kougai-taiki-1"])
        self.assertNotIn("qualificationIds", result["questionSets"][0])
        self.assertNotIn("licenseNames", result["questionSets"][0])


if __name__ == "__main__":
    unittest.main()
