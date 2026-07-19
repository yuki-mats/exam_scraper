from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.scrape.common import make_url_source_question_id, source_site_from_url
from scripts.scrape.keepitup import (
    choice_truth_labels,
    determine_question_intent,
    discover_course,
    parse_answer_page,
    parse_declared_question_count,
    parse_list_page,
    save_source_record,
    source_filename,
    synchronize_staged_images,
)


class ScrapeKeepItUpTests(unittest.TestCase):
    def test_discover_course_collects_question_sets_and_random_page(self) -> None:
        html = """
        <form action="https://aws.keepitup.jp/CL9900M001Q/">
          <input name="QSET_ID" value="CL9900M"><input name="ACTION_ID" value="Start">
        </form>
        <form action="https://aws.keepitup.jp/CLF101C001Q/">
          <input name="QSET_ID" value="CLF101C"><input name="ACTION_ID" value="Start">
        </form>
        <form action="https://aws.keepitup.jp/CLF301S001Q/">
          <input name="QSET_ID" value="CLF301S"><input name="ACTION_ID" value="Start">
        </form>
        <form action="https://aws.keepitup.jp/CL99/">
          <input name="QSET_ID" value="CL99---">
        </form>
        <form action="https://aws.keepitup.jp/AI99/">
          <input name="QSET_ID" value="AI99---">
        </form>
        """

        course = discover_course(html, page_url="https://aws.keepitup.jp/CL00/")

        self.assertEqual(course.question_set_ids, ("CLF101C", "CLF301S"))
        self.assertEqual(course.random_question_url, "https://aws.keepitup.jp/CL99/")

    def test_parse_declared_question_count(self) -> None:
        html = "<h3>（1）ランダム出題（332問）</h3>"

        self.assertEqual(
            parse_declared_question_count(html, page_url="https://aws.keepitup.jp/CL99/"),
            332,
        )

    def test_parse_list_page_collects_stable_ids_titles_category_and_pagination(self) -> None:
        html = """
        <div id="contents">
          <h3>AWS Certified Cloud Practitioner<br>テーマ別 集中演習（セキュリティとコンプライアンス）</h3>
          <table>
            <tr><th>問題タイトル</th><th>問題ID</th></tr>
            <tr><td>第1問 責任共有モデル（現在実行中の問題）</td><td>
              <form action="/CLF202C001Q/">
                <input name="QSET_ID" value="CLF202C"><input name="ACTION_ID" value="CLF202C001">
              </form>
            </td></tr>
            <tr><td>第2問 AWS IAM</td><td>
              <form action="/CLF202C002Q/">
                <input name="QSET_ID" value="CLF202C"><input name="ACTION_ID" value="CLF202C002">
              </form>
            </td></tr>
          </table>
          <form action="/CLF202C002L/">
            <input name="QSET_ID" value="CLF202C"><input name="ACTION_ID" value="List">
          </form>
        </div>
        """

        page = parse_list_page(
            html,
            page_url="https://aws.keepitup.jp/CLF202C000L/",
            question_set_id="CLF202C",
        )

        self.assertEqual([item.question_id for item in page.questions], ["CLF202C001", "CLF202C002"])
        self.assertEqual(page.questions[0].title, "責任共有モデル")
        self.assertEqual(
            page.questions[0].category,
            "テーマ別 集中演習（セキュリティとコンプライアンス）",
        )
        self.assertEqual(page.questions[0].answer_url, "https://aws.keepitup.jp/CLF202C001A/")
        self.assertEqual(page.pagination_urls, ("https://aws.keepitup.jp/CLF202C002L/",))

    def test_parse_single_answer_page_extracts_all_source_fields_and_filters_ad_image(self) -> None:
        html = """
        <div id="contents">
          <h2>解答・解説</h2>
          <h3>第69問 AWSアカウントに対するポリシー制御</h3>
          <p>複数のAWSアカウントを統一的に管理するサービスを選択してください。</p>
          <ol class="options_A">
            <li><strong class="correct_answer">AWS Organizations [正しい解答]</strong></li>
            <li>AWS IAM</li><li>AWS Security Hub</li><li>AWS RAM</li>
          </ol>
          <p>（問題ID：CLF202C069）</p>
          <div class="enclosure"><h3>解答</h3></div>
          <div class="enclosure">
            <h3>徹底解説</h3>
            <p>AWS Organizationsは複数アカウントを一元管理します。</p>
            <img class="img_chart" src="../chart/Organizations_02.jpg?1">
            <div class="text_center"><a href="//example.valuecommerce.com/ad"><img src="//ad.example/ad.gif"></a></div>
          </div>
        </div>
        """

        parsed = parse_answer_page(
            html,
            page_url="https://aws.keepitup.jp/CLF202C069A/",
            expected_question_id="CLF202C069",
        )

        self.assertEqual(parsed.title, "AWSアカウントに対するポリシー制御")
        self.assertEqual(parsed.choices[0], "AWS Organizations")
        self.assertEqual(parsed.correct_choice_numbers, (1,))
        self.assertEqual(parsed.selection_type, "radio")
        self.assertEqual(
            parsed.explanation_image_urls,
            ("https://aws.keepitup.jp/chart/Organizations_02.jpg?1",),
        )
        self.assertEqual(parsed.reference_urls, ())

    def test_parse_multiple_answer_page_and_incorrect_intent(self) -> None:
        html = """
        <div id="contents">
          <h2>解答・解説</h2>
          <h3>第1問 誤っている説明</h3>
          <p>誤っている説明を2つ選択してください。</p>
          <ol class="options_A">
            <li><strong class="correct_answer">誤答A [正しい解答]</strong></li>
            <li>正答B</li>
            <li><strong class="correct_answer">誤答C [正しい解答]</strong></li>
          </ol>
          <p>（問題ID：CLF301S001）</p>
          <div class="enclosure"><h3>徹底解説</h3><p>AとCが誤りです。</p></div>
        </div>
        """

        parsed = parse_answer_page(
            html,
            page_url="https://aws.keepitup.jp/CLF301S001A/",
        )
        intent = determine_question_intent(parsed.question_text)

        self.assertEqual(parsed.correct_choice_numbers, (1, 3))
        self.assertEqual(parsed.selection_type, "checkbox")
        self.assertEqual(intent, "select_incorrect")
        self.assertEqual(
            choice_truth_labels(
                choice_count=3,
                correct_choice_numbers=parsed.correct_choice_numbers,
                question_intent=intent,
            ),
            ["間違い", "正しい", "間違い"],
        )

    def test_identity_uses_keepitup_stable_question_url(self) -> None:
        url = "https://aws.keepitup.jp/CLF202C069Q/"

        self.assertEqual(source_site_from_url(url), "aws-keepitup-jp")
        self.assertEqual(
            make_url_source_question_id("aws-cloud-practitioner", url),
            "aws-cloud-practitioner:aws-keepitup-jp:CLF202C069Q",
        )
        self.assertEqual(source_filename("CLF202C069"), "question_keepitup-CLF202C069.json")

    def test_source_refresh_replaces_same_stable_id_in_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            source_dir = Path(temporary_dir)
            original = {
                "question_url": "https://aws.keepitup.jp/CLF202C069Q/",
                "questionBodyText": "更新前",
            }
            updated = {**original, "questionBodyText": "更新後"}

            path = save_source_record(
                source_dir=source_dir,
                output_list_group_id="keepitup-aws-clf-c02",
                source_list_group_id="CL00",
                record=original,
            )
            refreshed_path = save_source_record(
                source_dir=source_dir,
                output_list_group_id="keepitup-aws-clf-c02",
                source_list_group_id="CL00",
                record=updated,
                replace_existing=True,
            )

            self.assertEqual(refreshed_path, path)
            self.assertEqual(path.name, "question_keepitup-CLF202C069.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["question_bodies"][0]["questionBodyText"], "更新後")

    def test_image_refresh_reports_question_id_for_changed_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            staged = root / "staged"
            current = root / "current"
            staged.mkdir()
            current.mkdir()
            filename = "keepitup_clf202c069_0123456789abcdef.png"
            (staged / filename).write_bytes(b"updated")
            (current / filename).write_bytes(b"original")

            result = synchronize_staged_images(
                staged_image_dir=staged,
                image_output_dir=current,
            )

            self.assertEqual(result, (0, 1, 0, ["CLF202C069"]))
            self.assertEqual((current / filename).read_bytes(), b"updated")


if __name__ == "__main__":
    unittest.main()
