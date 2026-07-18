from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.scrape.qualification_presets import (
    REPO_ROOT,
    build_list_first_page_url,
    has_existing_source_json,
    load_qualification_catalog,
    load_scrape_preset,
    resolve_target_list_group_ids,
)
from scripts.scrape.run_qualification_scrape import source_filename_suffix_for_kougai_url


class ScrapePresetTests(unittest.TestCase):
    def test_load_scrape_preset_for_aws_cloud_practitioner_pingt(self) -> None:
        preset = load_scrape_preset("aws-cloud-practitioner")

        self.assertEqual(preset.qualification_code, "aws-cloud-practitioner")
        self.assertEqual(preset.qualification_name, "AWS Certified Cloud Practitioner (CLF-C02)")
        self.assertEqual(preset.scraper_type, "pingt")
        self.assertEqual(preset.list_group_ids, ["ping-t-aws-clf-c02"])
        self.assertEqual(
            build_list_first_page_url(preset, "ping-t-aws-clf-c02"),
            "https://mondai.ping-t.com/question_subjects/76/questions",
        )

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

    def test_load_scrape_preset_for_gas_shunin_kou(self) -> None:
        preset = load_scrape_preset("gas-shunin-kou")

        self.assertEqual(preset.qualification_name, "ガス主任技術者甲種")
        self.assertEqual(preset.scraper_type, "gassyunin")
        self.assertEqual(preset.list_group_ids[0], "2025")
        self.assertEqual(preset.list_group_ids[-1], "2017")

    def test_build_list_first_page_url_for_gassyunin_kou(self) -> None:
        preset = load_scrape_preset("gas-shunin-kou")
        url = build_list_first_page_url(preset, "2025")

        self.assertEqual(
            url,
            "https://gassyunin.com/exam/kou/kou_2025/",
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

    def test_load_scrape_preset_for_nw(self) -> None:
        preset = load_scrape_preset("nw")

        self.assertEqual(preset.qualification_name, "ネットワークスペシャリスト")
        self.assertEqual(preset.scraper_type, "sgsiken")
        self.assertEqual(preset.list_group_ids, ["202501"])

    def test_build_list_first_page_url_for_nw(self) -> None:
        preset = load_scrape_preset("nw")
        url = build_list_first_page_url(preset, "202501")

        self.assertEqual(
            url,
            "https://www.nw-siken.com/s/kakomon/07_haru/",
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

    def test_load_scrape_preset_for_anma(self) -> None:
        preset = load_scrape_preset("anma")

        self.assertEqual(preset.qualification_name, "あん摩マッサージ指圧師")
        self.assertEqual(preset.scraper_type, "kakomonn")
        self.assertEqual(preset.list_group_ids[0], "2026")
        self.assertEqual(preset.list_group_ids[-1], "2016")

    def test_build_list_first_page_url_for_anma(self) -> None:
        preset = load_scrape_preset("anma")
        url = build_list_first_page_url(preset, "2026")

        self.assertEqual(
            url,
            "https://anma.kakomonn.com/list1/81011?page=1",
        )

    def test_load_scrape_preset_for_anma_kurohon_outputs_to_anma(self) -> None:
        preset = load_scrape_preset("anma_kurohon")

        self.assertEqual(preset.qualification_code, "anma")
        self.assertEqual(preset.qualification_name, "あん摩マッサージ指圧師")
        self.assertEqual(preset.scraper_type, "kurohon")
        self.assertEqual(preset.list_group_ids[0], "2015")
        self.assertEqual(preset.list_group_ids[-1], "1993")

    def test_build_list_first_page_url_for_anma_kurohon(self) -> None:
        preset = load_scrape_preset("anma_kurohon")
        url = build_list_first_page_url(preset, "2015")

        self.assertEqual(
            url,
            "https://kurohon.jp/gakusei/exams_am/am_23/",
        )

    def test_load_scrape_preset_for_tsukanshi(self) -> None:
        preset = load_scrape_preset("tsukanshi")

        self.assertEqual(preset.qualification_name, "通関士")
        self.assertEqual(preset.scraper_type, "kakomonn")
        self.assertEqual(preset.list_group_ids[0], "2025")
        self.assertEqual(preset.list_group_ids[-1], "2015")

    def test_build_list_first_page_url_for_tsukanshi(self) -> None:
        preset = load_scrape_preset("tsukanshi")
        url = build_list_first_page_url(preset, "2025")

        self.assertEqual(
            url,
            "https://tsukanshi.kakomonn.com/list1/68011?page=1",
        )

    def test_load_scrape_preset_for_1st_class_kenchikushi(self) -> None:
        preset = load_scrape_preset("1st-class-kenchikushi")

        self.assertEqual(preset.qualification_name, "一級建築士")
        self.assertEqual(preset.scraper_type, "kakomonn")
        self.assertEqual(preset.list_group_ids[0], "2025")
        self.assertEqual(preset.list_group_ids[-1], "2015")

    def test_build_list_first_page_url_for_1st_class_kenchikushi(self) -> None:
        preset = load_scrape_preset("1st-class-kenchikushi")
        url = build_list_first_page_url(preset, "2025")

        self.assertEqual(
            url,
            "https://1kenchikushi.kakomonn.com/list1/69011?page=1",
        )

    def test_load_scrape_preset_for_second_class_doboku_sekou(self) -> None:
        preset = load_scrape_preset("2nd-class-doboku-sekou")

        self.assertEqual(preset.qualification_name, "2級土木施工管理技士")
        self.assertEqual(preset.publication_qualification_id, "2dobokusekou")
        self.assertEqual(preset.scraper_type, "kakomonn")
        self.assertEqual(preset.list_group_ids[0], "202502")
        self.assertEqual(preset.list_group_ids[-1], "201701")

    def test_build_list_first_page_url_for_second_class_doboku_sekou(self) -> None:
        preset = load_scrape_preset("2nd-class-doboku-sekou")
        url = build_list_first_page_url(preset, "202502")

        self.assertEqual(
            url,
            "https://2dobokusekou.kakomonn.com/list1/84016?page=1",
        )

    def test_load_scrape_preset_for_second_class_kenchiku_sekou(self) -> None:
        preset = load_scrape_preset("2nd-class-kenchiku-sekou")

        self.assertEqual(preset.qualification_name, "2級建築施工管理技士")
        self.assertEqual(preset.publication_qualification_id, "2kenchikusekou")
        self.assertEqual(preset.scraper_type, "kakomonn")
        self.assertEqual(preset.list_group_ids[0], "202502")
        self.assertEqual(preset.list_group_ids[-1], "201702")

    def test_build_list_first_page_url_for_second_class_kenchiku_sekou(self) -> None:
        preset = load_scrape_preset("2nd-class-kenchiku-sekou")
        url = build_list_first_page_url(preset, "202502")

        self.assertEqual(
            url,
            "https://kenchikusekou2.kakomonn.com/list1/74016?page=1",
        )

    def test_qualification_catalog_separates_local_and_publication_codes(self) -> None:
        catalog = load_qualification_catalog()

        self.assertEqual(
            catalog["2nd-class-doboku-sekou"],
            {
                "displayName": "2級土木施工管理技士",
                "publicationId": "2dobokusekou",
            },
        )
        self.assertEqual(
            catalog["2nd-class-kenchiku-sekou"],
            {
                "displayName": "2級建築施工管理技士",
                "publicationId": "2kenchikusekou",
            },
        )

    def test_load_scrape_preset_for_judoseifukushi(self) -> None:
        preset = load_scrape_preset("judoseifukushi")

        self.assertEqual(preset.qualification_name, "柔道整復師")
        self.assertEqual(preset.scraper_type, "kurohon")
        self.assertEqual(preset.list_group_ids[0], "2026")
        self.assertEqual(preset.list_group_ids[-1], "1993")

    def test_build_list_first_page_url_for_judoseifukushi(self) -> None:
        preset = load_scrape_preset("judoseifukushi")
        url = build_list_first_page_url(preset, "2026")

        self.assertEqual(
            url,
            "https://kurohon.jp/gakusei/exams_js/js_34/",
        )

    def test_load_scrape_preset_for_shinkyu(self) -> None:
        preset = load_scrape_preset("shinkyu")

        self.assertEqual(preset.qualification_name, "鍼灸師")
        self.assertEqual(preset.scraper_type, "kurohon")
        self.assertEqual(preset.list_group_ids[0], "2026")
        self.assertEqual(preset.list_group_ids[-1], "1993")

    def test_build_list_first_page_url_for_shinkyu(self) -> None:
        preset = load_scrape_preset("shinkyu")
        url = build_list_first_page_url(preset, "2026")

        self.assertEqual(
            url,
            "https://kurohon.jp/gakusei/exams_hq/hq_34/",
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

    def test_has_existing_source_json_can_filter_by_source_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_root = Path(tmp_dir)
            source_dir = (
                repo_root
                / "output"
                / "kougai"
                / "questions_json"
                / "2022"
                / "00_source"
            )
            source_dir.mkdir(parents=True)
            (source_dir / "question_2022_yakutik_1.json").write_text("{}", encoding="utf-8")

            self.assertTrue(
                has_existing_source_json(
                    repo_root,
                    "kougai",
                    "2022",
                    output_root=repo_root / "output",
                    filename_glob="question_2022_yakutik_*.json",
                )
            )
            self.assertFalse(
                has_existing_source_json(
                    repo_root,
                    "kougai",
                    "2022",
                    output_root=repo_root / "output",
                    filename_glob="question_2022_qualification_text_*.json",
                )
            )

    def test_kougai_runner_source_filename_suffixes(self) -> None:
        self.assertEqual(
            source_filename_suffix_for_kougai_url("https://yaku-tik.com/kougai/category/kako/kako-r7/"),
            "yakutik",
        )
        self.assertEqual(
            source_filename_suffix_for_kougai_url("https://qualification-text.com/r04questions.php"),
            "qualification_text",
        )
        self.assertEqual(
            source_filename_suffix_for_kougai_url("https://zoron.hatenablog.com/entry/R6"),
            "zoron",
        )


if __name__ == "__main__":
    unittest.main()
