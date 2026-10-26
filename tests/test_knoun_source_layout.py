from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.scrape.knoun import locate_choice_block, parse_transcription
from scripts.scrape.knoun_source_layout import binding, validate_layout, save_manual_layout, load_layout


class KnounSourceLayoutTests(unittest.TestCase):
    question = "第6問\n図の2022年度の電源を選びなさい。\n①石油\n②LNG\n③水力\n④石炭\n【図中の文字】33.8％\n出典：原資料"
    explanation = "解答・解説\n正答…②LNG\n当該問題の説明。"
    block = "①石油\n②LNG\n③水力\n④石炭"

    def evidence(self):
        record = {"sourceQuestionId": "question-A", "questionImage": {"sha256": "q"}, "explanationImage": {"sha256": "a"}}
        bound = binding(record, self.question, self.explanation)
        value = {"schemaVersion": "knoun-source-layout/v1", "binding": bound,
                 "candidate": bound | {"reviewOutcome": "verified", "choiceBlock": self.block,
                                       "checks": {"allChoices": True, "onlyChoiceRegion": True, "answerTarget": True}},
                 "receipt": {"thread_id": "thread", "turn_id": "turn", "session_id": "session", "final_message": "JSON",
                             "image_inputs": [{"sha256": "q"}, {"sha256": "a"}]}}
        return record, value

    def test_figure_text_after_choices_stays_in_body_and_out_of_last_choice(self):
        parsed = parse_transcription(self.question, self.explanation, choice_block=self.block)
        self.assertEqual(parsed["choiceTextList"], ["石油", "LNG", "水力", "石炭"])
        self.assertIn("33.8％", parsed["questionBodyText"])
        self.assertIn("原資料", parsed["questionBodyText"])
        self.assertNotIn("①石油", parsed["questionBodyText"])

    def test_block_must_be_one_exact_span_of_this_transcription(self):
        for block in (self.block.replace("LNG", "天然ガス"), "", "①～④の記述"):
            with self.subTest(block=block), self.assertRaises(ValueError):
                locate_choice_block(self.question, block)
        with self.assertRaises(ValueError):
            locate_choice_block(self.question + "\n" + self.block, self.block)

    def test_actual_pair_and_unchanged_transcription_are_required(self):
        record, value = self.evidence()
        validate_layout(value, record, self.question, self.explanation)
        with self.assertRaises(ValueError):
            validate_layout(value, record, self.question + "変更", self.explanation)
        changed = copy.deepcopy(value)
        changed["receipt"]["image_inputs"][1]["sha256"] = "foreign-answer"
        with self.assertRaises(ValueError):
            validate_layout(changed, record, self.question, self.explanation)
        changed = copy.deepcopy(value)
        changed["candidate"]["sourceQuestionId"] = "question-B"
        with self.assertRaises(ValueError):
            validate_layout(changed, record, self.question, self.explanation)

    def test_image_answer_description_does_not_replace_the_printed_caption(self):
        question = "主題に対応する図を選びなさい。\n①8 働きがいも経済成長も\n②12 つくる責任 つかう責任"
        block = "①8 働きがいも経済成長も\n②12 つくる責任 つかう責任"
        parsed = parse_transcription(question, "解答・解説\n正答…②目標12 持続可能な生産・消費", choice_block=block)
        self.assertEqual(parsed["choiceTextList"][1], "12 つくる責任 つかう責任")
        self.assertEqual(parsed["answer_result_inferred_correct_choice_numbers"], [2])
        self.assertEqual(parsed["sourceAnswerDescription"]["printedAnswerWord"], "目標12 持続可能な生産・消費")

    def test_manual_pair_records_are_distinct_and_reject_foreign_or_incomplete_evidence(self):
        record, value = self.evidence()
        del value["receipt"]
        value.update(reviewMethod="manual_visual_pair", manualReview=value["binding"] | {
            "reviewer": "actual image reviewer", "reviewedAt": "2026-10-26T00:00:00Z",
        })
        validate_layout(value, record, self.question, self.explanation)
        for mutation in ("foreign", "missing_reviewer", "unconfirmed_target", "unknown_method"):
            changed = copy.deepcopy(value)
            if mutation == "foreign": changed["manualReview"]["explanationImageSha256"] = "foreign"
            elif mutation == "missing_reviewer": changed["manualReview"]["reviewer"] = ""
            elif mutation == "unconfirmed_target": changed["candidate"]["checks"]["answerTarget"] = False
            else: changed["reviewMethod"] = "automatic_guess"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_layout(changed, record, self.question, self.explanation)

    def test_manual_save_retains_previous_receipt_and_requires_witnessed_hashes(self):
        record, old = self.evidence()
        record["sourceQuestionId"] = "https://example.test/mondai1_1.html"
        checks = {"allChoices": True, "onlyChoiceRegion": True, "answerTarget": True}
        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.scrape.knoun_source_layout.reviewed_pair",
            return_value=(record, self.question, self.explanation),
        ):
            root = Path(directory)
            from scripts.scrape.knoun_images import atomic_json
            path = root / "source_layouts/knoun-2026-mock-1/mondai1_1.json"
            atomic_json(path, old)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                save_manual_layout(root, 1, record["sourceQuestionId"], question_hash="foreign", explanation_hash="a",
                                   choice_block=self.block, reviewer="reviewer", checks=checks)
            self.assertEqual(path.read_bytes(), before)
            save_manual_layout(root, 1, record["sourceQuestionId"], question_hash="q", explanation_hash="a",
                               choice_block=self.block, reviewer="reviewer", checks=checks)
            current = load_layout(root, 1, record, self.question, self.explanation)
            self.assertEqual(current["reviewMethod"], "manual_visual_pair")
            self.assertNotIn("receipt", current)
            backups = list((path.parent / "attempts").glob("*_prior_*.json"))
            self.assertEqual(len(backups), 1)
            import json
            self.assertEqual(json.loads(backups[0].read_text()), old)


if __name__ == "__main__":
    unittest.main()
