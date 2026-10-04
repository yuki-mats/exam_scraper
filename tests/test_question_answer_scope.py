import unittest

from scripts.common.question_answer_scope import question_answer_scope, question_body_for_answer, unordered_answer_evidence
from scripts.common.question_answer_contract import official_answer_alignment_issue


class QuestionAnswerScopeTests(unittest.TestCase):
    def record(self):
        url = "https://www.sg-siken.com/kakomon/28_haru/pm01.html"
        return {
            "questionBodyText": "共通事例。本文中のa，bに入れる字句を選べ。",
            "questionLabel": "午後問1 設問1 (1) a",
            "question_url": url,
            "source_question_id": f"201601:pm1:setumon1:1:a:{url}",
        }

    def test_named_answer_unit_is_bound_to_source_identity(self):
        record = self.record()
        before = dict(record)
        self.assertEqual(question_answer_scope(record)["answerTarget"], {
            "kind": "named_answer_slot", "marker": "a"
        })
        self.assertEqual(question_body_for_answer(record), record["questionBodyText"] + "\n\n解答対象：a")
        self.assertEqual(record, before)

    def test_mismatched_label_or_url_is_not_guessed(self):
        for update in ({"questionLabel": "午後問1 設問1 (1) b"}, {"question_url": "https://example.com/other"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                question_answer_scope({**self.record(), **update})

    def test_main_or_unbound_label_does_not_invent_a_target(self):
        record = self.record()
        for source in (record["source_question_id"].replace(":a:", ":main:"), "unstructured-source-id"):
            changed = {**record, "source_question_id": source}
            self.assertNotIn("answerTarget", question_answer_scope(changed))
            self.assertEqual(question_body_for_answer(changed), record["questionBodyText"])

    def test_display_annotation_is_idempotent(self):
        record = self.record()
        body = question_body_for_answer(record)
        self.assertEqual(question_body_for_answer(record, body_text=body), body)

    def unordered_record(self):
        return {**self.record(), "choiceTextList": ["回避", "共有", "低減", "保有"],
                "questionType": "true_false", "questionIntent": "select_correct",
                "correctChoiceText": ["間違い", "正しい", "間違い", "正しい"],
                "answer_result_text": "正解は 2 です。",
                "explanation_common_prefix": ["∴a＝イ（順不同）\n　b＝エ（順不同）"]}

    def test_explicit_unordered_lines_preserve_all_acceptable_answers(self):
        record = self.unordered_record()
        before = dict(record)
        evidence = unordered_answer_evidence(record)
        self.assertEqual(evidence["acceptedChoiceNumbers"], [2, 4])
        self.assertEqual(evidence["acceptedChoiceTexts"], ["共有", "保有"])
        self.assertIsNone(official_answer_alignment_issue(record))
        self.assertEqual(record, before)
        body = question_body_for_answer(record)
        self.assertTrue(body.endswith("解答対象：a（a・bは順不同）"))
        self.assertEqual(question_body_for_answer(record, body_text=body), body)

    def test_unordered_prose_names_positions_and_labels(self):
        record = {**self.unordered_record(), "choiceTextList": list("1234567"),
                  "explanation_common_prefix": ["したがって、a、bに入るのは「ウ」と「キ」です（順不同）。"]}
        self.assertEqual(unordered_answer_evidence(record)["acceptedChoiceNumbers"], [3, 7])

    def test_other_marker_does_not_inherit_unordered_answers(self):
        record = self.unordered_record()
        record["source_question_id"] = record["source_question_id"].replace(":a:", ":c:")
        record["questionLabel"] = "午後問1 設問1 (1) c"
        self.assertIsNone(unordered_answer_evidence(record))

    def test_answer_example_or_independent_verdict_disagreement_is_rejected(self):
        record = self.unordered_record()
        self.assertIsNotNone(official_answer_alignment_issue({**record, "answer_result_text": "正解は 1 です。"}))
        self.assertIsNotNone(official_answer_alignment_issue({**record, "correctChoiceText": ["間違い", "正しい", "間違い", "間違い"]}))

    def test_ambiguous_unordered_groups_or_missing_choice_are_rejected(self):
        record = self.unordered_record()
        for changed in ({"choiceTextList": ["一つだけ"]},
                        {"explanation_common_prefix": [record["explanation_common_prefix"][0] + "\n別の範囲\na＝ア（順不同）\nc＝ウ（順不同）"]}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                unordered_answer_evidence({**record, **changed})

    def test_ordinary_example_does_not_expand_answer_scope(self):
        record = {**self.unordered_record(), "explanation_common_prefix": ["a＝イ\nb＝エ"]}
        self.assertIsNone(unordered_answer_evidence(record))
