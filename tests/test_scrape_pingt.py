from __future__ import annotations

import unittest

from scripts.scrape.common import make_url_source_question_id, source_site_from_url
from scripts.scrape.pingt import (
    build_answer_result_text,
    choice_truth_labels,
    determine_question_intent,
    parse_index_page,
    parse_question_page,
    question_text_matches_index,
    source_filename,
    subject_id_from_url,
)


class ScrapePingTTests(unittest.TestCase):
    def test_question_text_matches_truncated_index_text(self) -> None:
        self.assertTrue(
            question_text_matches_index(
                "インスタンスの課金方法は...",
                "インスタンスの課金方法はどのように決まるか。",
            )
        )
        self.assertFalse(
            question_text_matches_index(
                "インスタンスの課金方法は...",
                "ストレージの課金方法はどのように決まるか。",
            )
        )
        self.assertTrue(
            question_text_matches_index(
                "クラウド導入で行うべきことはどれか。",
                "クラウド導入で行うべきことはどれか。(2つ選択)",
            )
        )

    def test_parse_index_page_collects_stable_ids_count_and_pagination(self) -> None:
        html = """
        <main>
          <p><span>3</span> 件の問題が該当します</p>
          <a href="/question_subjects/76/questions/39395">
            <p>39395 オンプレミスとクラウド</p>
            <p>オンプレミスの特徴はどれか。</p>
          </a>
          <a href="/question_subjects/76/questions/39396">
            <p>39396 AWSの概要</p>
            <p>AWSのリソースを操作できるものはどれか。</p>
          </a>
          <a href="/question_subjects/76/questions?page=2">2</a>
        </main>
        """

        parsed = parse_index_page(
            html,
            page_url="https://mondai.ping-t.com/question_subjects/76/questions",
            subject_id="76",
        )

        self.assertEqual(parsed.expected_count, 3)
        self.assertEqual(parsed.page_count, 2)
        self.assertEqual([item.question_id for item in parsed.questions], ["39395", "39396"])
        self.assertEqual(parsed.questions[0].category, "オンプレミスとクラウド")
        self.assertEqual(parsed.questions[0].question_text, "オンプレミスの特徴はどれか。")

    def test_parse_question_page_extracts_question_answer_explanation_images_and_references(self) -> None:
        html = """
        <main>
          <ul><li>問題ID : <span class="text-roman-number">39490</span><span>オンプレミスとクラウド</span></li></ul>
          <div class="mb-6">SaaSの説明で正しいのはどれか。<img src="/static/question_subjects/76/question_images/10.jpg"></div>
          <div class="form-check"><input class="form-check-input" type="radio"><label class="form-check-label">サーバー機能を提供する</label></div>
          <div class="form-check"><input class="form-check-input" type="radio"><label class="form-check-label text-info font-weight-bold correct-image-border">アプリケーションを含む機能を提供する</label></div>
          <div class="card-body">
            <p><strong>正解</strong></p>
            <p class="h3 text-info"><strong class="correct-image-border">アプリケーションを含む機能を提供する</strong></p>
            <p><strong>解説</strong><button data-ai-assistant-button--component-credential-value="secret">AI</button></p>
            <div>SaaSではアプリケーションまで提供します。<img src="/static/question_subjects/76/question_images/11.jpg"></div>
          </div>
          <div class="card-body">
            <p><strong>参考URL</strong></p>
            <p><a href="https://docs.aws.amazon.com/example">AWS公式資料</a></p>
          </div>
        </main>
        """

        parsed = parse_question_page(
            html,
            page_url="https://mondai.ping-t.com/question_subjects/76/questions/39490",
            subject_id="76",
            expected_question_id="39490",
        )

        self.assertEqual(parsed.question_id, "39490")
        self.assertEqual(parsed.category, "オンプレミスとクラウド")
        self.assertEqual(parsed.choices, ("サーバー機能を提供する", "アプリケーションを含む機能を提供する"))
        self.assertEqual(parsed.correct_choice_numbers, (2,))
        self.assertEqual(parsed.selection_type, "radio")
        self.assertEqual(parsed.explanation_text, "SaaSではアプリケーションまで提供します。")
        self.assertEqual(
            parsed.question_image_urls,
            ("https://mondai.ping-t.com/static/question_subjects/76/question_images/10.jpg",),
        )
        self.assertEqual(
            parsed.explanation_image_urls,
            ("https://mondai.ping-t.com/static/question_subjects/76/question_images/11.jpg",),
        )
        self.assertEqual(
            parsed.reference_urls,
            ({"title": "AWS公式資料", "url": "https://docs.aws.amazon.com/example"},),
        )

    def test_parse_question_page_handles_multiple_incorrect_answers(self) -> None:
        html = """
        <main>
          <li><span class="text-roman-number">90001</span><span>セキュリティ</span></li>
          <div class="mb-6">誤っているものを2つ選択してください。</div>
          <div><input class="form-check-input" type="checkbox"><label class="form-check-label text-info correct-image-border">誤答A</label></div>
          <div><input class="form-check-input" type="checkbox"><label class="form-check-label">正答B</label></div>
          <div><input class="form-check-input" type="checkbox"><label class="form-check-label text-info correct-image-border">誤答C</label></div>
          <div class="card-body">
            <p><strong>正解</strong></p>
            <p class="h3 text-info"><strong>誤答A</strong><strong>誤答C</strong></p>
            <p><strong>解説</strong></p><div>AとCが誤りです。</div>
          </div>
        </main>
        """

        parsed = parse_question_page(
            html,
            page_url="https://mondai.ping-t.com/question_subjects/76/questions/90001",
            subject_id="76",
        )

        self.assertEqual(parsed.correct_choice_numbers, (1, 3))
        self.assertEqual(determine_question_intent(parsed.question_text), "select_incorrect")
        self.assertEqual(
            choice_truth_labels(
                choice_count=3,
                correct_choice_numbers=parsed.correct_choice_numbers,
                question_intent="select_incorrect",
            ),
            ["間違い", "正しい", "間違い"],
        )
        self.assertEqual(build_answer_result_text(parsed.correct_choice_numbers), "正解は 1, 3 です。")

    def test_identity_uses_site_and_stable_question_url(self) -> None:
        url = "https://mondai.ping-t.com/question_subjects/76/questions/39490"

        self.assertEqual(subject_id_from_url(url), "76")
        self.assertEqual(source_site_from_url(url), "ping-t")
        self.assertEqual(
            make_url_source_question_id("aws-cloud-practitioner", url),
            "aws-cloud-practitioner:ping-t:question_subjects:76:questions:39490",
        )
        self.assertEqual(source_filename("76", "39490"), "question_ping-t-76_39490.json")


if __name__ == "__main__":
    unittest.main()
