from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.category import build_mecnet_kokushi_category as module


class MecnetKokushiCategoryBuildTests(unittest.TestCase):
    def test_parse_blueprint_counts_and_visual_fixups(self) -> None:
        outline = module.parse_blueprint(module.DEFAULT_BLUEPRINT_TEXT)

        self.assertEqual(len(outline["required"]), 18)
        self.assertEqual(len(outline["general"]), 9)
        self.assertEqual(sum(len(chapter["items"]) for chapter in outline["general"]), 82)
        self.assertEqual(len(outline["specific"]), 13)
        self.assertEqual(sum(len(chapter["items"]) for chapter in outline["specific"]), 100)
        self.assertEqual(outline["specific"][-1]["roman"], "XIII")
        self.assertEqual(outline["specific"][-1]["name"], "生活環境因子・職業性因子による疾患")
        self.assertNotIn("", str(outline))

    def test_build_category_uses_blueprint_names_without_question_mapping(self) -> None:
        outline = module.parse_blueprint(module.DEFAULT_BLUEPRINT_TEXT)
        category = module.build_category(outline)

        self.assertEqual(category["metadata"]["qualificationId"], "mecnet-kokushi")
        self.assertEqual(category["metadata"]["folderCount"], 23)
        self.assertEqual(category["metadata"]["questionSetCount"], 200)
        self.assertEqual(category["folders"][0]["name"], "必修の基本的事項")
        self.assertEqual(category["questionSets"][0]["name"], "医師のプロフェッショナリズム")
        self.assertFalse(category["questionSets"][0]["isDeleted"])
        self.assertEqual(category["questionSets"][-1]["name"], "物理的原因・生活環境因子による障害")

    def test_main_writes_outline_and_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outline_path = Path(tmp) / "outline.json"
            category_path = Path(tmp) / "category.json"
            outline = module.parse_blueprint(module.DEFAULT_BLUEPRINT_TEXT)
            category = module.build_category(outline)
            module.write_json(outline_path, outline)
            module.write_json(category_path, category)

            self.assertTrue(outline_path.exists())
            self.assertTrue(category_path.exists())


if __name__ == "__main__":
    unittest.main()
