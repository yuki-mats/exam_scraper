from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

import requests

from scrape_sgsiken import (
    collect_question_page_urls,
    parse_pm_question_page,
    parse_q_question_page,
    split_classification_hierarchy,
    save_validated_source,
    load_existing_identities,
    download_and_save_images,
)


RUN_LIVE_TESTS = os.environ.get("RUN_LIVE_TESTS") == "1"


class ScrapeSgsikenTests(unittest.TestCase):
    def test_inline_math_keeps_complements_powers_indices_and_stable_ids(self):
        html = """<h2>ネットワークスペシャリスト令和元年秋期 午前Ⅰ 問1</h2>
        <h3 class="qno">問1</h3>
        <div id="mondai"><span class="ol">A</span>∩<span class="ol">B</span>に等しい集合はどれか。<br>2<sup>3</sup>とX<sub>1</sub>を用いる。</div>
        <ul class="selectList">
        <li><button class="selectBtn">ア</button><span id="select_a"><span class="ol">A</span>－B</span></li>
        <li><button class="selectBtn">イ</button><span id="select_i">(A∪B)－(A∩B)</span></li>
        </ul><div class="answerBox"><span id="answerChar">ア</span></div>
        <div id="kaisetsu">否定は<span class="ol">A</span>で表す。</div>"""
        url = "https://www.nw-siken.com/kakomon/01_aki/am1_1.html"
        identity = {"source_question_id": f"201902:am:問1:{url}",
                    "public_question_id": "stable-public", "original_question_id": "stable-original"}
        record = parse_q_question_page(html, url, http_session=None, download_images=False,
                                      output_list_group_id="201902", existing_identity=identity)
        self.assertEqual(record["questionBodyText"], "A̅∩B̅に等しい集合はどれか。2³とX₁を用いる。")
        self.assertEqual(record["choiceTextList"], ["A̅－B", "(A∪B)－(A∩B)"])
        self.assertEqual(record["answer_result_inferred_correct_choice_numbers"], [1])
        self.assertIn("A̅", record["explanation_choice_snippets"][0][0])
        for field, value in identity.items():
            self.assertEqual(record[field], value)

    def test_inline_math_span_does_not_truncate_unwrapped_choice(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import extract_choice_text_from_li
        from scripts.scrape.common import extract_text_with_subsup

        li = BeautifulSoup('<li><button class="selectBtn">ア</button><span class="ol">A</span>－B</li>', 'html.parser').li
        original = str(li)
        self.assertEqual(extract_choice_text_from_li(li, "ア"), "A̅－B")
        self.assertEqual(str(li), original)
        node = BeautifulSoup('<span class="ol">A</span>', 'html.parser').span
        self.assertEqual(extract_text_with_subsup(node), "A")
        self.assertEqual(extract_text_with_subsup(node, overline_classes=("ol",)), "A̅")

    def test_css_fraction_and_radical_keep_grouping_and_nested_math(self):
        from bs4 import BeautifulSoup
        from scrape_sgsiken import extract_q_text
        node = BeautifulSoup('<div>平均時間×<span class="frac"><span>ρ</span>1－ρ</span>。'
                             '<span class="root"><span class="frac"><span>A<sup>2</sup></span>'
                             '<span class="ol">B</span>＋1</span></span></div>', 'html.parser').div
        before = str(node)
        self.assertEqual(extract_q_text(node), '平均時間×(ρ)/(1－ρ)。√((A²)/(B̅＋1))')
        self.assertEqual(str(node), before)
        separator = BeautifulSoup('<span class="frac">10,000×0.01＋0.1×n<span></span>0.01＋n</span>', 'html.parser').span
        self.assertEqual(extract_q_text(separator), '(10,000×0.01＋0.1×n)/(0.01＋n)')
        double = BeautifulSoup('<span class="dol"><span class="ol">A</span></span>', 'html.parser').span
        self.assertEqual(extract_q_text(double), 'A̅̅')
        for markup in ('<span class="frac">A</span>',
                       '<span class="frac"><span>A</span></span>'):
            with self.subTest(markup=markup), self.assertRaises(ValueError):
                extract_q_text(BeautifulSoup(markup, 'html.parser').span)

    def test_question_number_excludes_heading_tools_and_keeps_existing_ids(self):
        html = """<h2>ネットワークスペシャリスト令和7年春期 午前Ⅰ 問1</h2>
        <h3 class="qno">問1<div class="tool-box">note_alt calculate</div></h3>
        <div id="mondai">適切な説明はどれか。</div>
        <ul class="selectList"><li><button class="selectBtn">ア</button>A</li>
        <li><button class="selectBtn">イ</button>B</li></ul>
        <div class="answerBox"><span id="answerChar">ア</span></div>"""
        url = "https://www.nw-siken.com/kakomon/07_haru/am1_1.html"
        identity = {"source_question_id": f"202501:am:問1:{url}", "public_question_id": "old-public", "original_question_id": "old-original"}
        record = parse_q_question_page(html, url, http_session=None, download_images=False,
                                      output_list_group_id="202501", existing_identity=identity)
        self.assertIsNotNone(record)
        self.assertEqual(record["questionLabel"], "問1")
        self.assertEqual(record["source_question_id"], identity["source_question_id"])
        self.assertEqual(record["public_question_id"], "old-public")
        self.assertEqual(record["original_question_id"], "old-original")

    def test_missing_image_stops_source_acquisition(self):
        with patch("scrape_sgsiken._download_and_save_images", return_value=[]):
            with self.assertRaises(ValueError):
                download_and_save_images(None, ["https://example.com/figure.png"], "q1", base_dir=".")

    def test_refresh_preserves_ids_and_reports_changes_after_full_validation(self):
        record = {"source_question_id": "stable", "questionBodyText": "本文", "choiceTextList": ["A", "B"],
                  "answer_result_inferred_correct_choice_numbers": [1], "public_question_id": "old-public",
                  "original_question_id": "old-original"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_validated_source(root, "202501", [record], expected_count=1)
            path = root / "question_202501_1.json"
            baseline = path.read_bytes()
            with self.assertRaises(ValueError):
                save_validated_source(root, "202501", [record], expected_count=55)
            with self.assertRaises(ValueError):
                save_validated_source(root, "202501", [{**record, "public_question_id": "new"}], expected_count=1)
            with self.assertRaises(ValueError):
                save_validated_source(root, "202501", [{**record, "answer_result_inferred_correct_choice_numbers": [3]}], expected_count=1)
            self.assertEqual(path.read_bytes(), baseline)
            result = save_validated_source(root, "202501", [{**record, "questionBodyText": "取得元の新しい本文"}], expected_count=1)
            self.assertEqual(result["changedSourceQuestionIds"], ["stable"])
            result = save_validated_source(root, "202501", [{**record, "questionBodyText": "取得元の新しい本文"}], expected_count=1)
            self.assertEqual(result["unchangedSourceQuestionIds"], ["stable"])

    def test_identity_recovery_reads_only_exact_identity_and_rejects_conflict(self):
        record = {"source_question_id": "stable", "question_url": "https://example.com/q1", "public_question_id": "old-public",
                  "original_question_id": "old-original", "questionBodyText": "古い本文"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stage in ("00_source", "12_merged_questionType"):
                (root / stage).mkdir()
                (root / stage / "question.json").write_text(json.dumps({"question_bodies": [record]}))
            identities = load_existing_identities(root)
            self.assertNotIn("questionBodyText", identities[record["question_url"]])
            (root / "12_merged_questionType" / "question.json").write_text(json.dumps({"question_bodies": [{**record, "original_question_id": "conflict"}]}))
            with self.assertRaises(ValueError):
                load_existing_identities(root)

    def test_legacy_patch_identity_alias_survives_a_changed_id_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "10_questionType_fixed").mkdir()
            (root / "10_questionType_fixed" / "question.json").write_text(json.dumps([
                {"question_url": "https://example.com/q1", "original_question_id": "old-id"}
            ]))
            identity = load_existing_identities(root)["https://example.com/q1"]
            self.assertEqual(identity["public_question_id"], "old-id")
            self.assertEqual(identity["original_question_id"], "old-id")

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

    def test_split_classification_hierarchy(self) -> None:
        hierarchy, major, middle, small = split_classification_hierarchy(
            "テクノロジ系 » セキュリティ » 情報セキュリティ対策"
        )

        self.assertEqual(
            hierarchy,
            ["テクノロジ系", "セキュリティ", "情報セキュリティ対策"],
        )
        self.assertEqual(major, "テクノロジ系")
        self.assertEqual(middle, "セキュリティ")
        self.assertEqual(small, "情報セキュリティ対策")

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
        self.assertEqual(qb["categoryMajor"], "テクノロジ系")
        self.assertEqual(qb["categoryMiddle"], "セキュリティ")
        self.assertEqual(qb["categorySmall"], "情報セキュリティ対策")
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
