from __future__ import annotations

import unittest

from scripts.merge.patch_views import infer_question_intent_from_text


class InferQuestionIntentFromTextTest(unittest.TestCase):
    def test_incorrect_statement_prompt_words_map_to_select_incorrect(self) -> None:
        text = "建設業法に関する次の記述のうち、誤っているものはどれか。"
        self.assertEqual(infer_question_intent_from_text(text), "select_incorrect")

    def test_not_applicable_choice_stays_select_correct(self) -> None:
        text = (
            "公共工事標準請負契約約款上、該当しないものは次のうちどれか。"
        )
        self.assertEqual(infer_question_intent_from_text(text), "select_correct")

    def test_not_subject_to_rule_stays_select_correct(self) -> None:
        text = (
            "騒音規制法上、特定建設作業の対象とならない作業は、次のうちどれか。"
        )
        self.assertEqual(infer_question_intent_from_text(text), "select_correct")

    def test_not_used_equipment_stays_select_correct(self) -> None:
        text = "アースドリル工法の施工において、使用しない機材は次のうちどれか。"
        self.assertEqual(infer_question_intent_from_text(text), "select_correct")


if __name__ == "__main__":
    unittest.main()
