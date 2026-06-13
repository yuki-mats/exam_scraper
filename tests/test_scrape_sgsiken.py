from __future__ import annotations

import os
import unittest

import requests

from scrape_sgsiken import (
    collect_question_page_urls,
    parse_pm_question_page,
    parse_q_question_page,
)


RUN_LIVE_TESTS = os.environ.get("RUN_LIVE_TESTS") == "1"


class ScrapeSgsikenTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("QUESTION_ID_SECRET_KEY", "test-secret")

    def test_collect_question_page_urls_normalizes_nw_mobile_links(self) -> None:
        list_html = """
        <main>
          <ul class="menu">
            <li><a href="am1_1.html">問1</a></li>
            <li><a href="am2_1.html">問1</a></li>
            <li><a href="am2_25.html">問25</a></li>
          </ul>
        </main>
        """

        q_urls, pm_urls = collect_question_page_urls(
            list_html,
            "https://www.nw-siken.com/s/kakomon/07_haru/",
        )

        self.assertEqual(
            q_urls,
            [
                "https://www.nw-siken.com/kakomon/07_haru/am1_1.html",
                "https://www.nw-siken.com/kakomon/07_haru/am2_1.html",
                "https://www.nw-siken.com/kakomon/07_haru/am2_25.html",
            ],
        )
        self.assertEqual(pm_urls, [])

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_parse_live_am_q14(self) -> None:
        url = "https://www.sg-siken.com/kakomon/01_aki/q14.html"
        html = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).text

        qb = parse_q_question_page(
            html,
            url,
            http_session=None,
            download_images=False,
            output_list_group_id="201902",
        )
        self.assertIsNotNone(qb)
        assert qb is not None

        self.assertEqual(qb["examYear"], 2019)
        self.assertIn("午前", qb["examLabel"])
        self.assertIn("問14", qb["questionLabel"])
        self.assertEqual(qb["questionType"], "true_false")
        self.assertEqual(len(qb["choiceTextList"]), 4)
        self.assertEqual(qb["answer_result_inferred_correct_choice_numbers"], [1])
        self.assertEqual(qb["answer_result_text"], "正解は 1 です。")
        self.assertEqual(qb["questionIntent"], "select_correct")
        self.assertEqual(qb["correctChoiceText"], ["正しい", "間違い", "間違い", "間違い"])
        self.assertEqual(len(qb["explanation_choice_snippets"]), 4)
        self.assertTrue(qb["public_question_id"])
        self.assertIn("source_question_id", qb)
        self.assertNotIn("questionSetId", qb)

    @unittest.skipUnless(RUN_LIVE_TESTS, "live site dependent (set RUN_LIVE_TESTS=1)")
    def test_parse_live_pm01_splits_multiple_questions(self) -> None:
        url = "https://www.sg-siken.com/kakomon/01_aki/pm01.html"
        html = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"}).text

        qbs = parse_pm_question_page(
            html,
            url,
            http_session=None,
            download_images=False,
            output_list_group_id="201902",
        )
        self.assertTrue(qbs)
        self.assertEqual({qb["examYear"] for qb in qbs}, {2019})
        self.assertTrue(any("午後問1" in qb["questionLabel"] for qb in qbs))
        # 少なくとも1件は正解番号が取れている
        self.assertTrue(any(qb["answer_result_inferred_correct_choice_numbers"] for qb in qbs))
        # すべて true_false で出す
        self.assertTrue(all(qb["questionType"] == "true_false" for qb in qbs))


if __name__ == "__main__":
    unittest.main()
