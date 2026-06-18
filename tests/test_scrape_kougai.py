from __future__ import annotations

import os
import unittest

from scrape_kougai import (
    discover_qualification_text_question_urls,
    discover_zoron_question_urls,
    parse_qualification_text_question_page,
    parse_yakutik_question_page,
    parse_zoron_question_page,
)
from scripts.scrape.qualification_presets import build_list_first_page_url, load_scrape_preset


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.apparent_encoding = "utf-8"
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def get(self, url: str, timeout: int = 20) -> FakeResponse:
        if url not in self.mapping:
            raise AssertionError(f"unexpected URL: {url}")
        return FakeResponse(self.mapping[url])


class ScrapeKougaiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("QUESTION_ID_SECRET_KEY", "test-secret")

    def test_yakutik_fill_blank_combination_becomes_blank_term_true_false(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R7年 公害総論 問1 問題と解説</h1>
          <article><div class="entry-content">
            <p><strong>問 題</strong></p>
            <p>ア～ウの中に挿入すべき語句(a～c)の組合せとして、正しいものはどれか。</p>
            <p>この法律は、( ア )について、( イ )を定め、( ウ )に推進する。</p>
            <ol style="list-style-type: lower-alpha;">
              <li>総合的</li><li>環境の保全</li><li>事項</li>
            </ol>
            <ul><li>ア　イ　ウ</li></ul>
            <ol>
              <li>b c a</li>
              <li>a b c</li>
            </ol>
            <div class="blank-box bb-red">正解 (1)</div>
            <p><strong>解 説</strong></p>
            <p>アは環境の保全、イは事項、ウは総合的です。</p>
          </div></article>
        </body></html>
        """

        parsed = parse_yakutik_question_page(html, "https://yaku-tik.com/kougai/r7-kousou-01/")
        question = parsed.question

        self.assertEqual(question["sourceTransformMode"], "blank_pair_true_false")
        self.assertEqual(question["questionIntent"], "select_correct")
        self.assertIn("次の空欄と語句の対応が正しいか判定してください。", question["questionBodyText"])
        self.assertEqual(question["choiceTextList"][:3], ["ア：環境の保全", "イ：事項", "ウ：総合的"])
        self.assertEqual(question["correctChoiceText"][:3], ["正しい", "正しい", "正しい"])
        self.assertIn("ア：総合的", question["choiceTextList"])
        self.assertEqual(
            question["correctChoiceText"][question["choiceTextList"].index("ア：総合的")],
            "間違い",
        )
        self.assertEqual(question["answer_result_inferred_correct_choice_numbers"], [1, 2, 3])

    def test_yakutik_calculation_prompt_uses_select_correct_polarity(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R7年 汚水処理特論 問11 問題と解説</h1>
          <article><div class="entry-content">
            <p><strong>問 題</strong></p>
            <p>BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。</p>
            <p>(1) 0.10<br>(2) 0.15<br>(3) 0.20<br>(4) 0.28<br>(5) 0.32</p>
            <div class="blank-box bb-red">正解 (2)</div>
            <p><strong>解 説</strong></p>
            <p>計算すると0.15です。</p>
          </div></article>
        </body></html>
        """

        parsed = parse_yakutik_question_page(html, "https://yaku-tik.com/kougai/r7-osui-11/")
        question = parsed.question

        self.assertEqual(question["sourceTransformMode"], "original_choice_true_false")
        self.assertEqual(question["questionIntent"], "select_correct")
        self.assertEqual(question["choiceTextList"], ["(1) 0.10", "(2) 0.15", "(3) 0.20", "(4) 0.28", "(5) 0.32"])
        self.assertEqual(question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        becomes_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "ブロー水量を2倍に上げると濃縮倍数はいくらになるか。",
        )
        becomes_question = parse_yakutik_question_page(
            becomes_html,
            "https://yaku-tik.com/kougai/r7-daisui-06/",
        ).question
        self.assertEqual(becomes_question["questionIntent"], "select_correct")
        self.assertEqual(becomes_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        percent_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "酸素富化空気のO2濃度を何%にしたらよいか。",
        )
        percent_question = parse_yakutik_question_page(
            percent_html,
            "https://yaku-tik.com/kougai/r7-taitoku-04/",
        ).question
        self.assertEqual(percent_question["questionIntent"], "select_correct")
        self.assertEqual(percent_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

    def test_yakutik_generic_doreka_keeps_negative_priority(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R7年 水質有害物質特論 問8 問題と解説</h1>
          <article><div class="entry-content">
            <p><strong>問 題</strong></p>
            <p>次の有害物質のうち、揮散法により排水から分離するのが最も困難なものはどれか。</p>
            <p>(1) 1,2-ジクロロエタン<br>(2) ジクロロメタン<br>(3) 1,4-ジオキサン</p>
            <div class="blank-box bb-red">正解 (3)</div>
            <p><strong>解 説</strong></p>
            <p>1,4-ジオキサンが最も困難です。</p>
          </div></article>
        </body></html>
        """
        negative_html = html.replace(
            "揮散法により排水から分離するのが最も困難なものはどれか。",
            "次の記述のうち、誤っているのはどれか。",
        )

        correct_question = parse_yakutik_question_page(html, "https://yaku-tik.com/kougai/r7-suiyuu-08/").question
        incorrect_question = parse_yakutik_question_page(
            negative_html,
            "https://yaku-tik.com/kougai/r7-suiyuu-08/",
        ).question

        self.assertEqual(correct_question["questionIntent"], "select_correct")
        self.assertEqual(correct_question["correctChoiceText"], ["間違い", "間違い", "正しい"])
        self.assertEqual(incorrect_question["questionIntent"], "select_incorrect")
        self.assertEqual(incorrect_question["correctChoiceText"], ["正しい", "正しい", "間違い"])

    def test_yakutik_image_only_choice_table_uses_numbered_placeholders(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R7年 大気特論 問10 問題と解説</h1>
          <article><div class="entry-content">
            <p><strong>問 題</strong></p>
            <p>NOx抑制方式に関し、組合せのうち、正しいものはどれか。</p>
            <p><img src="https://yaku-tik.com/kougai/wp-content/uploads/sites/3/2025/11/fig_r7q-1.png"></p>
            <div class="blank-box bb-red">正解 (3)</div>
            <p><strong>解 説</strong></p>
            <p>(1)は誤りです。</p>
            <p>(2)は誤りです。</p>
            <p>(3)はいずれも正しいです。</p>
            <p>(4)は誤りです。</p>
            <p>(5)は誤りです。</p>
          </div></article>
        </body></html>
        """

        parsed = parse_yakutik_question_page(html, "https://yaku-tik.com/kougai/r7-taitoku-10/")
        question = parsed.question

        self.assertIn("選択肢は問題画像を参照してください。", question["questionBodyText"])
        self.assertEqual(
            question["choiceTextList"],
            ["(1) 画像内の選択肢1", "(2) 画像内の選択肢2", "(3) 画像内の選択肢3", "(4) 画像内の選択肢4", "(5) 画像内の選択肢5"],
        )
        self.assertEqual(question["correctChoiceText"], ["間違い", "間違い", "正しい", "間違い", "間違い"])

    def test_zoron_regular_table_uses_select_incorrect_polarity(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R6年 公害総論 問3(法律と内容の対応)</h1>
          <div class="entry-content">広告</div>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>次の法律と内容の組合せとして，誤っているものはどれか。</p>
            <table>
              <tr><td>⑴</td><td>環境基本法</td><td>環境基本計画</td></tr>
              <tr><td>⑵</td><td>悪臭防止法</td><td>悪臭原因物発生施設の公表</td></tr>
            </table>
            <h3>解答</h3><p>（２）</p>
            <h3>解説</h3><p>2が誤りです。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R6-1-03")
        question = parsed.question

        self.assertEqual(question["sourceTransformMode"], "original_choice_true_false")
        self.assertEqual(question["questionIntent"], "select_incorrect")
        self.assertEqual(question["choiceTextList"], ["(1) 環境基本法 / 環境基本計画", "(2) 悪臭防止法 / 悪臭原因物発生施設の公表"])
        self.assertEqual(question["correctChoiceText"], ["正しい", "間違い"])
        self.assertEqual(question["answer_result_text"], "正解は 2 です。")

    def test_zoron_ordered_list_choices_are_parsed(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R6年 公害総論 問4(組織法)</h1>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>特定工場に関する記述として，誤っているものはどれか。</p>
            <ol>
              <li>解任の日から2年を経過しない間は選任できない。</li>
              <li>60日以内に選任する。</li>
              <li>50万円以下の罰金に処せられる。</li>
              <li>従業員20人以下は例外付きで選任不要。</li>
              <li>2以上の工場で同一人を選任できない。</li>
            </ol>
            <h3>解答</h3><p>（４）</p>
            <h3>解説</h3><p>4が誤りです。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R6-1-04")
        question = parsed.question

        self.assertEqual(question["questionIntent"], "select_incorrect")
        self.assertEqual(question["choiceTextList"][0], "(1) 解任の日から2年を経過しない間は選任できない。")
        self.assertEqual(question["correctChoiceText"], ["正しい", "正しい", "正しい", "間違い", "正しい"])

    def test_zoron_image_only_choices_use_placeholders(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R6年 公害総論 問13(PFAS)</h1>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>PFAS，PFOS及びPFOAの包含関係を表す図として，正しいものはどれか。</p>
            <p><img src="https://cdn-ak.f.st-hatena.com/images/fotolife/z/zoron/20250105/20250105132743.png"></p>
            <h3>解答</h3><p>（２）</p>
            <h3>解説</h3><p>よって、(2)が正解となります。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R6-1-13")
        question = parsed.question

        self.assertIn("選択肢は問題画像を参照してください。", question["questionBodyText"])
        self.assertEqual(
            question["choiceTextList"],
            ["(1) 画像内の選択肢1", "(2) 画像内の選択肢2", "(3) 画像内の選択肢3", "(4) 画像内の選択肢4", "(5) 画像内の選択肢5"],
        )
        self.assertEqual(question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

    def test_qualification_text_underlined_choices_and_image_labels(self) -> None:
        question_url = "https://qualification-text.com/r04kako1-01.php"
        answer_url = "https://qualification-text.com/r04kako1-01a.php"
        question_html = """
        <html><body><div id="main">
          <h2>令和04年(2022) 公害総論 問1</h2>
          <h3>問1</h3>
          <p>環境基本法の記述中，<img src="images/kigou/sikakua.png">下線を付した箇所のうち，誤っているものはどれか。<br>
             (1)<span class="under">正しい語句</span>による説明。<br>
             (2)<span class="under">誤った語句</span>による説明。</p>
          <form action="r04kako1-01a.php" method="post">
            (1)<input name="r04_1_01" type="radio" value="1">
            (2)<input name="r04_1_01" type="radio" value="2">
            <input name="ncAnswers" type="hidden" value="2">
          </form>
        </div></body></html>
        """
        answer_html = """
        <html><body><div id="main">
          <h2>令和04年(2022) 公害総論 問1 解答・解説</h2>
          <h5>問1 解答・解説</h5>
          <p>【正解】(2)<br>解説本文です。</p>
          <a href="r04kako1-02.php">＞＞＞　次の問題へ　＞＞＞</a>
        </div></body></html>
        """
        parsed = parse_qualification_text_question_page(
            question_html,
            question_url,
            FakeSession({answer_url: answer_html}),
        )
        question = parsed.question

        self.assertIn("ア", question["originalQuestionBodyText"])
        self.assertEqual(question["questionIntent"], "select_incorrect")
        self.assertEqual(question["choiceTextList"], ["(1) 正しい語句", "(2) 誤った語句"])
        self.assertEqual(question["correctChoiceText"], ["正しい", "間違い"])
        self.assertEqual(question["answer_result_text"], "正解は 2 です。")
        self.assertIn("解説本文です。", question["explanationText"][0])

    def test_qualification_text_inline_numbered_choices_are_parsed(self) -> None:
        question_url = "https://qualification-text.com/r04kako3-03.php"
        answer_url = "https://qualification-text.com/r04kako3-03a.php"
        question_html = """
        <html><body><div id="main">
          <h2>令和04年(2022) 大気特論 問3</h2>
          <h3>問3</h3>
          <p>乾燥基準の侵入空気量は重油1kg当たり，およそ何m3Nとなるか。<br>
             (1)0.9　 (2)1.1　 (3)1.3　 (4)1.5　 (5)1.7</p>
          <form action="r04kako3-03a.php" method="post">
            <input name="r04_3_03" type="radio" value="1">
            <input name="r04_3_03" type="radio" value="2">
            <input name="r04_3_03" type="radio" value="3">
            <input name="r04_3_03" type="radio" value="4">
            <input name="r04_3_03" type="radio" value="5">
            <input name="ncAnswers" type="hidden" value="4">
          </form>
        </div></body></html>
        """
        answer_html = """
        <html><body><div id="main">
          <h2>令和04年(2022) 大気特論 問3 解答・解説</h2>
          <p>【正解】(4)<br>解説本文です。</p>
        </div></body></html>
        """

        parsed = parse_qualification_text_question_page(
            question_html,
            question_url,
            FakeSession({answer_url: answer_html}),
        )
        question = parsed.question

        self.assertEqual(question["questionIntent"], "select_correct")
        self.assertEqual(question["choiceTextList"], ["(1) 0.9", "(2) 1.1", "(3) 1.3", "(4) 1.5", "(5) 1.7"])
        self.assertEqual(question["correctChoiceText"], ["間違い", "間違い", "間違い", "正しい", "間違い"])

        times_html = question_html.replace(
            "乾燥基準の侵入空気量は重油1kg当たり，およそ何m3Nとなるか。",
            "条件2のNOの量は，条件1のそれの何倍か。",
        )
        times_question = parse_qualification_text_question_page(
            times_html,
            question_url,
            FakeSession({answer_url: answer_html}),
        ).question
        self.assertEqual(times_question["questionIntent"], "select_correct")
        self.assertEqual(times_question["choiceTextList"], ["(1) 0.9", "(2) 1.1", "(3) 1.3", "(4) 1.5", "(5) 1.7"])

    def test_qualification_text_image_choices_use_form_count(self) -> None:
        question_url = "https://qualification-text.com/r04kako4-01.php"
        answer_url = "https://qualification-text.com/r04kako4-01a.php"
        question_html = """
        <html><body><div id="main">
          <h2>令和04年(2022) ばいじん・粉じん特論 問1</h2>
          <h3>問1</h3>
          <p>頻度分布として，最も適切な図はどれか。<br><img src="images/r04-4-1t.png"></p>
          <form action="r04kako4-01a.php" method="post">
            <input name="r04_4_01" type="radio" value="1">
            <input name="r04_4_01" type="radio" value="2">
            <input name="r04_4_01" type="radio" value="3">
            <input name="r04_4_01" type="radio" value="4">
            <input name="r04_4_01" type="radio" value="5">
            <input name="ncAnswers" type="hidden" value="2">
          </form>
        </div></body></html>
        """
        answer_html = "<html><body><div id=\"main\"><p>【正解】(2)<br>解説本文です。</p></div></body></html>"

        parsed = parse_qualification_text_question_page(
            question_html,
            question_url,
            FakeSession({answer_url: answer_html}),
        )
        question = parsed.question

        self.assertIn("選択肢は問題画像を参照してください。", question["questionBodyText"])
        self.assertEqual(question["choiceTextList"], ["(1) 画像内の選択肢1", "(2) 画像内の選択肢2", "(3) 画像内の選択肢3", "(4) 画像内の選択肢4", "(5) 画像内の選択肢5"])
        self.assertEqual(question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

    def test_presets_route_kougai_sources_to_kougai_output(self) -> None:
        preset = load_scrape_preset("kougai-yakutik")
        self.assertEqual(preset.qualification_code, "kougai")
        self.assertEqual(preset.scraper_type, "kougai")
        self.assertEqual(preset.list_group_ids[0], "2025")
        self.assertEqual(
            build_list_first_page_url(preset, "2025"),
            "https://yaku-tik.com/kougai/category/kako/kako-r7/",
        )

    def test_qualification_text_discovery_keeps_php_url_without_trailing_slash(self) -> None:
        html = '<a href="r04kako1-01.php">問1</a><a href="r04kako1-01a.php">解説</a>'
        urls = discover_qualification_text_question_urls(
            FakeSession({"https://qualification-text.com/r04questions.php": html}),
            "https://qualification-text.com/r04questions.php",
        )
        self.assertEqual(urls, ["https://qualification-text.com/r04kako1-01.php"])

    def test_zoron_discovery_filters_to_requested_era(self) -> None:
        html = """
        <a href="https://zoron.hatenablog.com/entry/R6-1-01">R6-1</a>
        <a href="https://zoron.hatenablog.com/entry/R5-2-02">R5 mixed link</a>
        <a href="https://zoron.hatenablog.com/entry/R6-1-02">R6-2</a>
        """
        urls = discover_zoron_question_urls(
            FakeSession({"https://zoron.hatenablog.com/entry/R6": html}),
            "https://zoron.hatenablog.com/entry/R6",
        )
        self.assertEqual(
            urls,
            [
                "https://zoron.hatenablog.com/entry/R6-1-01/",
                "https://zoron.hatenablog.com/entry/R6-1-02/",
            ],
        )


if __name__ == "__main__":
    unittest.main()
