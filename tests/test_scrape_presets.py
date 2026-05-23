from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.scrape.qualification_presets import (
    REPO_ROOT,
    build_list_first_page_url,
    has_existing_source_json,
    load_scrape_preset,
    resolve_target_list_group_ids,
)


class ScrapePresetTests(unittest.TestCase):
    def test_load_scrape_preset_for_kaigofukushi(self) -> None:
        preset = load_scrape_preset("kaigofukushi")

        self.assertEqual(preset.qualification_name, "介護福祉士")
        self.assertEqual(preset.scraper_type, "kakomonn")
        self.assertEqual(preset.list_group_ids[0], "2024")
        self.assertEqual(preset.list_group_ids[-1], "2008")

    def test_build_list_first_page_url(self) -> None:
        preset = load_scrape_preset("kaigofukushi")
        url = build_list_first_page_url(preset, "2024")

        self.assertEqual(
            url,
            "https://kaigofukushi.kakomonn.com/list1/2019?page=1",
        )

    def test_load_scrape_preset_for_gas_shunin_otsu(self) -> None:
        preset = load_scrape_preset("gas-shunin-otsu")

        self.assertEqual(preset.qualification_name, "ガス主任技術者乙種")
        self.assertEqual(preset.scraper_type, "gassyunin")
        self.assertEqual(preset.list_group_ids[0], "2025")
        self.assertEqual(preset.list_group_ids[-1], "2017")

    def test_build_list_first_page_url_for_gassyunin(self) -> None:
        preset = load_scrape_preset("gas-shunin-otsu")
        url = build_list_first_page_url(preset, "2025")

        self.assertEqual(
            url,
            "https://gassyunin.com/exam/otsu/otsu_2025/",
        )

    def test_load_scrape_preset_for_sg(self) -> None:
        preset = load_scrape_preset("sg")

        self.assertEqual(preset.qualification_name, "情報セキュリティマネジメント")
        self.assertEqual(preset.scraper_type, "sgsiken")
        self.assertEqual(preset.list_group_ids[0], "202501")
        self.assertEqual(preset.list_group_ids[-1], "201601")

    def test_build_list_first_page_url_for_sg(self) -> None:
        preset = load_scrape_preset("sg")
        url = build_list_first_page_url(preset, "201902")

        self.assertEqual(
            url,
            "https://www.sg-siken.com/kakomon/01_aki/",
        )

    def test_load_scrape_preset_for_mecnet_kokushi(self) -> None:
        preset = load_scrape_preset("mecnet-kokushi")

        self.assertEqual(preset.qualification_name, "医師国家試験（MEC Net.）")
        self.assertEqual(preset.scraper_type, "mecnet")
        self.assertEqual(preset.list_group_ids, ["120A"])

    def test_build_list_first_page_url_for_mecnet_kokushi(self) -> None:
        preset = load_scrape_preset("mecnet-kokushi")
        url = build_list_first_page_url(preset, "120A")

        self.assertEqual(
            url,
            "https://study.mecnet.jp/exercises/exercise_list/1",
        )

    def test_resolve_target_list_group_ids_rejects_unknown_group(self) -> None:
        preset = load_scrape_preset("kaigofukushi")

        with self.assertRaises(ValueError):
            resolve_target_list_group_ids(preset, ["9999"])

    def test_has_existing_source_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            source_dir = (
                repo_root
                / "output"
                / "kaigofukushi"
                / "questions_json"
                / "2024"
                / "00_source"
            )
            source_dir.mkdir(parents=True)

            self.assertFalse(
                has_existing_source_json(
                    repo_root,
                    "kaigofukushi",
                    "2024",
                    output_root=repo_root / "output",
                )
            )

            sample_file = source_dir / "question_2024_1.json"
            sample_file.write_text("{}", encoding="utf-8")

            self.assertTrue(
                has_existing_source_json(
                    repo_root,
                    "kaigofukushi",
                    "2024",
                    output_root=repo_root / "output",
                )
            )


if __name__ == "__main__":
    unittest.main()
