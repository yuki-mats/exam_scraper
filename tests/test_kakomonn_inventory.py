from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.scrape.kakomonn_inventory import (
    build_inventory_rows,
    discover_list_targets_from_html,
    discover_qualifications_from_html,
    infer_year_from_label,
    load_configured_kakomonn_presets,
)


TOP_HTML = """
<html><body>
  <a href="https://itpass.kakomonn.com">ITパスポート</a>
  <a href="https://itpass.kakomonn.com/list">問題一覧</a>
  <a href="https://anma.kakomonn.com/">あん摩マッサージ指圧師</a>
  <a href="https://docs.google.com/forms/example">アンケート</a>
  <a href="https://kakomonn.com/login">ログイン</a>
</body></html>
"""


LIST_HTML = """
<html><body>
  <a href="https://itpass.kakomonn.com/list1/66015?page=1">令和7年度</a>
  <a href="https://itpass.kakomonn.com/list1/66014?page=1">令和6年度</a>
  <a href="https://itpass.kakomonn.com/list1/66010?page=1">令和2年度 秋期</a>
  <a href="https://itpass.kakomonn.com/list1/66010?page=1">1 令和2年度 秋期</a>
  <a href="https://itpass.kakomonn.com/list2/660001?page=1">ストラテジ系</a>
</body></html>
"""


class KakomonnInventoryTests(unittest.TestCase):
    def test_discover_qualifications_ignores_generic_links(self) -> None:
        qualifications = discover_qualifications_from_html(TOP_HTML)

        self.assertEqual([qualification.slug for qualification in qualifications], ["itpass", "anma"])
        self.assertEqual(qualifications[0].name, "ITパスポート")
        self.assertEqual(qualifications[0].list_url, "https://itpass.kakomonn.com/list")

    def test_infer_year_from_japanese_era_labels(self) -> None:
        self.assertEqual(infer_year_from_label("令和7年度"), 2025)
        self.assertEqual(infer_year_from_label("令和元年度 秋期"), 2019)
        self.assertEqual(infer_year_from_label("平成31年度 春期"), 2019)
        self.assertEqual(infer_year_from_label("第34回（2026年）"), 2026)
        self.assertIsNone(infer_year_from_label("準1級"))

    def test_discover_list_targets_dedupes_list1_links(self) -> None:
        targets = discover_list_targets_from_html(
            LIST_HTML,
            list_url="https://itpass.kakomonn.com/list",
            output_group_id_mode="year-when-unique",
        )

        self.assertEqual([target.source_list_group_id for target in targets], ["66015", "66014", "66010"])
        self.assertEqual([target.output_list_group_id for target in targets], ["2025", "2024", "2020"])
        self.assertEqual(targets[0].url, "https://itpass.kakomonn.com/list1/66015?page=1")

    def test_load_configured_kakomonn_presets_by_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "scrape_presets.json"
            config_path.write_text(
                json.dumps(
                    {
                        "itpass": {
                            "scraper_type": "kakomonn",
                            "qualification_name": "ITパスポート",
                            "list_first_page_url_template": "https://itpass.kakomonn.com/list1/{list_group_id}?page=1",
                            "list_group_ids": ["66015"],
                        },
                        "gas": {
                            "scraper_type": "gassyunin",
                            "qualification_name": "ガス",
                            "list_first_page_url_template": "https://example.com/{list_group_id}",
                            "list_group_ids": ["2025"],
                        },
                    }
                ),
                encoding="utf-8",
            )

            presets = load_configured_kakomonn_presets(config_path)

        self.assertEqual(list(presets), ["itpass.kakomonn.com"])
        self.assertEqual(presets["itpass.kakomonn.com"][0].qualification_code, "itpass")

    def test_build_inventory_rows_marks_missing_and_scraped(self) -> None:
        qualifications = discover_qualifications_from_html(TOP_HTML)
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "output"
            source_dir = output_root / "itpass" / "questions_json" / "66015" / "00_source"
            source_dir.mkdir(parents=True)
            (source_dir / "question_66015_1.json").write_text("{}", encoding="utf-8")

            rows = build_inventory_rows(
                qualifications=qualifications,
                configured_by_host={
                    "itpass.kakomonn.com": [
                        load_configured_kakomonn_presets(
                            self._write_config(Path(tmp_dir) / "scrape_presets.json")
                        )["itpass.kakomonn.com"][0]
                    ]
                },
                output_root=output_root,
            )

        self.assertEqual(rows[0]["status"], "configured_scraped")
        self.assertEqual(rows[1]["status"], "missing_preset")

    @staticmethod
    def _write_config(config_path: Path) -> Path:
        config_path.write_text(
            json.dumps(
                {
                    "itpass": {
                        "scraper_type": "kakomonn",
                        "qualification_name": "ITパスポート",
                        "list_first_page_url_template": "https://itpass.kakomonn.com/list1/{list_group_id}?page=1",
                        "list_group_ids": ["66015"],
                    }
                }
            ),
            encoding="utf-8",
        )
        return config_path


if __name__ == "__main__":
    unittest.main()
