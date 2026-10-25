from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from scripts.scrape.knoun import import_group, parse_transcription
from scripts.scrape.knoun_images import BOOK_ID, atomic_json, read_manifest, store_png
from scripts.scrape.knoun_ocr import make_draft
from scripts.scrape.knoun_review import reviewed_pair, save_review

BASE = f"https://knoun.jp/knounclient/contents/{BOOK_ID}/"
QUESTION = "第1問\n次のア〜コの文章のうち、内容が正しいものには①を、誤っているものには②を選びなさい。\nア\n照合用の文章。"
EXPLANATION = "解答・解説\n正答…②\n照合用の解説。"


class KnounSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        self.root = self.output / "eco-kentei"
        self.group = "knoun-2026-mock-1"
        self.manifest_path = self.root / "question_images" / self.group / "capture_manifest.json"
        self.manifest = read_manifest(self.manifest_path, 1, BOOK_ID)
        self.env = patch.dict(os.environ, {"QUESTION_ID_SECRET_KEY": "fixture-only"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def fixture(self, number=1, reviewed=True):
        url = BASE + f"mondai1_{number}.html"
        record = {"sourceQuestionId": url, "questionFrameUrl": url,
                  "explanationFrameUrl": BASE + f"comment1_{number}.html"}
        for role, prefix in (("questionImage", "mondai"), ("explanationImage", "comment")):
            name = f"{prefix}1_{number}.png"
            buffer = io.BytesIO()
            Image.new("RGB", (450, 800), "white").save(buffer, format="PNG")
            image = store_png(self.manifest_path.parent / "raw" / name, buffer.getvalue(), "image/png")
            image.update(relativePath=f"raw/{name}", sourceUrl=BASE + name)
            record[role] = image
        self.manifest["records"][url] = record
        atomic_json(self.manifest_path, self.manifest)
        for role in ("questionImage", "explanationImage"):
            path = self.root / "ocr" / self.group / "drafts" / (Path(record[role]["filename"]).stem + ".json")
            make_draft(record, role, self.manifest_path, path, lambda _: "未照合OCR")
        if reviewed:
            save_review(self.root, 1, url, question_hash=record["questionImage"]["sha256"],
                        explanation_hash=record["explanationImage"]["sha256"],
                        question_text=QUESTION, explanation_text=EXPLANATION, question_image_content="text_only")
        return url, record

    def test_unreviewed_question_stops_entire_selected_scope_before_writes(self):
        self.fixture(1)
        self.fixture(2, reviewed=False)
        with self.assertRaisesRegex(ValueError, "目視照合"):
            import_group(self.output, 1, 2)
        self.assertFalse((self.root / "questions_json").exists())

    def test_review_keeps_original_ocr_and_rejects_stale_pair(self):
        url, _ = self.fixture()
        path = self.root / "ocr" / self.group / "drafts/mondai1_1.json"
        draft = json.loads(path.read_text())
        self.assertEqual(draft["text"], "未照合OCR")
        draft["visualReview"]["explanationImageSha256"] = "foreign-image"
        atomic_json(path, draft)
        with self.assertRaisesRegex(ValueError, "画像対応"):
            reviewed_pair(self.root, 1, url)

    def test_cross_question_draft_is_rejected(self):
        url, _ = self.fixture()
        path = self.root / "ocr" / self.group / "drafts/comment1_1.json"
        draft = json.loads(path.read_text())
        draft["binding"]["sourceQuestionId"] = BASE + "mondai1_2.html"
        atomic_json(path, draft)
        with self.assertRaisesRegex(ValueError, "対応"):
            reviewed_pair(self.root, 1, url)

    def test_registration_retains_provenance_and_ids_and_never_rewrites_source(self):
        url, _ = self.fixture()
        first = import_group(self.output, 1, 1)
        path = self.root / "questions_json" / self.group / "00_source/question_mondai1_1.json"
        original = path.read_bytes()
        record = json.loads(original)["question_bodies"][0]
        self.assertEqual(record["source_question_id"], url)
        self.assertEqual(record["sourceTranscription"]["questionText"], QUESTION)
        self.assertTrue(record["sourceTranscription"]["originalizationRequired"])
        self.assertNotIn("questionType", record)
        self.assertEqual(record["questionImageStorageUrls"], [])
        self.assertEqual(first["scope"], "limited")
        self.assertEqual(import_group(self.output, 1, 1)["newQuestionCount"], 0)
        draft_path = self.root / "ocr" / self.group / "drafts/comment1_1.json"
        draft = json.loads(draft_path.read_text())
        draft["verifiedText"] += "\n転記変更。"
        atomic_json(draft_path, draft)
        with self.assertRaisesRegex(ValueError, "既存00_source"):
            import_group(self.output, 1, 1)
        self.assertEqual(path.read_bytes(), original)

    def test_figure_or_unclassified_scan_cannot_be_discarded(self):
        self.fixture()
        path = self.root / "ocr" / self.group / "drafts/mondai1_1.json"
        draft = json.loads(path.read_text())
        draft["visualReview"]["questionImageContent"] = "contains_figure"
        atomic_json(path, draft)
        with self.assertRaisesRegex(ValueError, "図表"):
            import_group(self.output, 1, 1)

    def test_target_blank_and_answer_text_are_checked_within_the_same_question(self):
        question = "語句を選びなさい。\n［語群］\n①人工排熱\n②熱中症\n［ウ］にあてはまる最も適切な語句の番号を以下に入力してください。"
        parsed = parse_transcription(question, "解答・解説\n正答…②熱中症")
        self.assertIn("［ウ］", parsed["questionBodyText"])
        self.assertEqual(parsed["choiceTextList"], ["人工排熱", "熱中症"])
        self.assertEqual(parsed["explanation_common_prefix"], [])
        with self.assertRaisesRegex(ValueError, "正答語句"):
            parse_transcription(question, "解答・解説\n正答…②人工排熱")
        with self.assertRaises(ValueError):
            parse_transcription(question.replace("②熱中症", "⑫熱中症"), "解答・解説\n正答…②熱中症")

    def test_numbered_choices_are_read_from_this_questions_printed_labels(self):
        question = "最も適切な略称を選びなさい。\n\n①地球サミット\n②GHG\n③EANET\n④IPCC"
        parsed = parse_transcription(question, "解答・解説\n正答…④IPCC\n当該問題の解説。")
        self.assertEqual(parsed["questionBodyText"], "最も適切な略称を選びなさい。")
        self.assertEqual(parsed["choiceTextList"], ["地球サミット", "GHG", "EANET", "IPCC"])
        self.assertEqual(parsed["answer_result_inferred_correct_choice_numbers"], [4])
        with self.assertRaises(ValueError):
            parse_transcription(question, "解答・解説\n正答…④GHG")

    def test_explicit_reviewed_scope_can_progress_past_an_unreviewed_question(self):
        self.fixture(1, reviewed=False)
        url, _ = self.fixture(2)
        report = import_group(self.output, 1, source_ids=[url])
        source = self.root / "questions_json" / self.group / "00_source"
        self.assertEqual([p.name for p in source.glob("*.json")], ["question_mondai1_2.json"])
        self.assertEqual(report["scope"], "explicit_selection")
        self.assertEqual(report["selectedSourceIds"], [url])
        with self.assertRaises(ValueError):
            import_group(self.output, 1, source_ids=[url, url])

    def test_printed_pool_labels_need_not_start_at_one(self):
        question = "［キ］の語句を選びなさい。\n［語群］\n⑯水質汚濁防止法\n⑰化審法\n⑱農薬取締法\n⑲水俣病\n⑳イタイイタイ病\n㉑中皮腫\n㉒揮発性有機化合物\n「キ」にあてはまる最も適切な語句の番号を以下に入力してください。"
        parsed = parse_transcription(question, "解答・解説\n正答･･･㉒揮発性有機化合物")
        self.assertEqual(parsed["sourceChoiceLabels"], ["⑯", "⑰", "⑱", "⑲", "⑳", "㉑", "㉒"])
        self.assertEqual(parsed["answer_result_inferred_correct_choice_numbers"], [7])
        self.assertEqual(parsed["sourceSelectedChoiceLabel"], "㉒")
        self.assertEqual(parsed["answer_result_text"], "正答･･･㉒揮発性有機化合物")

    def test_editorial_annotation_is_kept_in_raw_answer_but_not_choice_word(self):
        parsed = parse_transcription(QUESTION, "解答・解説\n正答･･･② ＜テキスト外＞\n説明。")
        self.assertEqual(parsed["answer_result_text"], "正答･･･② ＜テキスト外＞")
        self.assertEqual(parsed["explanation_common_prefix"], ["説明。"])

    def test_choice_range_in_instruction_is_not_a_choice(self):
        question = "次の\n①～④の記述から選びなさい。\n① 太陽光\n② 再エネ証書\n③ 原子力\n④ 地熱"
        parsed = parse_transcription(question, "解答・解説\n正答…③")
        self.assertIn("①～④", parsed["questionBodyText"])
        self.assertEqual(parsed["choiceTextList"], ["太陽光", "再エネ証書", "原子力", "地熱"])

    def test_confirmed_printed_discrepancy_is_retained_without_correcting_source(self):
        question = "国連の専門機関を選びなさい。\n①国連教育科学文化機関（UNESCO）\n②別の候補"
        explanation = "解答・解説\n正答…①国際教育科学文化機関（UNESCO）\n原文の説明。"
        with self.assertRaises(ValueError):
            parse_transcription(question, explanation)
        review = {"status": "confirmed_source_discrepancy", "printedLabel": "①",
                  "printedAnswerWord": "国際教育科学文化機関（UNESCO）", "printedChoiceText": "国連教育科学文化機関（UNESCO）",
                  "referenceUrls": ["https://www.mext.go.jp/unesco/003/001.htm"]}
        result = parse_transcription(question, explanation, answer_word_review=review)
        self.assertEqual(result["answer_result_text"], "正答…①国際教育科学文化機関（UNESCO）")
        self.assertEqual(result["choiceTextList"][0], "国連教育科学文化機関（UNESCO）")
        self.assertEqual(result["sourceAnswerWordMismatch"]["printedLabel"], "①")
        with self.assertRaises(ValueError):
            parse_transcription(question, explanation, answer_word_review=review | {"printedLabel": "②"})


if __name__ == "__main__":
    unittest.main()
