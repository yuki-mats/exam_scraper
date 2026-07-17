import unittest

from tools.question_review_console.question_work_queue import (
    build_question_executions,
    queue_summary,
    recover_interrupted_executions,
    resume_plan,
    specialize_question_plan,
)


def target(question_id: str, index: int) -> dict:
    return {
        "id": question_id,
        "uiQuestionId": question_id,
        "questionKey": f"sample:2026:q{index}",
        "sourceQuestionKey": f"sample:2026:q{index}",
        "reviewQuestionId": f"review-{index}",
        "sourceRecordRef": f"source.json#{index - 1}",
        "listGroupId": "2026",
        "displayLabel": f"問{index}",
        "displayOrder": index,
        "stateHash": f"state-{index}",
        "aliases": [question_id, f"source.json#{index - 1}"],
    }


def stage_plan(stage_id: str, targets: list[dict]) -> dict:
    groups = [list(value["aliases"]) for value in targets]
    source_path = "output/sample/questions_json/2026/00_source/source.json"
    patch_path = (
        f"output/sample/questions_json/2026/21_explanationText_added/"
        f"source_{stage_id}.json"
    )
    return {
        "qualification": "sample",
        "stageId": stage_id,
        "stageIds": [stage_id],
        "stageCode": stage_id,
        "stageLabel": stage_id,
        "kind": "human",
        "mode": "group_refresh",
        "modeLabel": "2026を全件洗い替え",
        "targetCount": len(targets),
        "targetGroupIds": ["2026"],
        "scopeListGroupIds": ["2026"],
        "progressTargets": targets,
        "targetQuestionKeys": [value["id"] for value in targets],
        "targetRecordBindings": [
            {
                "uiQuestionId": value["id"],
                "sourceQuestionKey": value["sourceQuestionKey"],
                "reviewQuestionId": value["reviewQuestionId"],
                "sourceRecordRef": value["sourceRecordRef"],
                "aliases": value["aliases"],
            }
            for value in targets
        ],
        "targetRecordAliasGroups": groups,
        "targetSourceRecordScopes": {source_path: groups},
        "targetRecordScopes": {patch_path: groups},
        "sourceFiles": [source_path],
        "outputFiles": [patch_path],
        "allowedPatchFiles": [patch_path],
        "allowedWriteFiles": [],
        "policyVersions": {stage_id: "1.0"},
        "policyFingerprints": {stage_id: f"policy-{stage_id}"},
        "policyTargets": {stage_id: [value["id"] for value in targets]},
    }


class QuestionWorkQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.targets = [target("q1", 1), target("q2", 2)]
        self.first = stage_plan("explanation", self.targets)
        self.second = stage_plan("law_audit", self.targets)
        self.plan = {
            **self.first,
            "stageId": "multi",
            "stageIds": ["explanation", "law_audit"],
            "stagePlans": [self.first, self.second],
            "targetCount": 2,
            "workItemCount": 4,
            "policyTargets": {
                "explanation": ["q1", "q2"],
                "law_audit": ["q1", "q2"],
            },
        }

    def test_builds_exact_question_and_stage_work_items(self) -> None:
        executions = build_question_executions(self.plan)

        self.assertEqual([value["questionId"] for value in executions], ["q1", "q2"])
        self.assertEqual(
            [stage["stageId"] for stage in executions[0]["stages"]],
            ["explanation", "law_audit"],
        )
        self.assertEqual(len({stage["workItemKey"] for item in executions for stage in item["stages"]}), 4)
        self.assertEqual(queue_summary(executions)["workItemCount"], 4)

    def test_specializes_writable_record_scope_to_one_question(self) -> None:
        plan = specialize_question_plan(self.first, "q2")

        self.assertEqual(plan["targetCount"], 1)
        self.assertEqual(plan["progressTargets"][0]["id"], "q2")
        self.assertEqual(plan["policyTargets"], {"explanation": ["q2"]})
        self.assertEqual(
            next(iter(plan["targetRecordScopes"].values())),
            [["q2", "source.json#1"]],
        )

    def test_restart_keeps_validated_and_requeues_only_safe_preparation(self) -> None:
        executions = build_question_executions(self.plan)
        executions[0]["stages"][0]["status"] = "validated"
        executions[0]["stages"][1]["status"] = "committing"
        executions[1]["stages"][0]["status"] = "preparing"

        recovered = recover_interrupted_executions(executions)

        self.assertEqual(recovered[0]["stages"][0]["status"], "validated")
        self.assertEqual(recovered[0]["stages"][1]["status"], "blocked")
        self.assertEqual(recovered[1]["stages"][0]["status"], "queued")

    def test_restart_blocks_only_dependants_of_uncommitted_stage(self) -> None:
        executions = build_question_executions(self.plan)
        executions[0]["stages"][0]["status"] = "prepared"

        recovered = recover_interrupted_executions(executions)

        self.assertEqual(
            [stage["status"] for stage in recovered[0]["stages"]],
            ["blocked", "blocked"],
        )
        self.assertIn(
            "前工程 explanation の停止",
            recovered[0]["stages"][1]["error"],
        )
        self.assertEqual(
            [stage["status"] for stage in recovered[1]["stages"]],
            ["queued", "queued"],
        )

    def test_resume_plan_excludes_validated_work_items(self) -> None:
        executions = build_question_executions(self.plan)
        for stage in executions[0]["stages"]:
            stage["status"] = "validated"
        executions[1]["stages"][0]["status"] = "validated"
        executions[1]["stages"][1]["status"] = "blocked"

        resumed = resume_plan(self.plan, executions)

        self.assertEqual(resumed["targetCount"], 1)
        self.assertEqual(resumed["workItemCount"], 1)
        self.assertEqual(resumed["progressTargets"][0]["id"], "q2")
        self.assertEqual(
            [stage["stageId"] for stage in resumed["stagePlans"]],
            ["law_audit"],
        )
        self.assertEqual(resumed["policyTargets"], {"law_audit": ["q2"]})

    def test_resume_plan_requeues_validated_item_when_input_changed(self) -> None:
        executions = build_question_executions(self.plan)
        for question in executions:
            for stage in question["stages"]:
                stage["status"] = "validated"
        changed = {**self.first, "progressTargets": [dict(self.targets[0])]}
        changed["progressTargets"][0]["stateHash"] = "changed-state"
        changed["targetCount"] = 1

        resumed = resume_plan(changed, executions)

        self.assertEqual(resumed["targetCount"], 1)
        self.assertEqual(resumed["progressTargets"][0]["id"], "q1")


if __name__ == "__main__":
    unittest.main()
