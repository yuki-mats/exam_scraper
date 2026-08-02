import json
import unittest
from types import SimpleNamespace

from tools.question_review_console.maintenance_instruction import (
    MaintenanceInstructionError,
    MaintenanceInstructionInterpreter,
    selectable_instruction_targets,
)


def workflow(*, category_ready=True):
    return {
        "stages": [
            {
                "id": "category_setup",
                "status": "ready" if category_ready else "waiting",
            },
            {
                "id": "correct_choice",
                "code": "02a",
                "label": "正答精査",
                "purpose": "正誤を確定する",
                "kind": "human",
                "batchSelectable": True,
                "supportsGroupScope": True,
                "updateTargets": [
                    {
                        "selectionId": "correct_choice.correct_answer",
                        "label": "正答",
                        "fields": ["correctChoiceText"],
                        "instructionAliases": ["答え", "正誤"],
                    }
                ],
            },
            {
                "id": "explanation",
                "code": "03",
                "label": "解説",
                "purpose": "分類と解説を整える",
                "kind": "human",
                "batchSelectable": True,
                "supportsGroupScope": True,
                "updateTargets": [
                    {
                        "selectionId": "explanation.learning_pattern",
                        "label": "問題の学び方",
                        "fields": ["questionLearningPatternId"],
                        "instructionAliases": ["学び方分類", "問題分類", "分類"],
                    },
                    {
                        "selectionId": "explanation.basic_explanation",
                        "label": "基本解説",
                        "fields": ["explanationText"],
                        "instructionAliases": ["解説"],
                    },
                    {
                        "selectionId": "explanation.supplementary_questions",
                        "label": "補足質問と回答",
                        "fields": ["suggestedQuestionDetailsByChoice"],
                        "instructionAliases": ["補足質問", "補足解説"],
                    },
                ],
            },
            {
                "id": "question_set",
                "code": "04",
                "label": "問題集",
                "purpose": "問題集に割り当てる",
                "kind": "human",
                "batchSelectable": True,
                "supportsGroupScope": True,
                "updateTargets": [
                    {
                        "selectionId": "question_set.question_set",
                        "label": "問題集割当",
                        "fields": ["questionSetId"],
                        "instructionAliases": ["問題集分類"],
                    }
                ],
            },
        ]
    }


class NeverCalledAppServer:
    def run_turn(self, *_args, **_kwargs):
        raise AssertionError("カタログで解決できる指示でmodelを呼ばない")


class FakeAppServer:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def run_turn(self, prompt, **options):
        self.calls.append((prompt, options))
        return SimpleNamespace(
            final_message=json.dumps(self.payload, ensure_ascii=False),
            changed_files=(),
        )


class MaintenanceInstructionTests(unittest.TestCase):
    def test_classification_only_rerun_resolves_without_model(self):
        result = MaintenanceInstructionInterpreter(NeverCalledAppServer()).interpret(
            qualification="sample",
            instruction="分類だけ再実行して",
            workflow=workflow(),
            current_mode="needed",
        )

        self.assertTrue(result["canApply"])
        self.assertEqual(result["resolvedBy"], "catalog")
        self.assertEqual(
            result["selectedUpdateTargetIds"],
            ["explanation.learning_pattern"],
        )
        self.assertEqual(result["selectedStageIds"], ["explanation"])
        self.assertEqual(result["mode"], "group_refresh")

    def test_normal_flow_for_needed_questions_selects_every_available_target(self):
        result = MaintenanceInstructionInterpreter(NeverCalledAppServer()).interpret(
            qualification="sample",
            instruction="通常フローで整備が必要な問題だけやって",
            workflow=workflow(),
            current_mode="group_refresh",
        )

        self.assertEqual(
            result["selectedUpdateTargetIds"],
            [
                "correct_choice.correct_answer",
                "explanation.learning_pattern",
                "explanation.basic_explanation",
                "explanation.supplementary_questions",
                "question_set.question_set",
            ],
        )
        self.assertEqual(result["mode"], "needed")

    def test_specific_alias_does_not_select_generic_alias_contained_in_it(self):
        result = MaintenanceInstructionInterpreter(NeverCalledAppServer()).interpret(
            qualification="sample",
            instruction="補足解説だけ再実行して",
            workflow=workflow(),
        )

        self.assertEqual(
            result["selectedUpdateTargetIds"],
            ["explanation.supplementary_questions"],
        )

    def test_multiple_specific_aliases_can_be_selected_together(self):
        result = MaintenanceInstructionInterpreter(NeverCalledAppServer()).interpret(
            qualification="sample",
            instruction="基本解説と補足解説だけ再実行して",
            workflow=workflow(),
        )

        self.assertEqual(
            result["selectedUpdateTargetIds"],
            [
                "explanation.basic_explanation",
                "explanation.supplementary_questions",
            ],
        )

    def test_exclusive_target_takes_precedence_over_all_target_phrase(self):
        result = MaintenanceInstructionInterpreter(NeverCalledAppServer()).interpret(
            qualification="sample",
            instruction="通常フローのうち分類だけ再実行して",
            workflow=workflow(),
        )

        self.assertEqual(
            result["selectedUpdateTargetIds"],
            ["explanation.learning_pattern"],
        )

    def test_complex_instruction_uses_structured_model_result(self):
        app_server = FakeAppServer(
            {
                "status": "ready",
                "selectedUpdateTargetIds": ["explanation.learning_pattern"],
                "mode": "group_refresh",
                "clarification": "",
            }
        )

        result = MaintenanceInstructionInterpreter(app_server).interpret(
            qualification="sample",
            instruction="解説はやらず、分類だけ全問やり直して",
            workflow=workflow(),
            current_mode="needed",
        )

        self.assertEqual(result["resolvedBy"], "model")
        self.assertEqual(result["selectedUpdateTargetIds"], ["explanation.learning_pattern"])
        prompt, options = app_server.calls[0]
        self.assertIn("解説はやらず", prompt)
        self.assertEqual(options["work_type"], "maintenance_instruction_candidate")
        self.assertEqual(options["sandbox"], "read-only")
        self.assertIn(
            "explanation.learning_pattern",
            options["output_schema"]["properties"]["selectedUpdateTargetIds"]["items"]["enum"],
        )

    def test_ambiguous_model_result_requires_clarification(self):
        app_server = FakeAppServer(
            {
                "status": "needs_clarification",
                "selectedUpdateTargetIds": [],
                "mode": "needed",
                "clarification": "どの整備項目を実行するか指定してください。",
            }
        )

        result = MaintenanceInstructionInterpreter(app_server).interpret(
            qualification="sample",
            instruction="いい感じにして",
            workflow=workflow(),
        )

        self.assertFalse(result["canApply"])
        self.assertEqual(result["selectedUpdateTargetIds"], [])
        self.assertIn("指定", result["clarification"])

    def test_question_set_is_not_selectable_before_category_is_ready(self):
        target_ids = [
            target["selectionId"]
            for target in selectable_instruction_targets(
                workflow(category_ready=False)
            )
        ]

        self.assertNotIn("question_set.question_set", target_ids)

    def test_empty_instruction_is_rejected(self):
        with self.assertRaises(MaintenanceInstructionError):
            MaintenanceInstructionInterpreter(NeverCalledAppServer()).interpret(
                qualification="sample",
                instruction="  ",
                workflow=workflow(),
            )


if __name__ == "__main__":
    unittest.main()
