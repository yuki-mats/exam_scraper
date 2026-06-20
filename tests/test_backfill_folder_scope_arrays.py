from __future__ import annotations

import unittest

from scripts.upload import backfill_folder_scope_arrays as module


class BackfillFolderScopeArraysTests(unittest.TestCase):
    def test_build_scope_update_defaults_missing_arrays_from_scalar_fields(self) -> None:
        updates = module.build_scope_update(
            {
                "licenseName": "ガス主任技術者",
                "qualificationId": "chiefgasengineerlicense",
            }
        )

        self.assertEqual(updates["licenseNames"], ["ガス主任技術者"])
        self.assertEqual(updates["qualificationIds"], ["chiefgasengineerlicense"])

    def test_build_scope_update_preserves_existing_multi_scope_arrays(self) -> None:
        updates = module.build_scope_update(
            {
                "licenseName": "公害防止管理者",
                "qualificationId": "kougai",
                "licenseNames": [
                    "大気関係第1種公害防止管理者",
                    "大気関係第2種公害防止管理者",
                ],
                "qualificationIds": ["kougai-taiki-1", "kougai-taiki-2"],
            }
        )

        self.assertEqual(updates, {})

    def test_build_scope_update_normalizes_duplicate_arrays(self) -> None:
        updates = module.build_scope_update(
            {
                "licenseName": "資格",
                "qualificationId": "qualification-1",
                "licenseNames": ["資格", "資格", ""],
                "qualificationIds": ["qualification-1", "qualification-1"],
            }
        )

        self.assertEqual(updates["licenseNames"], ["資格"])
        self.assertEqual(updates["qualificationIds"], ["qualification-1"])


if __name__ == "__main__":
    unittest.main()
