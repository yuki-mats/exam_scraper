from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.category import build_nw_category as module


class NwCategoryBuildTests(unittest.TestCase):
    def test_build_category_uses_ipa_syllabus_structure(self) -> None:
        category = module.build_category()

        self.assertEqual(category["metadata"]["qualificationId"], "nw")
        self.assertEqual(len(category["folders"]), 7)
        self.assertEqual(len(category["questionSets"]), 29)
        self.assertEqual(
            category["folders"][0]["name"],
            "01_ネットワークシステムの要件定義",
        )
        self.assertEqual(
            category["questionSets"][0]["name"],
            "1-1 業務システムからの要求分析",
        )
        self.assertEqual(
            category["questionSets"][-1]["name"],
            "7-3 ネットワークシステム運用・保守のアドバイス",
        )
        self.assertTrue(
            any(
                qset["questionSetId"] == "nw_qs05_04_security_incident_response"
                and "情報セキュリティ" in qset["matchingHints"]
                for qset in category["questionSets"]
            )
        )

    def test_write_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "category.json"
            module.write_json(path, module.build_category())

            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
