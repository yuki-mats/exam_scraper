import copy
import ast
import json
import unittest
from pathlib import Path

from scripts.convert.convert_merged_to_firestore import convert_question_to_firestore
from scripts.merge.patch_views import PatchArtifactEntry
from scripts.merge.record_projection import project_merge_record
from scripts.pipeline.repair_gas_shunin_num_choice_sources import repair_question
from tools.question_review_console.question_candidate import (
    QuestionCandidateError,
    candidate_targets,
    parse_model_candidate_v3,
)


class QuestionTypeStageOwnershipTests(unittest.TestCase):
    def test_source_scrapers_do_not_emit_a_guessed_question_type(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("code.py", "scrape_gassyunin.py"):
            tree = ast.parse((root / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    self.assertFalse(any(isinstance(key, ast.Constant) and key.value == "questionType" for key in node.keys), filename)

    def test_source_choice_repair_does_not_classify_answer_operation(self):
        html = """<h2>問3</h2><div class="num-choice-box">
        <div><strong>1</strong>独立した記述A</div>
        <div><strong>2</strong>独立した記述B</div></div>"""
        for question_type in (None, "true_false", "flash_card", "group_choice"):
            with self.subTest(question_type=question_type):
                source = {
                    "question_url": "https://gassyunin.com/example#law-q3",
                    "choiceTextList": [],
                    "answer_result_text": "正解は 2 です。",
                }
                if question_type is not None:
                    source["questionType"] = question_type
                before = copy.deepcopy(source)
                repaired, receipt = repair_question(source, html)
                self.assertIsNotNone(receipt)
                self.assertEqual(len(repaired["choiceTextList"]), 2)
                self.assertEqual(repaired.get("questionType"), question_type)
                self.assertEqual("questionType" in repaired, "questionType" in source)
                self.assertEqual(source, before)

    def test_downstream_candidates_cannot_set_or_unset_question_type(self):
        paths = {
            "question_intent": "15_correctChoiceText_fixed",
            "correct_choice": "23_correctChoiceText_fixed",
            "law_context": "18_law_context_prepared",
            "explanation": "21_explanationText_added",
            "question_set": "22_questionSetId_linked",
            "law_audit": "21_explanationText_added",
        }
        # Even a broad legacy plan must resolve targets for this stage only.
        plan = {"allowedPatchFiles": [
            f"output/sample/questions_json/2026/{directory}/q.json"
            for directory in ["10_questionType_fixed", *paths.values()]
        ], "allowedWriteFiles": ["output/sample/review/law_revision_audit/q.jsonl"]}
        for stage_id in paths:
            targets = candidate_targets("q1", stage_id, plan)
            self.assertTrue(targets)
            self.assertTrue(all("questionType" not in t.allowed_fields for t in targets))
            for update in (
                {"setFields": [{"field": "questionType", "value": "group_choice"}], "unsetFields": []},
                {"setFields": [], "unsetFields": ["questionType"]},
            ):
                with self.subTest(stage=stage_id, update=update):
                    with self.assertRaises(QuestionCandidateError):
                        parse_model_candidate_v3(
                            json.dumps({"decision": "candidate", "summary": "変更", "update": update}),
                            ["q1"],
                            {"q1": targets},
                        )

    def test_only_stage_01_controls_type_through_merge_and_conversion(self):
        source = {
            "original_question_id": "q1",
            "questionBodyText": "次の記述のうち、正しいものはどれか。",
            "choiceTextList": ["独立した記述A", "独立した記述B"],
            "correctChoiceText": ["間違い", "正しい"],
            "answer_result_text": "正解は 2 です。",
            "questionIntent": "select_correct",
            "questionType": "group_choice",
            "examYear": 2026,
        }
        def patch(stage, **values):
            return (PatchArtifactEntry(Path(f"{stage}/q.json"), {
                "original_question_id": "q1", **values,
            }),)

        for decided_type in ("true_false", "group_choice", "flash_card"):
            with self.subTest(decided_type=decided_type):
                opposite = "group_choice" if decided_type == "true_false" else "true_false"
                projection = project_merge_record(
                    source,
                    question_type=patch("10_questionType_fixed", questionType=decided_type),
                    intent_fallback=patch("15_correctChoiceText_fixed", questionType=opposite, questionIntent="select_correct"),
                    strict_correct=patch("23_correctChoiceText_fixed", questionType=opposite, correctChoiceText=["間違い", "正しい"]),
                    law_context=patch("18_law_context_prepared", questionType=opposite, isLawRelated=False),
                    explanation=patch("21_explanationText_added", questionType=opposite, explanationText=["間違い。", "正しい。"] if decided_type == "true_false" else ["問題全体の解説。"]),
                    question_set=patch("22_questionSetId_linked", questionType=opposite, questionSetId="sample-set"),
                )
                self.assertFalse(projection.errors)
                self.assertEqual(projection.merged1["questionType"], decided_type)
                self.assertEqual(projection.merged2["questionType"], decided_type)
                documents = convert_question_to_firestore(projection.merged2)
                self.assertEqual(len(documents), 2)
                self.assertEqual({d["questionType"] for d in documents}, {decided_type})
                self.assertEqual(sum(not d["isChoiceOnly"] for d in documents), 2 if decided_type == "true_false" else 1)
        self.assertEqual(source["questionType"], "group_choice")


if __name__ == "__main__":
    unittest.main()
