import unittest

from scripts.common.question_answer_scope import question_answer_scope, question_body_for_answer


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
