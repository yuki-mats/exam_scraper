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


def question(*, patches=None, issues=None, law_related=False, workflow=None, group="2026"):
    return {
        "paths": {
            "source": f"output/sample/questions_json/{group}/00_source/question_{group}_1.json",
            "patches": list(patches or []),
        },
        "issues": list(issues or []),
        "issueCodes": [issue["code"] for issue in issues or []],
        "isLawRelated": law_related,
        "projected": {
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


class QualificationWorkflowTests(unittest.TestCase):
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

        self.assertEqual(initial["nextStageId"], "setup")
        self.assertEqual(prepared["nextStageId"], "question_type")
        stage = next(item for item in prepared["stages"] if item["id"] == "question_type")
        self.assertEqual(stage["status"], "not_started")
        self.assertEqual(stage["remainingCount"], 1)
        self.assertIn("10_questionType_fixed", stage["outputPreview"][0])
        self.assertIn("prompt/01_prompt_fix_questionType.md", prompt)
        self.assertIn("question_2026_1.json", prompt)
        self.assertNotIn("## 問題文", prompt)

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

            with_issue = workflow.overview("sample")
            item["issues"] = []
            item["issueCodes"] = []
            without_issue = workflow.overview("sample")

        self.assertEqual(with_issue["nextStageId"], "law_audit")
        audit = next(item for item in with_issue["stages"] if item["id"] == "law_audit")
        self.assertEqual(audit["remainingCount"], 1)
        self.assertIn("21_explanationText_added", audit["outputPreview"][0])
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

            overview = workflow.overview("sample")
            prompt = workflow.prompt("sample", "correct_choice")["prompt"]

        self.assertEqual(overview["nextStageId"], "correct_choice")
        self.assertLess(
            [stage["id"] for stage in overview["stages"]].index("correct_choice"),
            [stage["id"] for stage in overview["stages"]].index("law_context"),
        )
        self.assertIn("23_correctChoiceText_fixed", prompt)
        self.assertIn("prompt/02a_prompt_review_correctChoiceText.md", prompt)

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
        self.assertEqual(plan["modeLabel"], "2025を全件洗い替え")
        self.assertTrue(all("/2025/" in path for path in plan["sourceFiles"]))

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
            workflow = QualificationWorkflow(
                root,
                FakeInventory(
                    "sample",
                    [{"listGroupId": "2026", "questions": [question(patches=patches)]}],
                ),
            )
            before = workflow.overview("sample")
            with self.assertRaisesRegex(ValueError, "03c カテゴリ設計"):
                workflow.plan("sample", "question_set", "refresh")
            write_category(root)
            after = workflow.overview("sample")

        self.assertEqual(before["nextStageId"], "category_setup")
        question_set = next(
            stage for stage in before["stages"] if stage["id"] == "question_set"
        )
        self.assertEqual(question_set["status"], "waiting")
        self.assertEqual(after["nextStageId"], "question_set")

    def test_multiple_stages_generate_one_question_at_a_time_prompt(self):
        issue = {
            "code": "law_audit_metadata_incomplete",
            "fields": ["lawRevisionFacts.current.correctChoiceText"],
        }
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory),
                FakeInventory(
                    "sample",
                    [
                        {
                            "listGroupId": "2026",
                            "questions": [
                                question(issues=[issue], law_related=True)
                            ],
                        }
                    ],
                ),
            )
            result = workflow.prompt_many(
                "sample",
                ["question_type", "explanation", "law_audit"],
                "group_refresh",
                list_group_id="2026",
            )

        self.assertEqual(result["stageIds"], ["question_type", "explanation", "law_audit"])
        self.assertEqual(result["targetCount"], 1)
        self.assertIn("選択工程を上記順序で完了してから次の問題へ進む", result["prompt"])
        self.assertIn("Lawzilla MCPとFirestore条文検索で一問一肢ずつ", result["prompt"])
        self.assertNotIn("## 問題文", result["prompt"])

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

        self.assertIn("Lawzilla MCPとFirestore条文検索で一問一肢ずつ", prompt)
        self.assertIn("21_explanationText_added", prompt)
        self.assertNotIn("## 問題文", prompt)


if __name__ == "__main__":
    unittest.main()
