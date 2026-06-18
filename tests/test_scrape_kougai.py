from __future__ import annotations

import os
import unittest

from scrape_kougai import (
    discover_qualification_text_question_urls,
    discover_zoron_question_urls,
    parse_qualification_text_question_page,
    parse_yakutik_question_page,
    parse_zoron_question_page,
    source_filename_suffix_for_url,
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

        set_amount_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "返送汚泥率はいくらにすればよいか。",
        )
        set_amount_question = parse_yakutik_question_page(
            set_amount_html,
            "https://yaku-tik.com/kougai/r6-osui-12/",
        ).question
        self.assertEqual(set_amount_question["questionIntent"], "select_correct")
        self.assertEqual(set_amount_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        change_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "空気の供給量はおよそ何%変化するか。",
        )
        change_question = parse_yakutik_question_page(
            change_html,
            "https://yaku-tik.com/kougai/r5-taitoku-04/",
        ).question
        self.assertEqual(change_question["questionIntent"], "select_correct")
        self.assertEqual(change_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        percent_plain_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "集じん装置単体の集じん率は何%か。",
        )
        percent_plain_question = parse_yakutik_question_page(
            percent_plain_html,
            "https://yaku-tik.com/kougai/r5-baifun-01/",
        ).question
        self.assertEqual(percent_plain_question["questionIntent"], "select_correct")
        self.assertEqual(percent_plain_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        count_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "検定法として適用できる有害物質は、いくつあるか。",
        )
        count_question = parse_yakutik_question_page(
            count_html,
            "https://yaku-tik.com/kougai/r3-suiyuu-13/",
        ).question
        self.assertEqual(count_question["questionIntent"], "select_correct")
        self.assertEqual(count_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        degree_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "汚染物質の濃度(mg/L)はどの程度になるか。",
        )
        degree_question = parse_yakutik_question_page(
            degree_html,
            "https://yaku-tik.com/kougai/r2-suigai-08/",
        ).question
        self.assertEqual(degree_question["questionIntent"], "select_correct")
        self.assertEqual(degree_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        fraction_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "不純物質の量を、およそ何分の1に減少させることができるか。",
        )
        fraction_question = parse_yakutik_question_page(
            fraction_html,
            "https://yaku-tik.com/kougai/r2-osui-02/",
        ).question
        self.assertEqual(fraction_question["questionIntent"], "select_correct")
        self.assertEqual(fraction_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        which_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "粒子の分離効率が最も高くなるのは、どの排水か。",
        )
        which_question = parse_yakutik_question_page(
            which_html,
            "https://yaku-tik.com/kougai/r2-osui-04/",
        ).question
        self.assertEqual(which_question["questionIntent"], "select_correct")
        self.assertEqual(which_question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

        decrease_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "理論湿り燃焼排ガス量は、通常空気使用時のそれに比べ、何m3N/kg減少するか。",
        )
        decrease_question = parse_yakutik_question_page(
            decrease_html,
            "https://yaku-tik.com/kougai/r1-taitoku-03/",
        ).question
        self.assertEqual(decrease_question["questionIntent"], "select_correct")

        oxygen_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "硝酸イオンにまで酸化する反応は、何倍量の酸素を必要とするか。",
        )
        oxygen_question = parse_yakutik_question_page(
            oxygen_html,
            "https://yaku-tik.com/kougai/h29-osui-14/",
        ).question
        self.assertEqual(oxygen_question["questionIntent"], "select_correct")

        amount_html = html.replace(
            "BOD汚泥負荷(kgBOD/(kgMLSS・日))を求めよ。",
            "処理水のカドミウム濃度(mg/L)はpH11で理論上どれだけになるか。",
        )
        amount_question = parse_yakutik_question_page(
            amount_html,
            "https://yaku-tik.com/kougai/h28-suiyuu-02/",
        ).question
        self.assertEqual(amount_question["questionIntent"], "select_correct")

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

    def test_yakutik_chooses_answer_list_when_multiple_ordered_lists_exist(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R4年 ばいじん・粉じん特論 問4 問題と解説</h1>
          <article><div class="entry-content">
            <p><strong>問 題</strong></p>
            <p>用語と集じん装置の組合せとして、正しいものはどれか。</p>
            <ol style="list-style-type: katakana;">
              <li>ドイッチェの式</li><li>ストークス数</li><li>液ガス比</li><li>圧力損失</li>
              <li>洗浄水量</li><li>空塔速度</li><li>分級径</li>
            </ol>
            <ul><li>ア　イ　ウ</li></ul>
            <ol>
              <li>衝突式慣性力集じん装置　電気集じん装置　サイクロン</li>
              <li>ベンチュリスクラバー　サイクロン　電気集じん装置</li>
              <li>電気集じん装置　重力集じん装置　ベンチュリスクラバー</li>
              <li>電気集じん装置　衝突式慣性力集じん装置　ベンチュリスクラバー</li>
              <li>重力集じん装置　衝突式慣性力集じん装置　サイクロン</li>
            </ol>
            <div class="blank-box bb-red">正解 (4)</div>
            <p><strong>解 説</strong></p>
            <p>4が正しいです。</p>
          </div></article>
        </body></html>
        """

        parsed = parse_yakutik_question_page(html, "https://yaku-tik.com/kougai/r4-baifun-04/")
        question = parsed.question

        self.assertEqual(len(question["choiceTextList"]), 5)
        self.assertEqual(question["choiceTextList"][0], "(1) 衝突式慣性力集じん装置　電気集じん装置　サイクロン")
        self.assertEqual(question["correctChoiceText"], ["間違い", "間違い", "間違い", "正しい", "間違い"])

    def test_yakutik_embedded_numbered_terms_with_repeated_markers(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">H29年 水質概論 問2 問題と解説</h1>
          <article><div class="entry-content">
            <p>水質汚濁防止法に関する記述中、下線を付した箇所のうち、誤っているものはどれか。</p>
            <p>削減の目標、目標年度その他(1)汚濁負荷量の総量の削減に関する基本的な事項を定めるものとする。</p>
            <p>削減の目標に関しては、当該指定項目に係る(2)総量規制基準を確保することを目途とする。</p>
            <p>一　当該指定水域に流入する水の(1)汚濁負荷量の総量</p>
            <p>二　(3)人口及び産業の動向、(4)汚水又は廃液の処理の技術の水準、(5)下水道の整備の見通し等を勘案する。</p>
            <div class="blank-box bb-red">正解 (2)</div>
            <p>解 説</p>
            <p>(2)が誤りです。</p>
          </div></article>
        </body></html>
        """

        parsed = parse_yakutik_question_page(html, "https://yaku-tik.com/kougai/h29-suigai-02/")
        question = parsed.question

        self.assertEqual(len(question["choiceTextList"]), 5)
        self.assertIn("(5) 下水道の整備", question["choiceTextList"][4])
        self.assertEqual(question["questionIntent"], "select_incorrect")
        self.assertEqual(question["correctChoiceText"], ["正しい", "間違い", "正しい", "正しい", "正しい"])

    def test_yakutik_plain_paragraph_answer_box(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">H30年 大気概論 問6 問題と解説</h1>
          <article><div class="entry-content">
            <p>窒素酸化物(NOx)に関する記述として、誤っているものはどれか。</p>
            <ol>
              <li>高温燃焼の過程ではNOの形で生成される。</li>
              <li>NOは大気中でNO2になる。</li>
              <li>環境基準もNO2について定められている。</li>
              <li>NOxは有害物質の一つに指定されている。</li>
              <li>排出量は窯業が最も多い。</li>
            </ol>
            <p>正解 (5)</p>
            <p>(5)の順序が誤っています。</p>
          </div></article>
        </body></html>
        """

        parsed = parse_yakutik_question_page(html, "https://yaku-tik.com/kougai/h30-taigai-06/")
        question = parsed.question

        self.assertEqual(question["answer_result_inferred_correct_choice_numbers"], [5])
        self.assertEqual(question["correctChoiceText"], ["正しい", "正しい", "正しい", "正しい", "間違い"])

    def test_yakutik_image_numeric_question_defaults_to_select_correct(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">H30年 大規模水質特論 問5 問題と解説</h1>
          <article><div class="entry-content">
            <p>図はビール製造業において水合理化計画を実施した後の用排水系統図を示したものである。数字は水量(m3/日)であり、</p>
            <p><img src="https://yaku-tik.com/kougai/wp-content/uploads/sites/3/2018/01/h30-daisui-05.png"></p>
            <ol><li>1950</li><li>1970</li><li>2070</li><li>2270</li><li>2290</li></ol>
            <div class="blank-box bb-red">正解 (1)</div>
            <p>解 説</p>
            <p>計算すると1950です。</p>
          </div></article>
        </body></html>
        """

        parsed = parse_yakutik_question_page(html, "https://yaku-tik.com/kougai/h30-daisui-05/")
        question = parsed.question

        self.assertEqual(question["questionIntent"], "select_correct")
        self.assertEqual(question["correctChoiceText"], ["正しい", "間違い", "間違い", "間違い", "間違い"])

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

        sparse_explanation_html = html.replace(
            "<p>(1)は誤りです。</p>\n            <p>(2)は誤りです。</p>\n            <p>(3)はいずれも正しいです。</p>\n            <p>(4)は誤りです。</p>\n            <p>(5)は誤りです。</p>",
            "<p>よって、それを満たす選択肢(3)が正解です。</p>",
        )
        sparse_question = parse_yakutik_question_page(
            sparse_explanation_html,
            "https://yaku-tik.com/kougai/r7-baifun-07/",
        ).question
        self.assertEqual(
            sparse_question["choiceTextList"],
            ["(1) 画像内の選択肢1", "(2) 画像内の選択肢2", "(3) 画像内の選択肢3", "(4) 画像内の選択肢4", "(5) 画像内の選択肢5"],
        )

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

    def test_zoron_embedded_numbered_terms_when_ordered_list_is_not_choice_list(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R5年 大気概論 問2（排出基準）</h1>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>排出基準に関する記述中，下線を付した箇所のうち，誤っているものはどれか。</p>
            <ol>
              <li>いおう酸化物の量について，(1)政令で定める地域の区分ごとに定める許容限度</li>
              <li>ばいじんの量について，(2)排出物に含まれるばいじんの量について，(3)施設の種類及び(4)燃料の種類ごとに定める許容限度</li>
              <li>有害物質の量について，(5)有害物質の種類及び(3)施設の種類ごとに定める許容限度</li>
            </ol>
            <h3>解答</h3><p>（４）</p>
            <h3>解説</h3><p>(4)が誤りです。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R5-2-02")
        question = parsed.question

        self.assertEqual(len(question["choiceTextList"]), 5)
        self.assertIn("(4) 燃料の種類", question["choiceTextList"][3])
        self.assertEqual(question["correctChoiceText"], ["正しい", "正しい", "正しい", "間違い", "正しい"])

    def test_zoron_inline_circled_choices_are_split(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R5年 大気特論 問4（燃焼計算）</h1>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>空気の供給量はおよそ何％変化するか。</p>
            <p>⑴　－ 5　⑵　－ 3　⑶　3　⑷　5　⑸　7</p>
            <h3>解答</h3><p>（５）</p>
            <h3>解説</h3><p>5が正解です。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R5-3-04")
        question = parsed.question

        self.assertEqual(question["choiceTextList"], ["(1) － 5", "(2) － 3", "(3) 3", "(4) 5", "(5) 7"])
        self.assertEqual(question["correctChoiceText"], ["間違い", "間違い", "間違い", "間違い", "正しい"])

    def test_zoron_data_table_is_not_misread_as_choice_table(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R4年 大気特論 問4（燃焼計算）</h1>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>石炭中N分から発生するNOの量を調べるため，次の結果を得た。</p>
            <table>
              <tr><td></td><td>乾き燃焼ガス中O2濃度（%）</td><td>乾き燃焼ガス中NO濃度（ppm）</td></tr>
              <tr><td>条件1</td><td>2.0</td><td>160</td></tr>
              <tr><td>条件2</td><td>5.0</td><td>200</td></tr>
            </table>
            <p>条件2 の発生量は，条件1 のそれの何倍か。</p>
            <p>⑴1.2　⑵1.25　⑶1.3　⑷1.5　⑸1.7</p>
            <h3>解答</h3><p>（４）</p>
            <h3>解説</h3><p>1.5倍となるため、(4)が正解となります。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R4-3-04")
        question = parsed.question

        self.assertEqual(question["choiceTextList"], ["(1) 1.2", "(2) 1.25", "(3) 1.3", "(4) 1.5", "(5) 1.7"])
        self.assertEqual(question["correctChoiceText"], ["間違い", "間違い", "間違い", "正しい", "間違い"])

    def test_zoron_nested_choice_table_inside_ordered_list(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R4年 ばいじん・粉じん特論 問4（集じん装置の性能評価）</h1>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>集じん装置の性能評価に関わるア～ウの用語と，組合せとして，正しいものはどれか。</p>
            <ol style="list-style-type: katakana">
              <li>ドイッチェの式</li>
              <li>ストークス数</li>
              <li>液ガス比</li>
              <table class="none">
                <tr><td></td><td>ア</td><td>イ</td><td>ウ</td></tr>
                <tr><td>⑴</td><td>衝突式慣性力集じん装置</td><td>電気集じん装置</td><td>サイクロン</td></tr>
                <tr><td>⑵</td><td>ベンチュリスクラバー</td><td>サイクロン</td><td>電気集じん装置</td></tr>
                <tr><td>⑶</td><td>電気集じん装置</td><td>重力集じん装置</td><td>ベンチュリスクラバー</td></tr>
                <tr><td>⑷</td><td>電気集じん装置</td><td>衝突式慣性力集じん装置</td><td>ベンチュリスクラバー</td></tr>
                <tr><td>⑸</td><td>電気集じん装置</td><td>衝突式慣性力集じん装置</td><td>サイクロン</td></tr>
              </table>
            </ol>
            <h3>解答</h3><p>（４）</p>
            <h3>解説</h3><p>以上より、(4)が正解となります。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R4-4-04")
        question = parsed.question

        self.assertEqual(question["choiceTextList"][3], "(4) 電気集じん装置 / 衝突式慣性力集じん装置 / ベンチュリスクラバー")
        self.assertEqual(len(question["choiceTextList"]), 5)
        self.assertIn("ドイッチェの式", question["questionBodyText"])
        self.assertNotIn("ベンチュリスクラバー / サイクロン", question["questionBodyText"])
        self.assertEqual(question["correctChoiceText"], ["間違い", "間違い", "間違い", "正しい", "間違い"])

    def test_zoron_unnumbered_alpha_combination_choices_are_numbered(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R1年 公害総論 問4（環境基本法第16条）</h1>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>下線部分（ａ～ｅ）の用語のうち，正しいものの組合せはどれか。</p>
            <p>(a)望ましい基準と(b)地域又は水域と(c)都道府県の知事を含む条文。</p>
            <p>ａ，ｃ，ｅ<br>ａ，ｂ，ｅ<br>ｂ，ｃ，ｄ<br>ｂ，ｄ，ｅ<br>ｃ，ｄ，ｅ</p>
            <h3>解答</h3><p>（２）</p>
            <h3>解説</h3><p>(2)が正解です。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R1-1-04")
        question = parsed.question

        self.assertEqual(question["choiceTextList"], ["(1) ａ，ｃ，ｅ", "(2) ａ，ｂ，ｅ", "(3) ｂ，ｃ，ｄ", "(4) ｂ，ｄ，ｅ", "(5) ｃ，ｄ，ｅ"])
        self.assertNotIn("ａ，ｃ，ｅ", question["questionBodyText"])
        self.assertEqual(question["correctChoiceText"], ["間違い", "正しい", "間違い", "間違い", "間違い"])

    def test_zoron_combination_terms_can_be_inferred_from_lines_before_table(self) -> None:
        html = """
        <html><body>
          <h1 class="entry-title">R1年 ばいじん・粉じん特論 問3（流通形式集じん装置内の集じん率）</h1>
          <div class="entry-content hatenablog-entry">
            <h3>問題</h3>
            <p>ア～ウの中に挿入すべき語句の組合せとして，正しいものはどれか。</p>
            <p>気流が乱流で，装置内すべてにおいてダスト濃度が均一<br>
            気流が乱流で，流れ方向断面においてダスト濃度が均一<br>
            気流が層流</p>
            <p>
              アイウ
              <table class="none">
                <tr><td></td><td>ア</td><td>イ</td><td>ウ</td></tr>
                <tr><td>⑴</td><td>A</td><td>B</td><td>C</td></tr>
                <tr><td>⑵</td><td>A</td><td>C</td><td>B</td></tr>
                <tr><td>⑶</td><td>B</td><td>C</td><td>A</td></tr>
                <tr><td>⑷</td><td>C</td><td>A</td><td>B</td></tr>
                <tr><td>⑸</td><td>C</td><td>B</td><td>A</td></tr>
              </table>
            </p>
            <h3>解答</h3><p>（５）</p>
            <h3>解説</h3><p>以上より、(5)が正解となります。</p>
          </div>
        </body></html>
        """

        parsed = parse_zoron_question_page(html, "https://zoron.hatenablog.com/entry/R1-4-03")
        question = parsed.question

        self.assertEqual(question["sourceTransformMode"], "blank_pair_true_false")
        self.assertIn("候補語句:", question["questionBodyText"])
        self.assertIn("a：気流が乱流で，装置内すべてにおいてダスト濃度が均一", question["questionBodyText"])
        self.assertNotIn("⑴ABC", question["questionBodyText"])
        correct_pairs = {
            choice
            for choice, correctness in zip(question["choiceTextList"], question["correctChoiceText"], strict=True)
            if correctness == "正しい"
        }
        self.assertEqual(
            correct_pairs,
            {
                "ア：気流が層流",
                "イ：気流が乱流で，流れ方向断面においてダスト濃度が均一",
                "ウ：気流が乱流で，装置内すべてにおいてダスト濃度が均一",
            },
        )

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

        percent_html = question_html.replace(
            "乾燥基準の侵入空気量は重油1kg当たり，およそ何m3Nとなるか。",
            "湿り燃焼排ガス中のCO2濃度はおよそ何％か。",
        )
        percent_question = parse_qualification_text_question_page(
            percent_html,
            "https://qualification-text.com/h30kako3-03.php",
            FakeSession({answer_url: answer_html}),
        ).question
        self.assertEqual(percent_question["questionIntent"], "select_correct")

        unit_space_html = question_html.replace(
            "乾燥基準の侵入空気量は重油1kg当たり，およそ何m3Nとなるか。",
            "高発熱量当たりのNO発生量はおよそ何mg/MJ か。",
        )
        unit_space_question = parse_qualification_text_question_page(
            unit_space_html,
            "https://qualification-text.com/h26kako3-04.php",
            FakeSession({answer_url: answer_html}),
        ).question
        self.assertEqual(unit_space_question["questionIntent"], "select_correct")

    def test_qualification_text_underlined_choices_can_include_number_marker(self) -> None:
        question_url = "https://qualification-text.com/r03kako1-08.php"
        answer_url = "https://qualification-text.com/r03kako1-08a.php"
        question_html = """
        <html><body><div id="main">
          <h2>令和03年(2021) 公害総論 問8</h2>
          <h3>問8</h3>
          <p>有害大気汚染物質に関する記述中，下線を付した箇所のうち，誤っているものはどれか。<br>
             <span class="under">(1)23の優先取組物質</span>が指定されており，
             <span class="under">(2)ベンゼン</span>，
             <span class="under">(3)トリクロロエチレン</span>，
             <span class="under">(4)テトラクロロエチレン</span>，及び，
             <span class="under">(5)水銀及びその化合物</span>の4物質には，環境基準が定められている。</p>
          <form action="r03kako1-08a.php" method="post">
            <input name="r03_1_08" type="radio" value="1">
            <input name="r03_1_08" type="radio" value="2">
            <input name="r03_1_08" type="radio" value="3">
            <input name="r03_1_08" type="radio" value="4">
            <input name="r03_1_08" type="radio" value="5">
            <input name="ncAnswers" type="hidden" value="5">
          </form>
        </div></body></html>
        """
        answer_html = """
        <html><body><div id="main">
          <h5>問8 解答・解説</h5>
          <p>【正解】(5)<br>水銀が誤りです。</p>
        </div></body></html>
        """

        parsed = parse_qualification_text_question_page(
            question_html,
            question_url,
            FakeSession({answer_url: answer_html}),
        )
        question = parsed.question

        self.assertEqual(len(question["choiceTextList"]), 5)
        self.assertEqual(question["choiceTextList"][4], "(5) 水銀及びその化合物")
        self.assertEqual(question["correctChoiceText"], ["正しい", "正しい", "正しい", "正しい", "間違い"])

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

    def test_source_filename_suffixes_are_stable(self) -> None:
        self.assertEqual(source_filename_suffix_for_url("https://yaku-tik.com/kougai/"), "yakutik")
        self.assertEqual(source_filename_suffix_for_url("https://qualification-text.com/r04questions.php"), "qualification_text")
        self.assertEqual(source_filename_suffix_for_url("https://zoron.hatenablog.com/entry/R6"), "zoron")

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
