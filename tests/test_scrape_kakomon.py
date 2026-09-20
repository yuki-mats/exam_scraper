from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from scripts.scrape.kakomon import (
    build_source_record,
    determine_question_intent,
    parse_question_page,
    question_url,
)


class KakomonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = """
        <h1 class="entry-title">令和7年後期-問1</h1>
        <div class="question-container">
          <div class="question-wrap"><p class="question-body">適切でないものはどれか。</p><div class="question-subs"><img src="/q.png"></div></div>
          <div class="choices-wrap">
            <div class="choice-body" data-cnum="1"><p>１：肢1</p></div>
            <div class="choice-body" data-cnum="2"><p>２：肢2</p></div>
            <div class="choice-body choice-correct" data-cnum="3"><p>３：肢3</p></div>
            <div class="choice-body" data-cnum="4"><p>４：肢4</p></div>
            <div class="choice-body" data-cnum="5"><p>５：肢5</p></div>
          </div>
          <div class="question-footer" data-corr="3"></div>
          <p class="answer-body">答：３</p>
        </div>
        <table class="question-information"><tr><th>カテゴリ</th><td>ボイラーの構造</td></tr><tr><th>出題分野</th><td>熱・蒸気</td></tr></table>
        <div class="explanation-wrap"><ul><li class="explanation-body">説明1</li></ul></div>
        """

    def test_parse_question_checks_three_independent_answer_markers(self) -> None:
        parsed = parse_question_page(
            self.html,
            page_url="https://kako-mon.com/bo-1/2025-2-01-001/",
        )
        self.assertEqual(parsed.site_qualification, "bo-1")
        self.assertEqual(parsed.choices, ("肢1", "肢2", "肢3", "肢4", "肢5"))
        self.assertEqual(parsed.correct_choice_number, 3)
        self.assertEqual(parsed.category, "ボイラーの構造")
        self.assertEqual(parsed.question_image_urls, ("https://kako-mon.com/q.png",))

    def test_parser_is_shared_across_kakomon_qualification_slugs(self) -> None:
        parsed = parse_question_page(
            self.html,
            page_url="https://kako-mon.com/bo-2/2025-2-01-001/",
        )
        self.assertEqual(parsed.site_qualification, "bo-2")
        self.assertEqual(parsed.source_group_id, "2025-2")

    def test_answer_marker_disagreement_is_rejected(self) -> None:
        html = self.html.replace("答：３", "答：２")
        with self.assertRaisesRegex(ValueError, "正答指定が一致しません"):
            parse_question_page(
                html,
                page_url="https://kako-mon.com/bo-1/2025-2-01-001/",
            )

    def test_question_url_and_intent(self) -> None:
        self.assertEqual(
            question_url("bo-1", "2025-2", 40),
            "https://kako-mon.com/bo-1/2025-2-04-040/",
        )
        self.assertEqual(determine_question_intent("適切でないものはどれか。"), "select_incorrect")

    def test_build_source_record_uses_existing_identity_and_source_contract(self) -> None:
        parsed = parse_question_page(
            self.html.replace('<img src="/q.png">', ""),
            page_url="https://kako-mon.com/bo-1/2025-2-01-001/",
        )
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {"QUESTION_ID_SECRET_KEY": "test-secret"},
        ):
            record = build_source_record(
                parsed,
                qualification_code="boiler1",
                qualification_name="一級ボイラー技士",
                output_list_group_id="2025-2",
                session=requests.Session(),
                staged_image_dir=Path(directory),
            )

        self.assertEqual(record["canonical_question_key"], "boiler1:2025-2:q001")
        self.assertEqual(
            record["source_question_id"],
            "boiler1:kako-mon-com:bo-1:2025-2-01-001",
        )
        self.assertEqual(
            record["correctChoiceText"],
            ["正しい", "正しい", "間違い", "正しい", "正しい"],
        )
        self.assertNotIn("questionType", record)


if __name__ == "__main__":
    unittest.main()
