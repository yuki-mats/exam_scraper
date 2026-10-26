from __future__ import annotations

import functools
import os
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import scrape_gassyunin as scraper

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

    def test_parse_numbered_choice_box_without_classifying_question_type(self) -> None:
        html = """
        <h1>2018年（平成30年）甲種 過去問題</h1>
        <div class="tab-content-kiso">
          <h2>問1</h2>
          <p>計算結果として、最も近い値はどれか。</p>
          <ol class="choice-list">
            <li><strong>(1)</strong> 1060</li>
            <li><strong>(2)</strong> 1080</li>
            <li><strong>(3)</strong> 1100</li>
          </ol>
          <details>
            <h3>正解: (2)</h3>
            <h3>計算方法</h3>
          </details>
        </div>
        """

        questions = parse_exam_page_html(
            html,
            "https://gassyunin.com/exam/kou/kou_2018/",
            download_images=False,
        )

        self.assertEqual(len(questions), 1)
        question = questions[0]
        self.assertNotIn("questionType", question)
        self.assertEqual(question["choiceTextList"], ["1060", "1080", "1100"])
        self.assertEqual(question["choiceTextMarkedList"], ["1060", "1080", "1100"])
        self.assertEqual(question["correctChoiceText"], ["不正解", "正解", "不正解"])
        self.assertEqual(question["explanation_choice_correctness"], ["不正解", "正解", "不正解"])
        self.assertEqual(question["answer_result_inferred_correct_choice_numbers"], [2])

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_parse_live_kou_2018_numbered_choice(self) -> None:
        response = requests.get(
            "https://gassyunin.com/exam/kou/kou_2018/",
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        questions = parse_exam_page_html(
            response.text,
            "https://gassyunin.com/exam/kou/kou_2018/",
            download_images=False,
        )
        question = find_question(questions, category="基礎理論", question_label="問1")

        self.assertNotIn("questionType", question)
        self.assertEqual(question["choiceTextList"], ["1060", "1080", "1100", "1120", "1140"])
        self.assertEqual(question["correctChoiceText"], ["正解", "不正解", "不正解", "不正解", "不正解"])
        self.assertEqual(question["answer_result_inferred_correct_choice_numbers"], [1])

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


class HeiAcquisitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = patch.dict(os.environ, {"QUESTION_ID_SECRET_KEY": "test-secret"})
        self.secret.start()
        self.addCleanup(self.secret.stop)
        self.html = (Path(__file__).parent / "fixtures/gassyunin/hei_2025.html").read_text()
        self.url = "https://gassyunin.com/exam/hei/hei_2025/"

    def parse(self, html=None):
        return parse_exam_page_html(html or self.html, self.url)

    def test_full_hei_year_preserves_nine_table_questions(self):
        questions = self.parse()
        scraper.validate_complete_exam(questions, self.html)
        expected = {"法令": [2, 6, 7, 9, 13], "製造": [3], "供給": [10, 12, 16]}
        for category, numbers in expected.items():
            for number in numbers:
                question = find_question(questions, category=category, question_label=f"問{number}")
                self.assertEqual(len(question["choiceTextList"]), 5)
                self.assertIn("(イ):", question["choiceTextList"][0])
        q = find_question(questions, category="法令", question_label="問2")
        self.assertEqual(q["choiceTextList"][2], "(イ): 供給計画、(ロ): 開始前、(ハ): に届け出、(ニ): 確保、(ホ): 命ずる")
        self.assertEqual(q["answer_result_inferred_correct_choice_numbers"], [3])
        self.assertEqual(q["correctChoiceText"], ["不正解", "不正解", "正解", "不正解", "不正解"])

    def test_hei_public_ids_are_namespaced_without_changing_legacy_ids(self):
        hei = self.parse()[0]
        otsu = parse_exam_page_html(self.html, self.url.replace("hei", "otsu"))[0]
        self.assertEqual(hei["source_question_id"], otsu["source_question_id"])
        self.assertNotEqual(hei["public_question_id"], otsu["public_question_id"])
        self.assertEqual(otsu["public_question_id"], scraper.make_public_question_id(otsu["source_question_id"]))
        self.assertEqual(hei["public_question_id"], scraper.make_public_question_id("gas-shunin-hei:" + hei["source_question_id"]))

    def test_table_rejects_duplicate_missing_or_empty_rows(self):
        soup = BeautifulSoup(self.html, "html.parser")
        table = soup.find("table")
        for modification in ("duplicate", "missing", "empty"):
            copy = BeautifulSoup(str(table), "html.parser").table
            rows = copy.find_all("tr")
            if modification == "duplicate":
                rows[2].find("th").string = "(1)"
            elif modification == "missing":
                rows[-1].decompose()
            else:
                rows[1].find("td").clear()
            with self.subTest(modification=modification), self.assertRaises(ValueError):
                scraper.extract_table_choice_texts([copy])

    def test_table_preserves_superscript_and_subscript(self):
        table = '<table><tr><th></th><th>(イ)</th></tr>' + ''.join(
            f'<tr><th>({n})</th><td>CO<sub>2</sub> {n}m<sup>3</sup></td></tr>' for n in range(1, 6)
        ) + '</table>'
        choices = scraper.extract_table_choice_texts([BeautifulSoup(table, "html.parser").table])
        self.assertEqual(choices[0], "(イ): CO₂ 1m³")

    def test_complete_exam_rejects_missing_duplicate_and_invalid_answer(self):
        for problem in ("missing", "duplicate", "empty", "array", "answer"):
            questions = self.parse()
            if problem == "missing":
                questions.pop()
            elif problem == "duplicate":
                questions[1]["source_question_id"] = questions[0]["source_question_id"]
            elif problem == "empty":
                questions[0]["choiceTextList"] = []
            elif problem == "array":
                questions[0]["correctChoiceText"] = []
            else:
                questions[0]["answer_result_inferred_correct_choice_numbers"] = [6]
            with self.subTest(problem=problem), self.assertRaises(ValueError):
                scraper.validate_complete_exam(questions, self.html)
        with self.assertRaises(ValueError):
            scraper.build_group_choice_correctness(5, [1, 1])

    def test_paced_session_waits_for_every_request_and_archives(self):
        response = requests.Response()
        response.status_code = 200
        response._content = b"<html>fixture</html>"
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        with tempfile.TemporaryDirectory() as directory, patch.object(requests.Session, "request", return_value=response), \
             patch.object(scraper.time, "sleep") as sleep, patch.object(scraper.random, "uniform", return_value=7):
            session = scraper.PacedArchiveSession(Path(directory))
            session.get(self.url)
            session.get(self.url + "image.png")
            self.assertEqual(sleep.call_args_list, [unittest.mock.call(7), unittest.mock.call(7)])
            logs = [json.loads(line) for line in (Path(directory) / "requests.jsonl").read_text().splitlines()]
            self.assertEqual(len(logs), 2)
            self.assertTrue(all(log["status"] == 200 and len(log["sha256"]) == 64 for log in logs))
            self.assertTrue(all((Path(directory) / log["htmlPath"]).read_bytes() == response.content for log in logs))
            self.assertNotIn("headers", logs[0])

    def test_paced_session_failure_is_logged_and_waited(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(requests.Session, "request", side_effect=requests.Timeout), \
             patch.object(scraper.time, "sleep") as sleep:
            session = scraper.PacedArchiveSession(Path(directory))
            with self.assertRaises(requests.Timeout):
                session.get(self.url)
            sleep.assert_called_once()
            record = json.loads((Path(directory) / "requests.jsonl").read_text())
            self.assertEqual(record["errorType"], "Timeout")
            self.assertNotIn("headers", record)


if __name__ == "__main__":
    unittest.main()
