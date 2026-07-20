import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.qualification_workflow import QualificationWorkflow


class FakeInventory:
    def __init__(self, qualification, groups):
        self.qualification = qualification
        self.groups = {group["listGroupId"]: group for group in groups}

    def inventory(self):
        return {
            "qualifications": [
                {
                    "id": self.qualification,
                    "listGroupIds": list(self.groups),
                    "listGroupCount": len(self.groups),
                }
            ]
        }

    def group(self, qualification, list_group_id):
        if qualification != self.qualification:
            raise FileNotFoundError(qualification)
        return self.groups[list_group_id]


def question(
    *,
    patches=None,
    issues=None,
    law_related=False,
    workflow=None,
    group="2026",
    question_number=1,
):
    original_id = f"sample-{group}-q{question_number}"
    source_path = (
        f"output/sample/questions_json/{group}/00_source/"
        f"question_{group}_{question_number}.json"
    )
    return {
        "id": original_id,
        "reviewKey": (
            f"sample:{group}:question_{group}_{question_number}:{original_id}"
        ),
        "sourceQuestionKey": f"sample:{group}:{original_id}",
        "sourceRecordRef": f"question_{group}_{question_number}.json#0",
        "qualification": "sample",
        "listGroupId": group,
        "sourceStem": f"question_{group}_{question_number}",
        "sourceIndex": 0,
        "originalQuestionId": original_id,
        "questionLabel": f"問{question_number}",
        "source": {
            "original_question_id": original_id,
            "questionLabel": f"問{question_number}",
        },
        "paths": {
            "source": source_path,
            "patches": list(patches or []),
        },
        "issues": list(issues or []),
        "issueCodes": [issue["code"] for issue in issues or []],
        "isLawRelated": law_related,
        "projected": {
            "originalQuestionId": original_id,
            "isLawRelated": law_related,
            "lawRevisionFacts": {"auditStatus": "same_as_current"}
            if law_related
            else None,
        },
        "workflow": dict(
            workflow or {"merge": "missing", "convert": "missing", "upload": "missing"}
        ),
    }


def write_category(root: Path, qualification: str = "sample") -> None:
    path = root / "output" / qualification / "category" / "category.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "folders": [{"folderId": "sample_f01"}],
                "questionSets": [
                    {"questionSetId": "sample_qs01", "folderId": "sample_f01"}
                ],
            }
        ),
        encoding="utf-8",
    )


def mark_current(workflow, item, stage_ids):
    policies = workflow.versioned_policies("sample")
    for stage_id in stage_ids:
        workflow.work_versions.record_stage(
            [item],
            policies[stage_id],
            run_id=f"run-{stage_id}",
            source="validated_run",
        )


class QualificationWorkflowTests(unittest.TestCase):
    def test_plan_filters_same_question_range_per_year_and_selected_fields(self):
        groups = [
            {
                "listGroupId": year,
                "questions": [
                    question(group=year, question_number=number)
                    for number in range(1, 6)
                ],
            }
            for year in ("original", "2026")
        ]
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory), FakeInventory("sample", groups)
            )
            plan = workflow.plan(
                "sample",
                "explanation",
                "group_refresh",
                list_group_ids=["original", "2026"],
                update_target_ids=["explanation.supplementary_questions"],
                question_range={"start": 2, "end": 3},
            )
            prompt = workflow.prompt(
                "sample",
                "explanation",
                "group_refresh",
                list_group_ids=["original", "2026"],
                update_target_ids=["explanation.supplementary_questions"],
                question_range={"start": 2, "end": 3},
            )["prompt"]

        self.assertEqual(plan["targetCount"], 4)
        labels_by_group = {}
        for target in plan["progressTargets"]:
            labels_by_group.setdefault(target["listGroupId"], []).append(
                target["questionLabel"]
            )
        self.assertEqual(
            labels_by_group,
            {"2026": ["問2", "問3"], "original": ["問2", "問3"]},
        )
        self.assertTrue(
            all("examYear" not in target for target in plan["progressTargets"])
        )
        self.assertEqual(
            plan["selectedFieldsByStage"],
            {"explanation": ["suggestedQuestionDetailsByChoice"]},
        )
        self.assertIn("explanationText", plan["readFieldsByStage"]["explanation"])
        self.assertEqual(plan["questionRange"], {"start": 2, "end": 3})
        self.assertIn("補足質問と回答", prompt)
        self.assertIn("参照用field", prompt)
        self.assertIn("第2問〜第3問", prompt)

    def test_plan_rejects_update_target_from_another_stage(self):
        group = {"listGroupId": "2026", "questions": [question()]}
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory), FakeInventory("sample", [group])
            )
            with self.assertRaisesRegex(ValueError, "更新項目がありません"):
                workflow.plan(
                    "sample",
                    "question_type",
                    "refresh",
                    update_target_ids=["explanation.supplementary_questions"],
                )

    def test_plan_selects_non_explanation_update_target_generically(self):
        group = {"listGroupId": "original", "questions": [question(group="original")]}
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory), FakeInventory("sample", [group])
            )
            plan = workflow.plan(
                "sample",
                "question_type",
                "group_refresh",
                list_group_ids=["original"],
                update_target_ids=["question_type.calculation_flag"],
            )

        self.assertEqual(
            plan["selectedUpdateTargetIds"],
            ["question_type.calculation_flag"],
        )
        self.assertEqual(
            plan["selectedFieldsByStage"],
            {"question_type": ["isCalculationQuestion"]},
        )
        self.assertEqual(
            plan["readFieldsByStage"],
            {
                "question_type": [
                    "questionType",
                    "questionBodyText",
                    "choiceTextList",
                ]
            },
        )
        self.assertTrue(
            all("examYear" not in target for target in plan["progressTargets"])
        )

    def test_plan_orders_progress_by_source_logical_id_naturally(self):
        items = []
        for unique_number, logical_id, label, category in (
            (10, "sample-subject-a-10", "問10", "科目A"),
            (101, "sample-subject-b-1", "問1", "科目B"),
            (2, "sample-subject-a-2", "問2", "科目A"),
            (1, "sample-subject-a-1", "問1", "科目A"),
        ):
            item = question(question_number=unique_number)
            item["sourceStem"] = "source-file"
            item["sourceIndex"] = 0
            item["questionLabel"] = label
            item["source"].update(
                originalQuestionId=logical_id,
                original_question_id=logical_id,
                questionLabel=label,
                category=category,
            )
            items.append(item)
        group = {"listGroupId": "2026", "questions": items}

        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory("sample", [group]),
            )
            plan = workflow.plan("sample", "question_type", "refresh")

        self.assertEqual(
            [target["displayLabel"] for target in plan["progressTargets"]],
            ["科目A 問1", "科目A 問2", "科目A 問10", "科目B 問1"],
        )
        self.assertEqual(
            [target["displayOrder"] for target in plan["progressTargets"]],
            [1, 2, 3, 4],
        )

    def test_plan_blocks_only_stage_with_unmatched_selected_artifact(self):
        item = question()
        group = {
            "listGroupId": "2026",
            "questions": [item],
            "artifactResolutionBlockers": [
                {
                    "code": "artifact_identity_unmatched",
                    "patchDir": "10_questionType_fixed",
                    "path": "output/sample/questions_json/2026/10_questionType_fixed/orphan.json",
                    "count": 1,
                    "message": "questionTypeのartifact 1件をsource recordへ対応できません。",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory("sample", [group]),
            )
            overview = workflow.overview("sample")

            with self.assertRaisesRegex(ValueError, "source recordへ対応できない"):
                workflow.plan("sample", "question_type", "refresh")
            explanation = workflow.plan("sample", "explanation", "refresh")

        self.assertEqual(explanation["targetCount"], 1)
        stages = {stage["id"]: stage for stage in overview["stages"]}
        self.assertEqual(stages["question_type"]["status"], "attention")
        self.assertEqual(stages["delivery"]["status"], "attention")
        self.assertEqual(
            stages["question_type"]["artifactResolutionBlockers"][0]["count"],
            1,
        )

    def test_overview_counts_unique_required_questions_by_year(self):
        current_question = question(group="2025")
        legacy_question = question(group="2026")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_category(root)
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [
                        {"listGroupId": "2025", "questions": [current_question]},
                        {"listGroupId": "2026", "questions": [legacy_question]},
                    ],
                ),
            )
            policies = workflow.versioned_policies("sample")
            for stage in policies.values():
                workflow.work_versions.record_stage(
                    [current_question],
                    stage,
                    run_id=f"current-{stage['id']}",
                    source="validated_run",
                )
                workflow.work_versions.record_stage(
                    [legacy_question],
                    stage,
                    run_id=None,
                    source="firestore_published_backfill",
                    version="0.0",
                    policy_fingerprint_override="legacy-unknown",
                )

            overview = workflow.overview("sample")

        self.assertEqual(
            overview["summary"]["maintenanceProgress"],
            {"totalCount": 2, "currentCount": 1, "requiredCount": 1},
        )
        by_year = {item["listGroupId"]: item for item in overview["groups"]}
        self.assertEqual(by_year["2025"]["maintenanceProgress"]["requiredCount"], 0)
        self.assertEqual(by_year["2026"]["maintenanceProgress"]["requiredCount"], 1)

    def test_attention_plan_carries_only_target_record_identity_groups(self):
        target = question(
            issues=[{"code": "type_issue", "fields": ["questionType"]}]
        )
        other = question(question_number=2)
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [
                        {
                            "listGroupId": "2026",
                            "questions": [target, other],
                        }
                    ],
                ),
            )

            plan = workflow.plan("sample", "question_type", "attention")

        self.assertEqual(plan["targetCount"], 1)
        self.assertEqual(
            plan["targetRecordAliasGroups"],
            [
                [
                    "question_2026_1.json#0",
                    "sample-2026-q1",
                    "sample:2026:sample-2026-q1",
                ]
            ],
        )

    def test_human_patch_plan_rejects_question_without_strong_identity(self):
        item = question()
        item.pop("id")
        item.pop("sourceQuestionKey")
        item.pop("sourceRecordRef")
        item.pop("originalQuestionId")
        item["source"] = {}
        item["projected"].pop("originalQuestionId")
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [item]}],
                ),
            )

            with self.assertRaisesRegex(ValueError, "一意ID"):
                workflow.plan("sample", "question_type", "refresh")

    def test_source_only_qualification_starts_with_policy_then_stage_01(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = FakeInventory(
                "sample",
                [
                    {
                        "listGroupId": "2026",
                        "questions": [question()],
                    }
                ],
            )
            workflow = QualificationWorkflow(root, inventory)

            initial = workflow.overview("sample")
            policy_dir = root / "prompt" / "qualification_docs" / "sample"
            policy_dir.mkdir(parents=True)
            (policy_dir / "README.md").write_text("# sample\n", encoding="utf-8")
            write_category(root)
            prepared = workflow.overview("sample")
            prompt = workflow.prompt("sample", "question_type")["prompt"]
            originalize = workflow.plan("sample", "originalize", "refresh")

        self.assertEqual(initial["nextStageId"], "setup")
        self.assertEqual(prepared["nextStageId"], "question_type")
        stage = next(item for item in prepared["stages"] if item["id"] == "question_type")
        self.assertEqual(stage["status"], "not_started")
        self.assertEqual(stage["remainingCount"], 1)
        self.assertIn("10_questionType_fixed", stage["outputPreview"][0])
        self.assertIn("prompt/01_prompt_fix_questionType.md", prompt)
        self.assertIn("question_2026_1.json", prompt)
        self.assertNotIn("## 問題文", prompt)
        self.assertEqual(originalize["targetCount"], 1)
        self.assertIn("05_originalized", originalize["outputFiles"][0])

    def test_existing_originalized_patch_opts_question_into_version_tracking(self):
        item = question(
            patches=[
                "output/sample/questions_json/2026/05_originalized/"
                "question_2026_1_originalized.json"
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_dir = root / "prompt" / "qualification_docs" / "sample"
            policy_dir.mkdir(parents=True)
            (policy_dir / "README.md").write_text("# sample\n", encoding="utf-8")
            write_category(root)
            workflow = QualificationWorkflow(
                root,
                FakeInventory("sample", [{"listGroupId": "2026", "questions": [item]}]),
            )

            overview = workflow.overview("sample")

        self.assertEqual(overview["nextStageId"], "originalize")
        stage = next(item for item in overview["stages"] if item["id"] == "originalize")
        self.assertEqual(stage["versionUnrecordedCount"], 1)

    def test_law_issue_precedes_delivery_and_clears_to_delivery(self):
        patches = [
            "output/sample/questions_json/2026/10_questionType_fixed/question_2026_1_questionType_fixed.json",
            "output/sample/questions_json/2026/15_correctChoiceText_fixed/question_2026_1_correctChoiceText_fixed.json",
            "output/sample/questions_json/2026/23_correctChoiceText_fixed/question_2026_1_correctChoiceText_fixed.json",
            "output/sample/questions_json/2026/18_law_context_prepared/question_2026_1_lawContext_prepared.json",
            "output/sample/questions_json/2026/21_explanationText_added/question_2026_1_explanationText_added.json",
            "output/sample/questions_json/2026/22_questionSetId_linked/question_2026_1_questionSetId_linked.json",
        ]
        law_issue = {
            "code": "law_audit_metadata_incomplete",
            "fields": ["lawRevisionFacts.current.correctChoiceText"],
        }
        item = question(patches=patches, issues=[law_issue], law_related=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_dir = root / "prompt" / "qualification_docs" / "sample"
            policy_dir.mkdir(parents=True)
            (policy_dir / "README.md").write_text("# sample\n", encoding="utf-8")
            write_category(root)
            inventory = FakeInventory(
                "sample", [{"listGroupId": "2026", "questions": [item]}]
            )
            workflow = QualificationWorkflow(root, inventory)
            mark_current(
                workflow,
                item,
                [
                    "question_type",
                    "question_intent",
                    "correct_choice",
                    "law_context",
                    "explanation",
                    "law_audit",
                    "question_set",
                ],
            )

            with_issue = workflow.overview("sample")
            retry_plan = workflow.plan("sample", "law_audit", "outdated")
            item["issues"] = []
            item["issueCodes"] = []
            without_issue = workflow.overview("sample")

        self.assertEqual(with_issue["nextStageId"], "law_audit")
        audit = next(item for item in with_issue["stages"] if item["id"] == "law_audit")
        self.assertEqual(audit["remainingCount"], 1)
        self.assertEqual(
            with_issue["summary"]["maintenanceProgress"]["requiredCount"], 1
        )
        self.assertEqual(retry_plan["targetCount"], 1)
        self.assertIn("21_explanationText_added", audit["outputPreview"][0])
        self.assertEqual(
            without_issue["summary"]["maintenanceProgress"]["requiredCount"], 0
        )
        self.assertEqual(without_issue["nextStageId"], "delivery")
        delivery = next(
            item for item in without_issue["stages"] if item["id"] == "delivery"
        )
        self.assertEqual(delivery["targetGroupIds"], ["2026"])

    def test_strict_correct_choice_is_a_separate_stage_before_law_context(self):
        patches = [
            "output/sample/questions_json/2026/10_questionType_fixed/question_2026_1_questionType_fixed.json",
            "output/sample/questions_json/2026/15_correctChoiceText_fixed/question_2026_1_correctChoiceText_fixed.json",
        ]
        item = question(patches=patches)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_dir = root / "prompt" / "qualification_docs" / "sample"
            policy_dir.mkdir(parents=True)
            (policy_dir / "README.md").write_text("# sample\n", encoding="utf-8")
            workflow = QualificationWorkflow(
                root,
                FakeInventory("sample", [{"listGroupId": "2026", "questions": [item]}]),
            )
            mark_current(
                workflow,
                item,
                ["question_type", "question_intent"],
            )

            overview = workflow.overview("sample")
            prompt = workflow.prompt("sample", "correct_choice")["prompt"]

        self.assertEqual(overview["nextStageId"], "correct_choice")
        self.assertLess(
            [stage["id"] for stage in overview["stages"]].index("correct_choice"),
            [stage["id"] for stage in overview["stages"]].index("law_context"),
        )
        self.assertIn("23_correctChoiceText_fixed", prompt)
        self.assertIn("prompt/02a_prompt_review_correctChoiceText.md", prompt)

    def test_law_context_and_explanation_follow_the_merged_filename_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [question()]}],
                ),
            )

            law_context = workflow.plan(
                "sample", "law_context", "refresh"
            )
            explanation = workflow.plan(
                "sample", "explanation", "refresh"
            )

        self.assertEqual(
            law_context["outputFiles"],
            [
                "output/sample/questions_json/2026/18_law_context_prepared/"
                "question_2026_1_merged_lawContext_prepared.json"
            ],
        )
        self.assertEqual(
            explanation["outputFiles"],
            [
                "output/sample/questions_json/2026/21_explanationText_added/"
                "question_2026_1_merged_explanationText_added.json"
            ],
        )

    def test_stage_plan_reuses_the_selected_timestamp_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_root = (
                root
                / "output/sample/questions_json/2026/21_explanationText_added"
            )
            patch_root.mkdir(parents=True)
            fixed = patch_root / "question_2026_1_merged_explanationText_added.json"
            latest = (
                patch_root
                / "question_2026_1_merged_explanationText_added_20260714_1200.json"
            )
            fixed.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [question()]}],
                ),
            )

            plan = workflow.plan("sample", "explanation", "refresh")

        self.assertEqual(plan["outputFiles"], [str(latest.relative_to(root))])

    def test_stage_plan_supports_remaining_attention_and_refresh(self):
        patches = [
            "output/sample/questions_json/2026/10_questionType_fixed/question_2026_1_questionType_fixed.json"
        ]
        issue = {"code": "required_field_missing", "fields": ["questionType"]}
        item = question(patches=patches, issues=[issue])
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample", [{"listGroupId": "2026", "questions": [item]}]
                ),
            )
            remaining = workflow.plan("sample", "question_type", "remaining")
            attention = workflow.plan("sample", "question_type", "attention")
            refresh = workflow.plan("sample", "question_type", "refresh")

        self.assertEqual(remaining["targetCount"], 0)
        self.assertEqual(attention["targetCount"], 1)
        self.assertEqual(refresh["targetCount"], 1)

    def test_outdated_mode_selects_only_questions_below_current_stage_version(self):
        item = question()
        item.update(
            {
                "qualification": "sample",
                "listGroupId": "2026",
                "reviewKey": "sample:2026:question_2026_1:sample-2026-q1",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample", [{"listGroupId": "2026", "questions": [item]}]
                ),
            )
            stage = workflow.versioned_policies("sample")["question_type"]
            unrecorded = workflow.plan("sample", "question_type", "outdated")
            workflow.work_versions.record_stage(
                [item],
                stage,
                run_id=None,
                source="firestore_published_backfill",
                version=0,
                policy_fingerprint_override="legacy-unknown",
            )
            legacy = workflow.plan("sample", "question_type", "outdated")
            workflow.work_versions.record_stage(
                [item], stage, run_id="run-1", source="validated_run"
            )
            current = workflow.plan("sample", "question_type", "outdated")

        self.assertEqual(unrecorded["targetCount"], 1)
        self.assertEqual(legacy["targetCount"], 1)
        self.assertEqual(
            legacy["policyVersions"],
            {"question_type": stage["policyVersion"]},
        )
        self.assertEqual(current["targetCount"], 0)

    def test_group_refresh_targets_only_the_selected_year(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [
                        {"listGroupId": "2025", "questions": [question(group="2025")]},
                        {"listGroupId": "2026", "questions": [question(group="2026")]},
                    ],
                ),
            )
            plan = workflow.plan(
                "sample",
                "question_type",
                "group_refresh",
                list_group_id="2025",
            )

        self.assertEqual(plan["targetCount"], 1)
        self.assertEqual(plan["targetGroupIds"], ["2025"])
        self.assertEqual(plan["modeLabel"], "2025の全問題を再整備")
        self.assertTrue(all("/2025/" in path for path in plan["sourceFiles"]))

    def test_group_refresh_keeps_year_scope_after_qualification_prerequisite(self):
        selected = question(group="2026")
        other = question(group="2025")
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [
                        {"listGroupId": "2025", "questions": [other]},
                        {"listGroupId": "2026", "questions": [selected]},
                    ],
                ),
            )
            prerequisite_plans = {
                stage_id: workflow.plan(
                    "sample",
                    stage_id,
                    "group_refresh",
                    list_group_ids=["2026"],
                )
                for stage_id in ("setup", "category_setup")
            }
            plans = [
                workflow.plan_many(
                    "sample",
                    [scope_stage, question_stage],
                    "group_refresh",
                    list_group_ids=["2026"],
                )
                for scope_stage, question_stage in (
                    ("setup", "question_type"),
                    ("category_setup", "question_set"),
                )
            ]

        for stage_id, prerequisite in prerequisite_plans.items():
            with self.subTest(prerequisite=stage_id):
                self.assertEqual(prerequisite["mode"], "refresh")
                self.assertEqual(prerequisite["scopeListGroupIds"], [])
                self.assertEqual(
                    prerequisite["targetGroupIds"], ["2025", "2026"]
                )
        for plan, scope_stage, question_stage in zip(
            plans,
            ("setup", "category_setup"),
            ("question_type", "question_set"),
        ):
            with self.subTest(scope_stage=scope_stage):
                stages = {
                    stage["stageId"]: stage for stage in plan["stagePlans"]
                }
                self.assertEqual(plan["mode"], "group_refresh")
                self.assertEqual(plan["modeLabel"], "2026の全問題を再整備")
                self.assertEqual(plan["scopeListGroupIds"], ["2026"])
                self.assertEqual(plan["targetGroupIds"], ["2026"])
                self.assertEqual(plan["targetQuestionKeys"], [selected["id"]])
                self.assertEqual(stages[scope_stage]["mode"], "refresh")
                self.assertEqual(stages[scope_stage]["scopeListGroupIds"], [])
                self.assertEqual(
                    stages[scope_stage]["targetGroupIds"], ["2025", "2026"]
                )
                self.assertEqual(stages[question_stage]["mode"], "group_refresh")
                self.assertEqual(
                    stages[question_stage]["scopeListGroupIds"], ["2026"]
                )
                self.assertEqual(stages[question_stage]["targetGroupIds"], ["2026"])

    def test_selected_years_scope_all_modes_and_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [
                        {"listGroupId": "2024", "questions": [question(group="2024")]},
                        {"listGroupId": "2025", "questions": [question(group="2025")]},
                        {"listGroupId": "2026", "questions": [question(group="2026")]},
                    ],
                ),
            )
            selected = ["2024", "2026"]
            remaining = workflow.plan(
                "sample",
                "question_type",
                "remaining",
                list_group_ids=selected,
            )
            refresh = workflow.plan(
                "sample",
                "question_type",
                "group_refresh",
                list_group_ids=selected,
            )
            prompt = workflow.prompt(
                "sample",
                "question_type",
                "remaining",
                list_group_ids=selected,
            )["prompt"]

        self.assertEqual(remaining["targetCount"], 2)
        self.assertEqual(remaining["targetGroupIds"], selected)
        self.assertEqual(remaining["scopeListGroupIds"], selected)
        self.assertEqual(remaining["modeLabel"], "2024・2026の未作業のみ")
        self.assertEqual(refresh["modeLabel"], "2024・2026の全問題を再整備")
        self.assertNotIn("/2025/", "\n".join(remaining["sourceFiles"]))
        self.assertIn("# 選択年度・フォルダの問題整備", prompt)
        self.assertIn("対象listGroupId: `2024`, `2026`", prompt)
        self.assertIn("対象問題: `2問`", prompt)
        self.assertNotIn("対象問題: `2問すべて`", prompt)

    def test_category_setup_blocks_question_set_until_valid_category_exists(self):
        patches = [
            "output/sample/questions_json/2026/10_questionType_fixed/question_2026_1_questionType_fixed.json",
            "output/sample/questions_json/2026/15_correctChoiceText_fixed/question_2026_1_correctChoiceText_fixed.json",
            "output/sample/questions_json/2026/23_correctChoiceText_fixed/question_2026_1_correctChoiceText_fixed.json",
            "output/sample/questions_json/2026/18_law_context_prepared/question_2026_1_lawContext_prepared.json",
            "output/sample/questions_json/2026/21_explanationText_added/question_2026_1_explanationText_added.json",
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_dir = root / "prompt" / "qualification_docs" / "sample"
            policy_dir.mkdir(parents=True)
            (policy_dir / "README.md").write_text("# sample\n", encoding="utf-8")
            item = question(patches=patches)
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [item]}],
                ),
            )
            mark_current(
                workflow,
                item,
                [
                    "question_type",
                    "question_intent",
                    "correct_choice",
                    "law_context",
                    "explanation",
                    "law_audit",
                ],
            )
            before = workflow.overview("sample")
            category_plan = workflow.plan("sample", "category_setup", "remaining")
            with self.assertRaisesRegex(ValueError, "03c カテゴリ設計"):
                workflow.plan("sample", "question_set", "refresh")
            top_plan = workflow.plan_many(
                "sample",
                ["category_setup", "question_set"],
                "outdated",
                list_group_ids=["2026"],
            )
            write_category(root)
            after = workflow.overview("sample")

        self.assertEqual(before["nextStageId"], "category_setup")
        self.assertIn("output/sample/category/category.json", category_plan["outputFiles"])
        self.assertIn(
            "prompt/qualification_docs/sample/03_category_preparation.md",
            category_plan["outputFiles"],
        )
        self.assertEqual(
            [item["stageId"] for item in top_plan["stagePlans"]],
            ["category_setup", "question_set"],
        )
        self.assertEqual(top_plan["scopeListGroupIds"], ["2026"])
        question_set = next(
            stage for stage in before["stages"] if stage["id"] == "question_set"
        )
        self.assertEqual(question_set["status"], "waiting")
        self.assertEqual(after["nextStageId"], "question_set")

    def test_valid_qualification_category_is_not_a_per_question_outdated_stage(self):
        item = question()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_category(root)
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [item]}],
                ),
            )
            current_stage_ids = list(workflow.versioned_policies("sample"))
            mark_current(workflow, item, current_stage_ids)

            overview = workflow.overview("sample")
            plan = workflow.plan("sample", "category_setup", "outdated")

        self.assertEqual(
            overview["summary"]["maintenanceProgress"]["requiredCount"],
            0,
        )
        self.assertEqual(
            overview["summary"]["requiredMaintenance"]["stageIds"],
            [],
        )
        self.assertEqual(plan["targetCount"], 0)
        self.assertEqual(plan["targetQuestionKeys"], [])

    def test_selected_group_plan_does_not_expand_to_qualification_scope(self):
        selected = question(group="2026")
        other = question(group="2025")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_category(root)
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [
                        {"listGroupId": "2025", "questions": [other]},
                        {"listGroupId": "2026", "questions": [selected]},
                    ],
                ),
            )
            policies = list(workflow.versioned_policies("sample"))
            mark_current(workflow, other, policies)
            mark_current(
                workflow,
                selected,
                [stage_id for stage_id in policies if stage_id != "question_type"],
            )

            overview = workflow.overview("sample")
            required = overview["summary"]["requiredMaintenance"]
            plan = workflow.plan_many(
                "sample",
                required["stageIds"],
                required["mode"],
                list_group_ids=["2026"],
            )
        self.assertEqual(required["stageIds"], ["question_type"])
        self.assertEqual(plan["targetCount"], 1)
        self.assertEqual(plan["targetGroupIds"], ["2026"])
        self.assertEqual(plan["scopeListGroupIds"], ["2026"])

    def test_missing_category_prerequisite_does_not_expand_selected_group(self):
        selected = question(group="2026")
        other = question(group="2025")
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [
                        {"listGroupId": "2025", "questions": [other]},
                        {"listGroupId": "2026", "questions": [selected]},
                    ],
                ),
            )
            policies = list(workflow.versioned_policies("sample"))
            mark_current(workflow, other, policies)
            mark_current(workflow, selected, policies)

            overview = workflow.overview("sample")
            required = overview["summary"]["requiredMaintenance"]
            plan = workflow.plan_many(
                "sample",
                required["stageIds"],
                required["mode"],
                list_group_ids=["2026"],
            )
            singular_plan = workflow.plan_many(
                "sample",
                required["stageIds"],
                required["mode"],
                list_group_id="2026",
            )

        self.assertEqual(required["stageIds"], ["category_setup", "question_set"])
        self.assertEqual(plan["targetCount"], 1)
        self.assertEqual(plan["workItemCount"], 1)
        self.assertEqual(plan["targetQuestionKeys"], [selected["id"]])
        self.assertEqual(plan["targetGroupIds"], ["2026"])
        self.assertEqual(plan["scopeListGroupIds"], ["2026"])
        category_plan = next(
            item for item in plan["stagePlans"] if item["stageId"] == "category_setup"
        )
        self.assertEqual(category_plan["targetGroupIds"], ["2025", "2026"])
        self.assertEqual(singular_plan["targetQuestionKeys"], [selected["id"]])
        self.assertEqual(singular_plan["targetGroupIds"], ["2026"])
        self.assertEqual(singular_plan["scopeListGroupIds"], ["2026"])

    def test_non_law_blocking_issue_reopens_current_law_audit(self):
        issue = {
            "code": "law_audit_metadata_incomplete",
            "fields": ["lawRevisionFacts"],
        }
        item = question(law_related=False, issues=[issue])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_category(root)
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [item]}],
                ),
            )
            mark_current(workflow, item, list(workflow.versioned_policies("sample")))

            overview = workflow.overview("sample")
            plan = workflow.plan("sample", "law_audit", "outdated")

        self.assertEqual(
            overview["summary"]["maintenanceProgress"]["requiredCount"], 1
        )
        self.assertEqual(
            overview["summary"]["requiredMaintenance"]["stageIds"],
            ["law_audit"],
        )
        self.assertEqual(plan["targetQuestionKeys"], [item["id"]])

    def test_unclassified_question_without_facts_reopens_current_law_audit(self):
        item = question(law_related=None)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_category(root)
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [item]}],
                ),
            )
            mark_current(workflow, item, list(workflow.versioned_policies("sample")))

            overview = workflow.overview("sample")
            plan = workflow.plan("sample", "law_audit", "outdated")

        self.assertEqual(
            overview["summary"]["requiredMaintenance"]["stageIds"],
            ["law_audit"],
        )
        self.assertEqual(plan["targetQuestionKeys"], [item["id"]])

    def test_required_non_law_audit_version_has_an_executable_outdated_plan(self):
        item = question(law_related=False)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_category(root)
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [item]}],
                ),
            )
            current_stage_ids = [
                stage_id
                for stage_id in workflow.versioned_policies("sample")
                if stage_id != "law_audit"
            ]
            mark_current(workflow, item, current_stage_ids)

            overview = workflow.overview("sample")
            plan = workflow.plan("sample", "law_audit", "outdated")

        self.assertEqual(
            overview["summary"]["requiredMaintenance"]["stageIds"],
            ["law_audit"],
        )
        self.assertEqual(plan["targetCount"], 1)

    def test_invalid_live_catalog_keeps_overview_but_blocks_new_plan(self):
        item = question()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config" / "question_maintenance_workflow.toml"
            config.parent.mkdir(parents=True)
            source = (
                Path(__file__).resolve().parents[1]
                / "config"
                / "question_maintenance_workflow.toml"
            )
            config.write_bytes(source.read_bytes())
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [item]}],
                ),
            )
            workflow.catalog("sample")

            config.write_text("broken = [\n", encoding="utf-8")
            overview = workflow.overview("sample")

            with self.assertRaisesRegex(ValueError, "直前の正常な設定"):
                workflow.plan("sample", "question_type", "outdated")

        self.assertTrue(overview["restartRequired"])
        self.assertIn("直前の正常な設定", overview["catalogWarning"])

    def test_multiple_stages_generate_one_question_at_a_time_prompt(self):
        issue = {
            "code": "law_audit_metadata_incomplete",
            "fields": ["lawRevisionFacts.current.correctChoiceText"],
        }
        with tempfile.TemporaryDirectory() as directory:
            law_question = question(issues=[issue], law_related=True)
            law_question["id"] = "law-question"
            non_law_question = question(law_related=False, question_number=2)
            non_law_question["id"] = "non-law-question"
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [
                        {
                            "listGroupId": "2026",
                            "questions": [law_question, non_law_question],
                        }
                    ],
                ),
            )
            stage_ids = [
                "question_type",
                "law_context",
                "explanation",
                "law_audit",
            ]
            plan = workflow.plan_many(
                "sample",
                stage_ids,
                "group_refresh",
                list_group_id="2026",
            )
            result = workflow.prompt_many(
                "sample",
                stage_ids,
                "group_refresh",
                list_group_id="2026",
            )

        audit_plan = next(
            item for item in plan["stagePlans"] if item["stageId"] == "law_audit"
        )
        self.assertEqual(result["stageIds"], stage_ids)
        self.assertEqual(result["targetCount"], 2)
        self.assertEqual(result["workItemCount"], 8)
        self.assertEqual(audit_plan["targetCount"], 2)
        self.assertTrue(audit_plan["allQuestionGate"])
        self.assertIn("対象問題: `2問すべて`", result["prompt"])
        self.assertIn("工程判定: `延べ8件`", result["prompt"])
        self.assertIn("選択工程を上記順序で完了してから次の問題へ進む", result["prompt"])
        self.assertIn("Codex組み込みweb検索でe-Gov又は所管官庁", result["prompt"])
        self.assertIn("既存のisLawRelatedだけで対象を絞らず", result["prompt"])
        self.assertNotIn("## 問題文", result["prompt"])

    def test_multiple_stage_plan_reuses_catalog_and_qualification_data(self):
        class CountingWorkflow(QualificationWorkflow):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.catalog_calls = 0
                self.qualification_data_calls = 0

            def catalog(self, qualification=""):
                self.catalog_calls += 1
                return super().catalog(qualification)

            def _qualification_data(self, qualification):
                self.qualification_data_calls += 1
                return super()._qualification_data(qualification)

        with tempfile.TemporaryDirectory() as directory:
            workflow = CountingWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [question()]}],
                ),
            )
            workflow.plan_many(
                "sample",
                ["question_type", "law_context", "explanation", "law_audit"],
                "group_refresh",
                list_group_ids=["2026"],
            )

        self.assertEqual(workflow.catalog_calls, 1)
        self.assertEqual(workflow.qualification_data_calls, 1)

    def test_law_audit_refresh_alone_rechecks_every_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            law_question = question(law_related=True)
            law_question["id"] = "law-question"
            non_law_question = question(law_related=False, question_number=2)
            non_law_question["id"] = "non-law-question"
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [
                        {
                            "listGroupId": "2026",
                            "questions": [law_question, non_law_question],
                        }
                    ],
                ),
            )

            plan = workflow.plan(
                "sample", "law_audit", "group_refresh", list_group_id="2026"
            )
            result = workflow.prompt(
                "sample", "law_audit", "group_refresh", list_group_id="2026"
            )

        self.assertEqual(plan["targetCount"], 2)
        self.assertTrue(plan["allQuestionGate"])
        self.assertIn("既存のisLawRelatedだけで対象を絞らず", result["prompt"])
        self.assertIn(
            "法令根拠が見つからないこと自体を理由に",
            result["prompt"],
        )
        self.assertIn("確認できなければ`false`を維持", result["prompt"])
        self.assertIn("古い`hold`を残さない", result["prompt"])
        self.assertIn("監査sidecarの`sourceSummary`", result["prompt"])
        self.assertIn(
            str(root / "prompt" / "03b_prompt_audit_current_law_and_patch.md"),
            result["prompt"],
        )
        self.assertIn(
            str(
                root
                / "prompt"
                / "qualification_docs"
                / "sample"
                / "*law_reference*.md"
            ),
            result["prompt"],
        )

    def test_law_audit_prompt_is_path_only_and_requires_per_choice_research(self):
        patches = [
            "output/sample/questions_json/2026/18_law_context_prepared/question_2026_1_lawContext_prepared.json",
            "output/sample/questions_json/2026/21_explanationText_added/question_2026_1_explanationText_added.json",
        ]
        issue = {
            "code": "law_audit_metadata_incomplete",
            "fields": ["lawRevisionFacts.current.correctChoiceText"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [
                        {
                            "listGroupId": "2026",
                            "questions": [
                                question(
                                    patches=patches,
                                    issues=[issue],
                                    law_related=True,
                                )
                            ],
                        }
                    ],
                ),
            )
            prompt = workflow.prompt("sample", "law_audit", "attention")["prompt"]

        self.assertIn("Codex組み込みweb検索でe-Gov又は所管官庁", prompt)
        self.assertIn("21_explanationText_added", prompt)
        self.assertIn("suggestedQuestionDetailsByChoiceは0〜3件", prompt)
        self.assertIn("法令名・条文を機械的に文頭の主語にしない", prompt)
        self.assertIn("examTimeDecisionとcurrentLawDecision", prompt)
        self.assertNotIn("## 問題文", prompt)


if __name__ == "__main__":
    unittest.main()
