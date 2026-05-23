from __future__ import annotations

import functools
import os
import unittest

import requests

from scrape_gassyunin import count_questions_by_subject, parse_exam_page_html
from scripts.scrape.common import extract_image_urls_from_element
from bs4 import BeautifulSoup


RUN_LIVE_TESTS = os.environ.get("RUN_LIVE_TESTS") == "1"


@functools.lru_cache(maxsize=None)
def fetch_question_dicts_for_year(year: str) -> list[dict]:
    response = requests.get(
        f"https://gassyunin.com/exam/otsu/otsu_{year}/",
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    return parse_exam_page_html(
        response.text,
        f"https://gassyunin.com/exam/otsu/otsu_{year}/",
        download_images=False,
    )


def find_question(question_dicts: list[dict], *, category: str, question_label: str) -> dict:
    for question_dict in question_dicts:
        if question_dict["category"] == category and question_dict["questionLabel"] == question_label:
            return question_dict
    raise AssertionError(f"question not found: category={category}, question_label={question_label}")


class ScrapeGassyuninTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("QUESTION_ID_SECRET_KEY", "test-secret")

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_parse_live_2025_question_1(self) -> None:
        question_dicts = fetch_question_dicts_for_year("2025")
        first_question = find_question(question_dicts, category="法令", question_label="問1")

        self.assertEqual(
            first_question["questionBodyText"],
            "法令で規定されている用語の定義等に関する次の記述のうち、正しいものはどれか。",
        )
        self.assertEqual(first_question["category"], "法令")
        self.assertEqual(first_question["questionLabel"], "問1")
        self.assertEqual(first_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])
        self.assertEqual(first_question["answer_result_inferred_correct_choice_numbers"], [2])
        self.assertEqual(first_question["questionChoiceMarkers"], ["1", "2", "3", "4", "5"])
        self.assertEqual(first_question["judgeChoiceMarkers"], ["1", "2", "3", "4", "5"])
        self.assertEqual(first_question["choiceMarkerSource"], "judge")
        self.assertEqual(first_question["markerAlignmentMode"], "judge_matches_question_markers")
        self.assertFalse(first_question["markerMismatchDetected"])
        self.assertFalse(first_question["answerResultNumbersRemapped"])
        self.assertTrue(first_question["choiceTextList"][1].startswith("「ガス工作物」とは"))
        self.assertTrue(first_question["choiceTextList"][3].startswith("「液化ガス」とは"))
        self.assertIn("[wrong]70未満[/wrong]", first_question["choiceTextMarkedList"][0])
        self.assertIn("[wrong]0.2MPa以上[/wrong]", first_question["choiceTextMarkedList"][3])
        self.assertEqual(
            first_question["explanation_choice_snippets"][0],
            ["正しくは: 70以上\n📌 関連: 法2条(定義)1項、政令1条"],
        )
        self.assertEqual(
            first_question["explanation_choice_snippets"][3],
            ["正しくは: 1MPa以上(液化ガスは高圧ガス保安法準拠)\n📌 関連: 高圧ガス保安法2条3号(液化ガスの定義)"],
        )
        self.assertEqual(
            first_question["explanation_choice_snippets"][1],
            ["📌 関連: 法2条(定義)13項"],
        )
        self.assertIn("熱量の基準は22.4Lでなく1m³。", first_question["explanation_common_prefix"][-1])
        self.assertTrue(
            any("2017年の自由化でガス事業は4区分に再編。" in item for item in first_question["explanation_common_prefix"])
        )

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_parse_live_2019_question_6_aligns_kana_markers(self) -> None:
        question_dicts = fetch_question_dicts_for_year("2019")
        question = find_question(question_dicts, category="法令", question_label="問6")

        self.assertEqual(question["answer_result_inferred_correct_choice_numbers"], [4])
        self.assertEqual(question["correctChoiceText"], ["正しい", "正しい", "間違い", "正しい", "正しい"])
        self.assertEqual(question["questionChoiceMarkers"], ["イ", "ロ", "ハ", "ニ", "ホ"])
        self.assertEqual(question["judgeChoiceMarkers"], ["イ", "ロ", "ハ", "ニ", "ホ"])
        self.assertEqual(question["choiceMarkerSource"], "judge")
        self.assertEqual(question["markerAlignmentMode"], "judge_matches_question_markers")
        self.assertFalse(question["markerMismatchDetected"])
        self.assertTrue(question["choiceTextList"][0].startswith("最高使用圧力が低圧のガスホルダー"))
        self.assertTrue(question["choiceTextList"][2].startswith("附帯設備であって製造設備に属する配管"))
        self.assertEqual(
            question["explanation_choice_snippets"][2],
            ["正しくは: 不活性のガスを通ずるものは内面1MPa以上受ける部分\n📌 関連: 技省令15条1項三号ニ"],
        )

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_parse_live_2024_question_4_uses_verdict_polarity(self) -> None:
        question_dicts = fetch_question_dicts_for_year("2024")
        question = find_question(question_dicts, category="法令", question_label="問4")

        self.assertEqual(question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "正しい"])
        self.assertEqual(question["answer_result_inferred_correct_choice_numbers"], [3])
        self.assertEqual(question["questionChoiceMarkers"], ["イ", "ロ", "ハ", "ニ", "ホ"])
        self.assertEqual(question["judgeChoiceMarkers"], ["イ", "ロ", "ハ", "ニ", "ホ"])
        self.assertEqual(question["choiceMarkerSource"], "judge")
        self.assertEqual(question["markerAlignmentMode"], "judge_matches_question_markers")
        self.assertFalse(question["markerMismatchDetected"])
        self.assertIn("規定されていないものの組合せ", question["questionBodyText"])

    def test_extract_image_urls_uses_data_src(self) -> None:
        soup = BeautifulSoup(
            """
            <div>
              <img src="data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==" data-src="/img/otsu₂023_gijutsu₁0₁.png" />
              <img src="/img/sample2.png" />
            </div>
            """,
            "html.parser",
        )

        urls = extract_image_urls_from_element(soup.div, "https://gassyunin.com/exam/otsu/otsu_2023/")
        self.assertEqual(
            urls,
            [
                "https://gassyunin.com/img/otsu_2023_gijutsu_10_1.png",
                "https://gassyunin.com/img/sample2.png",
            ],
        )

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_live_2025_subject_question_counts(self) -> None:
        response = requests.get(
            "https://gassyunin.com/exam/otsu/otsu_2025/",
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()

        counts = count_questions_by_subject(response.text)
        self.assertEqual(
            counts,
            {
                "法令": 16,
                "基礎理論": 15,
                "製造": 9,
                "供給": 9,
                "消費機器": 9,
            },
        )
        self.assertEqual(sum(counts.values()), 58)


if __name__ == "__main__":
    unittest.main()
