import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.check.check_correct_choice_patch_coverage import check_pair
from scripts.common.question_identity import source_question_key


class OriginalizedCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "output" / "eco" / "questions_json" / "mock"
        self.source_path = root / "00_source" / "question_1.json"
        self.source_path.parent.mkdir(parents=True)
        self.orig_path = root / "05.json"
        self.type_path = root / "10.json"
        self.patch_path = root / "23.json"
        choices = [f"選択肢{i}" for i in range(1, 16)]
        self.source = {"original_question_id": "q1", "public_question_id": "q1",
                       "question_url": "https://example.org/q1", "questionBodyText": "元問題",
                       "choiceTextList": choices, "sourceUniqueKeys": [f"source:s{i:02d}" for i in range(1, 16)], "answer_result_text": "正答…④選択肢4"}
        self.binding = {"sourceQuestionKey": source_question_key("eco", "mock", self.source),
                        "reviewQuestionId": "q1", "sourceRecordRef": "question_1.json#0"}
        self.original = {**self.binding, "original_question_id": "q1", "questionBodyText": "独自問題の答えはどれか。",
                         "choiceTextList": choices, "correctChoiceText": ["間違い"] * 3 + ["正しい"] + ["間違い"] * 11,
                         "questionIntent": "select_correct", "sourceUniqueKeys": self.source["sourceUniqueKeys"].copy(), "answer_result_text": "正答は④選択肢4です。"}
        self.qtype = {**self.binding, "original_question_id": "q1", "questionType": "flash_card",
                      "isCalculationQuestion": False, "questionBodyText": "旧単一命題",
                      "choiceTextList": ["旧単一肢"]}
        self.correct = {**self.binding, "original_question_id": "q1",
                        "correctChoiceText": copy.deepcopy(self.original["correctChoiceText"])}
        self.save()

    def save(self):
        for path, data in [(self.source_path, {"question_bodies": [self.source]}),
                           (self.orig_path, [self.original]), (self.type_path, [self.qtype]),
                           (self.patch_path, [self.correct])]:
            path.write_text(json.dumps(data, ensure_ascii=False))

    def check(self):
        self.save()
        return check_pair(self.source_path, self.patch_path, True, False, False,
                          question_type_patch_path=self.type_path, originalized_patch_path=self.orig_path)

    def test_05_has_priority_over_stale_10_content(self):
        self.assertEqual(self.check(), 0)

    def test_fifteen_choices_with_single_verdict_fails(self):
        self.correct["correctChoiceText"] = ["正しい"]
        self.assertEqual(self.check(), 1)

    def test_another_questions_answer_text_fails(self):
        self.correct["correctChoiceText"] = "別問題の選択肢"
        self.assertEqual(self.check(), 1)

    def test_conflicting_answer_against_originalized_fails(self):
        self.correct["correctChoiceText"] = ["正しい"] + ["間違い"] * 14
        self.assertEqual(self.check(), 1)

    def test_invalid_originalized_choice_count_fails(self):
        self.original["choiceTextList"] = ["1肢に縮退"]
        self.assertEqual(self.check(), 1)

    def test_source_binding_mismatch_fails_for_each_stage(self):
        for record in (self.original, self.qtype, self.correct):
            with self.subTest(record=record):
                record["sourceRecordRef"] = "other.json#0"
                self.assertEqual(self.check(), 1)
                record["sourceRecordRef"] = self.binding["sourceRecordRef"]

    def test_legacy_mode_still_accepts_raw_source_and_type(self):
        self.source["choiceTextList"] = ["旧単一肢"]
        self.source["answer_result_text"] = ""
        self.correct["correctChoiceText"] = ["正しい"]
        self.save()
        self.assertEqual(check_pair(self.source_path, self.patch_path, True, False, False,
                                    question_type_patch_path=self.type_path), 0)

    def test_each_patch_rejects_contradictory_original_id(self):
        for record in (self.original, self.qtype, self.correct):
            with self.subTest(record=record):
                record["original_question_id"] = "alien"
                self.assertEqual(self.check(), 1)
                record["original_question_id"] = "q1"

    def test_incomplete_binding_with_wrong_key_does_not_fallback(self):
        self.correct.pop("sourceRecordRef")
        self.correct["sourceQuestionKey"] = "eco:mock:alien"
        self.assertEqual(self.check(), 1)

    def test_alien_and_reordered_keys_are_rejected(self):
        self.original["sourceUniqueKeys"] = ["alien"] * 15
        self.assertEqual(self.check(), 1)
        self.original["sourceUniqueKeys"] = list(reversed(self.source["sourceUniqueKeys"]))
        self.assertEqual(self.check(), 1)

    def test_other_stages_reject_alien_or_reordered_keys(self):
        for record in (self.qtype, self.correct):
            with self.subTest(record=record):
                record["sourceUniqueKeys"] = list(reversed(self.source["sourceUniqueKeys"]))
                self.assertEqual(self.check(), 1)
                record["sourceUniqueKeys"] = self.source["sourceUniqueKeys"].copy()
                self.assertEqual(self.check(), 0)
                record.pop("sourceUniqueKeys")

    def test_source_record_ref_and_duplicate_keys_are_rejected(self):
        self.source["sourceRecordRef"] = "foreign.json#0"
        self.assertEqual(self.check(), 1)
        self.source.pop("sourceRecordRef")
        self.source["sourceUniqueKeys"] = ["duplicate"] * 15
        self.original["sourceUniqueKeys"] = ["duplicate"] * 15
        self.assertEqual(self.check(), 1)

    def test_canonical_projected_keys_are_valid(self):
        self.original["sourceUniqueKeys"] = [f"q1:choice:{i}" for i in range(1, 16)]
        self.assertEqual(self.check(), 0)

    def test_fully_unspecified_legacy_binding_is_supported(self):
        for record in (self.original, self.qtype, self.correct):
            for key in self.binding:
                record.pop(key)
        self.assertEqual(self.check(), 0)

    def test_each_patch_rejects_other_question_url(self):
        for record in (self.original, self.qtype, self.correct):
            with self.subTest(record=record):
                record["question_url"] = "https://example.org/other"
                self.assertEqual(self.check(), 1)
                record["question_url"] = self.source["question_url"]
                self.assertEqual(self.check(), 0)
                record.pop("question_url")

    def test_source_public_id_can_be_filled_from_source_public_id(self):
        self.original["source_public_question_id"] = "q1"
        self.assertEqual(self.check(), 0)
        self.original["source_public_question_id"] = "other"
        self.assertEqual(self.check(), 1)
        self.source["source_public_question_id"] = "source-id"
        self.original["source_public_question_id"] = "source-id"
        self.assertEqual(self.check(), 0)

    def test_missing_source_choice_is_rejected_even_if_labels_match(self):
        self.original["choiceTextList"] = self.original["choiceTextList"][:14]
        self.original["correctChoiceText"] = self.original["correctChoiceText"][:14]
        self.correct["correctChoiceText"] = self.correct["correctChoiceText"][:14]
        self.assertEqual(self.check(), 1)

    def test_source_true_false_response_labels_may_form_single_statement(self):
        self.source["choiceTextList"] = ["正しい", "誤っている"]
        self.source["sourceUniqueKeys"] = ["source:s01", "source:s02"]
        self.source["answer_result_text"] = "正しい"
        self.original["choiceTextList"] = ["独自の命題"]
        self.original["sourceUniqueKeys"] = self.source["sourceUniqueKeys"].copy()
        self.original["correctChoiceText"] = ["正しい"]
        self.original["answer_result_text"] = "正しい"
        self.correct["correctChoiceText"] = ["正しい"]
        self.assertEqual(self.check(), 0)
        self.source["choiceTextList"] = ["実質的な選択肢A", "実質的な選択肢B"]
        self.assertEqual(self.check(), 1)

    def test_source_response_label_pairs_and_reversed_order(self):
        # Literal labels observed in 00_source: futsumenkyo/6001/question_6001_3,
        # kamotsu/43009/question_43009_2, fp2/62012/question_62012_4,
        # fp3/18018/question_18018_2. No runtime dependency on local output.
        pairs = [("正しい", "正しくない"), ("適", "不適"), ("○", "×"), ("◯", "✕")]
        self.source["sourceUniqueKeys"] = ["source:s01", "source:s02"]
        self.source["answer_result_text"] = "正しい"
        self.original["choiceTextList"] = ["独自の命題"]
        self.original["sourceUniqueKeys"] = self.source["sourceUniqueKeys"].copy()
        self.original["correctChoiceText"] = ["正しい"]
        self.original["answer_result_text"] = "正しい"
        self.correct["correctChoiceText"] = ["正しい"]
        for pair in pairs:
            for labels in (list(pair), list(reversed(pair))):
                with self.subTest(labels=labels):
                    self.source["choiceTextList"] = labels
                    self.assertEqual(self.check(), 0)
        for labels in (["正しい", "適"], ["○", "○"], ["適", "実質選択肢"], ["正しくない", "不適"]):
            with self.subTest(labels=labels):
                self.source["choiceTextList"] = labels
                self.assertEqual(self.check(), 1)

    def test_missing_05_binding_for_23_fails(self):
        self.save()
        self.orig_path.write_text("[]")
        self.assertEqual(check_pair(self.source_path, self.patch_path, True, False, False,
                                    question_type_patch_path=self.type_path,
                                    originalized_patch_path=self.orig_path), 1)


if __name__ == "__main__":
    unittest.main()
