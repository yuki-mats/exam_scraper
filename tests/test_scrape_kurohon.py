from __future__ import annotations

import os
import unittest

from scrape_kurohon import build_answer_result_text, extract_round_number_from_url, parse_exam_page_html


SAMPLE_HTML = """
<html>
  <body>
    <main id="single-exams">
      <h1>第1回 柔道整復師国家試験問題</h1>
      <section class="past-question">
        <h2>午前問題</h2>
        <div class="past-question__list">
          <p class="past-question__title">問題１　正しいのはどれか。</p>
          <p class="past-question__btn">答えを見る</p>
        </div>
        <div class="past-question__answer-wrap">
          <p class="past-question__answer">1．誤答1</p>
          <p class="past-question__answer">2．誤答2</p>
          <p class="past-question__answer--true">3．正答</p>
          <p class="past-question__answer">4．誤答4</p>
        </div>
        <div class="past-question__list">
          <p class="past-question__title">問題16 急性虫垂炎の症状で誤っているのはどれか。</p>
          <p class="past-question__btn">答えを見る</p>
        </div>
        <div class="past-question__answer-wrap">
          <p class="past-question__answer">1．正しい記述1</p>
          <p class="past-question__answer--true">2．CSS上の誤正解</p>
          <p class="past-question__answer">3．正しい記述3</p>
          <p class="past-question__answer">4．誤った記述</p>
        </div>
        <div class="past-question__list">
          <p class="past-question__title">問題3 成人の頭蓋骨と数との組合せで正しいのはどれか。※解なし</p>
          <p class="past-question__btn">答えを見る</p>
        </div>
        <div class="past-question__answer-wrap">
          <p class="past-question__answer">1．選択肢1</p>
          <p class="past-question__answer">2．選択肢2</p>
          <p class="past-question__answer">3．選択肢3</p>
          <p class="past-question__answer">4．選択肢4</p>
        </div>
        <h2 id="all-answer">第1回 柔道整復師国家試験問題 解答</h2>
        <table>
          <tr>
            <th class="past-question__all-head">問題 1</th>
            <td class="past-question__all-data">3</td>
          </tr>
          <tr>
            <th class="past-question__all-head">問題 2</th>
            <td class="past-question__all-data">4</td>
          </tr>
          <tr>
            <th class="past-question__all-head">問題 3</th>
            <td class="past-question__all-data"></td>
          </tr>
        </table>
      </section>
    </main>
  </body>
</html>
"""


class ScrapeKurohonTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.setdefault("QUESTION_ID_SECRET_KEY", "test-secret")

    def test_parse_uses_answer_table_and_ordinal_for_misnumbered_title(self) -> None:
        questions = parse_exam_page_html(
            SAMPLE_HTML,
            "https://kurohon.jp/gakusei/exams_js/js_1/",
            output_list_group_id="1993",
        )

        self.assertEqual(len(questions), 3)
        self.assertEqual(questions[1]["questionLabel"], "問2")
        self.assertEqual(questions[1]["sourceDisplayedQuestionNumber"], 16)
        self.assertEqual(questions[1]["sourceQuestionOrdinal"], 2)
        self.assertEqual(questions[1]["sourceAnswerStatus"], "answer_table")
        self.assertEqual(questions[1]["answer_result_text"], "正解は 4 です。")
        self.assertEqual(questions[1]["answerTableCorrectChoiceNumbers"], [4])
        self.assertEqual(questions[1]["choiceClassCorrectChoiceNumbers"], [2])
        self.assertEqual(
            questions[1]["correctChoiceText"],
            ["正しい", "正しい", "正しい", "間違い"],
        )

    def test_parse_marks_no_answer_as_manual_null_correct_choices(self) -> None:
        questions = parse_exam_page_html(
            SAMPLE_HTML,
            "https://kurohon.jp/gakusei/exams_js/js_1/",
            output_list_group_id="1993",
        )

        no_answer = questions[2]
        self.assertEqual(no_answer["sourceAnswerStatus"], "no_answer")
        self.assertEqual(no_answer["answer_result_text"], "解なしです。")
        self.assertEqual(no_answer["answer_result_inferred_correct_choice_numbers"], [])
        self.assertEqual(no_answer["correctChoiceText"], [None, None, None, None])

    def test_build_answer_result_text_supports_multiple_answers(self) -> None:
        self.assertEqual(build_answer_result_text([1, 3]), "正解は 1, 3 です。")

    def test_extract_round_number_from_url_supports_hq_pages(self) -> None:
        self.assertEqual(
            extract_round_number_from_url("https://kurohon.jp/gakusei/exams_hq/hq_34/"),
            34,
        )

    def test_extract_round_number_from_url_supports_am_pages(self) -> None:
        self.assertEqual(
            extract_round_number_from_url("https://kurohon.jp/gakusei/exams_am/am_23/"),
            23,
        )


if __name__ == "__main__":
    unittest.main()
