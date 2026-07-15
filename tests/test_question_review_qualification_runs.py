import json
import tempfile
import time
import unittest
from pathlib import Path

from tools.question_review_console.codex_app_server import AppServerTurnResult
from tools.question_review_console.jobs import JobManager
from tools.question_review_console.qualification_runs import (
    QualificationRunCoordinator,
    QualificationRunError,
    QualificationRunStore,
)
from tools.question_review_console.qualification_workflow import QualificationWorkflow


class FakeWorkflow:
    def plan(self, qualification, stage_id, mode="remaining"):
        machine = stage_id == "delivery"
        groups = ["2025", "2026"] if machine else ["2026"]
        return {
            "qualification": qualification,
            "stageId": stage_id,
            "stageCode": "出力" if machine else "03b",
            "stageLabel": "公開準備" if machine else "現行法監査",
            "purpose": "成果物を確認する" if machine else "一問一肢を監査する",
            "kind": "machine" if machine else "human",
            "mode": mode,
            "modeLabel": "全件洗い替え" if mode == "refresh" else "未作業のみ",
            "targetCount": len(groups) if machine else 3,
            "targetGroupIds": groups,
            "sourceFiles": ["output/sample/questions_json/2026/00_source"],
            "outputFiles": [
                "output/sample/questions_json/2026/"
                "21_explanationText_added/patch.json"
            ],
            "canonicalDocs": ["prompt/README.md"],
            "force": machine and mode == "refresh",
        }

    def prompt(self, qualification, stage_id, mode="remaining"):
        return {
            "qualification": qualification,
            "stageId": stage_id,
            "mode": mode,
            "targetCount": 3,
            "prompt": "# 資格単位の問題整備\n\n対象ファイルだけを一件ずつ監査する。\n",
        }


class FakeSynchronizer:
    def __init__(self):
        self.calls = []

    def preview(self, qualification, list_group_id, *, force=False):
        return {
            "previewToken": f"token-{list_group_id}-{force}",
            "questionCount": 2,
            "localReady": not force,
            "requiredFieldWarnings": [],
        }

    def run(self, qualification, list_group_id, token, emit, *, force=False):
        self.calls.append((qualification, list_group_id, force))
        emit(f"{list_group_id}: 完了")
        return {"message": "同期しました。"}


class SourceOnlyInventory:
    def inventory(self):
        return {
            "qualifications": [
                {"id": "new-exam", "listGroupIds": ["2026"]}
            ]
        }

    def group(self, qualification, list_group_id):
        original_id = f"new-exam-{list_group_id}-q1"
        return {
            "listGroupId": list_group_id,
            "questions": [
                {
                    "id": original_id,
                    "reviewKey": (
                        f"new-exam:{list_group_id}:"
                        f"question_{list_group_id}_1:{original_id}"
                    ),
                    "qualification": "new-exam",
                    "listGroupId": list_group_id,
                    "originalQuestionId": original_id,
                    "paths": {
                        "source": (
                            f"output/new-exam/questions_json/{list_group_id}/"
                            f"00_source/question_{list_group_id}_1.json"
                        ),
                        "patches": [],
                    },
                    "issues": [],
                    "issueCodes": [],
                    "isLawRelated": False,
                    "projected": {"originalQuestionId": original_id},
                    "workflow": {
                        "merge": "missing",
                        "convert": "missing",
                        "upload": "missing",
                    },
                }
            ],
        }


class MultiGroupSourceInventory(SourceOnlyInventory):
    def inventory(self):
        return {
            "qualifications": [
                {"id": "new-exam", "listGroupIds": ["2025", "2026"]}
            ]
        }

class QualificationRunTests(unittest.TestCase):
    def test_human_run_records_validated_question_level_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["correct_choice", "explanation"],
                "stageCode": "02a → 03",
                "stageLabel": "複数工程",
                "mode": "outdated",
                "modeLabel": "洗い替え必要のみ",
                "kind": "human",
                "targetCount": 2,
                "workItemCount": 4,
                "targetGroupIds": ["2026"],
                "targetQuestionKeys": ["sample:2026:q01", "sample:2026:q02"],
                "progressTargets": [
                    {
                        "id": "ui-q1",
                        "questionKey": "sample:2026:q01",
                        "listGroupId": "2026",
                        "questionLabel": "問1",
                        "bodyPreview": "問題本文1",
                        "aliases": ["source-q1"],
                    },
                    {
                        "id": "ui-q2",
                        "questionKey": "sample:2026:q02",
                        "listGroupId": "2026",
                        "questionLabel": "問2",
                        "bodyPreview": "問題本文2",
                        "aliases": ["source-q2"],
                    },
                ],
                "stagePlans": [
                    {
                        "stageId": "correct_choice",
                        "stageCode": "02a",
                        "stageLabel": "正答精査",
                    },
                    {
                        "stageId": "explanation",
                        "stageCode": "03",
                        "stageLabel": "解説整備",
                    },
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            run = store.create(plan, status="running", prompt="整備する。")
            progress_path = root / run["progressReceiptPath"]
            progress_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {"event": "question_started", "questionId": "source-q1"},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "event": "stage_completed",
                                "questionId": "ui-q1",
                                "stageId": "correct_choice",
                                "result": {
                                    "correctChoiceText": ["正しい", "誤り"],
                                    "privateReasoning": "表示してはいけない",
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "question_completed", "questionId": "ui-q1"},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "stage_completed", "questionId": "scope外", "stageId": "explanation"},
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            progress = store.progress("sample", run["runId"])
            prompt = store.prompt("sample", run["runId"])

        self.assertEqual(progress["completedQuestionCount"], 1)
        self.assertEqual(progress["completedWorkItemCount"], 1)
        self.assertEqual(progress["percent"], 50)
        self.assertEqual(progress["current"]["questionId"], "ui-q1")
        self.assertEqual(progress["groups"][0]["percent"], 50)
        self.assertEqual(progress["invalidEventCount"], 1)
        self.assertNotIn("privateReasoning", progress["events"][1]["result"])
        self.assertIn("画面用の問題別進捗", prompt)
        self.assertIn("progressTargets", prompt)

    def test_progress_receipt_is_not_treated_as_a_maintenance_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="running",
                prompt="整備する。",
            )
            progress_relative = str(
                (root / run["progressReceiptPath"]).relative_to(root)
            )

            coordinator._validate_changed_files(
                "sample",
                run["runId"],
                coordinator.store.get("sample", run["runId"]),
                (progress_relative,),
                (progress_relative,),
            )

    def test_validated_run_records_only_the_manifest_stage_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, SourceOnlyInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            policy = workflow.versioned_policies("new-exam")["question_type"]
            run = {
                "runId": "run-question-type",
                "qualification": "new-exam",
                "targetGroupIds": ["2026"],
                "policyVersions": {"question_type": policy["policyVersion"]},
                "policyFingerprints": {
                    "question_type": policy["policyFingerprint"]
                },
                "policyTargets": {"question_type": ["new-exam-2026-q1"]},
            }

            receipt = coordinator._record_work_versions(run)
            item = SourceOnlyInventory().group("new-exam", "2026")["questions"][0]
            status = workflow.work_versions.status_for(
                item,
                workflow.versioned_policies("new-exam").values(),
            )

        self.assertEqual(receipt["recordedCount"], 1)
        by_id = {stage["id"]: stage for stage in status["stages"]}
        self.assertEqual(by_id["question_type"]["status"], "current")
        self.assertEqual(by_id["question_intent"]["status"], "unrecorded")

    def test_run_policy_drift_blocks_version_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, SourceOnlyInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            policies = workflow.versioned_policies("new-exam")
            question_type = policies["question_type"]
            question_intent = policies["question_intent"]

            with self.assertRaisesRegex(QualificationRunError, "実行中"):
                coordinator._record_work_versions(
                    {
                        "runId": "run-stale",
                        "qualification": "new-exam",
                        "targetGroupIds": ["2026"],
                        "policyVersions": {
                            "question_type": question_type["policyVersion"],
                            "question_intent": question_intent["policyVersion"],
                        },
                        "policyFingerprints": {
                            "question_type": question_type["policyFingerprint"],
                            "question_intent": "stale",
                        },
                        "policyTargets": {
                            "question_type": ["new-exam-2026-q1"],
                            "question_intent": ["new-exam-2026-q1"],
                        },
                    }
                )
            version_path_exists = workflow.work_versions.path_for(
                "new-exam", "2026"
            ).exists()

        self.assertFalse(version_path_exists)

    def test_qualification_stage_writable_roots_are_limited_to_its_patch_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = FakeWorkflow().plan("sample", "question_type")
            plan["stageIds"] = ["question_type"]
            plan["workType"] = "maintenance"
            run = coordinator.store.create(plan, status="queued", prompt="work")

            roots, _created = coordinator._maintenance_writable_roots(
                "sample", run["runId"]
            )

        expected_group = root / "output/sample/questions_json/2026"
        self.assertIn(expected_group / "10_questionType_fixed", roots)
        self.assertIn(expected_group / "99_model_review_flags", roots)
        self.assertNotIn(expected_group / "21_explanationText_added", roots)
        self.assertNotIn(root / "output/sample/category", roots)
        self.assertNotIn(root / "prompt/qualification_docs/sample", roots)

    def test_writable_root_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            outside = root / "outside"
            outside.mkdir()
            patch_root = (
                root
                / "output/sample/questions_json/2026/10_questionType_fixed"
            )
            patch_root.parent.mkdir(parents=True)
            patch_root.symlink_to(outside, target_is_directory=True)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = FakeWorkflow().plan("sample", "question_type")
            plan["stageIds"] = ["question_type"]
            run = coordinator.store.create(plan, status="queued", prompt="work")

            with self.assertRaisesRegex(QualificationRunError, "symlink"):
                coordinator._maintenance_writable_roots(
                    "sample", run["runId"]
                )

    def test_rework_writable_roots_follow_structured_evaluation_stages(self):
        class FakeAppServer:
            configured = True
            provider = "Codex App Server"

            def assert_subscription_access(self, *, force=True):
                return {"allowed": True, "planType": "pro"}

        class DeferredJobs:
            def start(self, *, kind, key, worker):
                self.worker = worker
                return {"jobId": "job-deferred", "status": "queued"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=FakeAppServer(),
            )
            started = coordinator.start_review(
                {
                    "id": "question-1",
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "stateHash": "state-1",
                    "originalQuestionId": "original-1",
                    "paths": {
                        "source": (
                            "output/sample/questions_json/2026/"
                            "00_source/question_2026_1.json"
                        ),
                        "patches": [],
                    },
                },
                {
                    "reviewId": "review-1",
                    "prompt": "rework",
                    "investigationScope": "current_question",
                    "evaluationSnapshot": {
                        "reworkItems": [
                            {"stage": "03", "message": "fix", "choiceIndexes": [0]}
                        ]
                    },
                },
                work_type="rework",
            )
            run = coordinator.store.get("sample", started["run"]["runId"])
            roots, _created = coordinator._maintenance_writable_roots(
                "sample", run["runId"]
            )

        expected_group = root / "output/sample/questions_json/2026"
        self.assertIn(expected_group / "21_explanationText_added", roots)
        self.assertIn(expected_group / "99_model_review_flags", roots)
        self.assertNotIn(expected_group / "23_correctChoiceText_fixed", roots)
        self.assertNotIn(root / "output/sample/category", roots)
        self.assertEqual(run["policyVersions"], {"explanation": "1.0"})

    def test_qualification_law_audit_preserves_trusted_sources_and_record_scope(self):
        class FakeAppServer:
            configured = True
            provider = "Codex App Server"

            def assert_subscription_access(self, *, force=True):
                return {"allowed": True, "planType": "pro"}

        class DeferredJobs:
            def start(self, *, kind, key, worker):
                self.worker = worker
                return {"jobId": "job-deferred", "status": "queued"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=FakeAppServer(),
            )
            source_files = [
                "output/sample/questions_json/2026/00_source/q1.json",
                "output/sample/questions_json/2026/00_source/q2.json",
            ]
            started = coordinator.start_review(
                {
                    "id": "anchor",
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "stateHash": "state-1",
                    "paths": {"source": source_files[0], "patches": []},
                },
                {
                    "reviewId": "review-1",
                    "prompt": "law audit",
                    "requestKind": "qualification_law_audit",
                    "investigationScope": "qualification",
                    "issueTypes": ["law_audit_metadata_incomplete"],
                    "targetSourceFiles": source_files,
                    "targetRecordAliasGroups": [["q1"], ["q2"]],
                    "targetSourceRecordScopes": {
                        source_files[0]: [["q1"]],
                        source_files[1]: [["q2"]],
                    },
                },
                work_type="maintenance",
            )
            run = coordinator.store.get(
                "sample", started["run"]["runId"]
            )

        self.assertEqual(run["sourceFiles"], source_files)
        self.assertEqual(run["targetRecordAliasGroups"], [["q1"], ["q2"]])
        self.assertEqual(run["targetRecordAliases"], ["q1", "q2"])
        self.assertEqual(run["targetCount"], 2)
        self.assertEqual(run["policyVersions"], {"law_audit": "1.0"})
        expected_record_files = {
            path
            for path in [*run["allowedPatchFiles"], *run["allowedWriteFiles"]]
            if Path(path).suffix in {".json", ".jsonl"}
        }
        self.assertEqual(set(run["targetRecordScopes"]), expected_record_files)
        self.assertEqual(
            run["targetRecordScopes"][
                "output/sample/review/law_revision_audit/"
                "2026_law_revision_audit.jsonl"
            ],
            [["q1"], ["q2"]],
        )
        self.assertTrue(
            any("18_law_context_prepared/q1_" in path for path in run["allowedPatchFiles"])
        )
        self.assertTrue(
            any("23_correctChoiceText_fixed/q2_" in path for path in run["allowedPatchFiles"])
        )
        self.assertFalse(
            any("_explanationText_needs_5_5_high_review" in path for path in run["allowedPatchFiles"])
        )
        self.assertTrue(
            any("_lawRevision_needs_5_5_high_review" in path for path in run["allowedPatchFiles"])
        )

    def test_question_review_contract_limits_fields_and_patch_files(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            question = {
                "paths": {
                    "source": (
                        "output/sample/questions_json/2026/"
                        "00_source/question_2026_1.json"
                    ),
                    "patches": [
                        "output/sample/questions_json/2026/"
                        "21_explanationText_added/existing.json"
                    ],
                }
            }

            patch_dirs, write_areas, patch_files, write_files = (
                coordinator._review_write_contract(
                    question,
                    {
                        "fields": ["explanationText"],
                        "investigationScope": "current_question",
                    },
                )
            )

        self.assertEqual(
            patch_dirs,
            {"21_explanationText_added", "99_model_review_flags"},
        )
        self.assertEqual(write_areas, set())
        self.assertEqual(write_files, set())
        self.assertIn(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/question_2026_1_merged_explanationText_added.json",
            patch_files,
        )
        self.assertNotIn(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/existing.json",
            patch_files,
        )
        self.assertFalse(
            any("23_correctChoiceText_fixed" in path for path in patch_files)
        )

    def test_question_review_without_a_bounded_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            with self.assertRaisesRegex(QualificationRunError, "field"):
                coordinator._review_write_contract(
                    {"paths": {}},
                    {
                        "issueTypes": ["other"],
                        "investigationScope": "current_question",
                    },
                )

    def test_question_body_and_choices_require_the_dedicated_correction_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            question = {
                "paths": {
                    "source": (
                        "output/sample/questions_json/2026/"
                        "00_source/question_2026_1.json"
                    )
                }
            }
            reviews = (
                {"fields": ["questionBodyText"]},
                {"selection": {"fields": ["choiceTextList"]}},
                {"fields": ["explanationText", "questionBodyText"]},
            )

            for review in reviews:
                with self.subTest(review=review), self.assertRaisesRegex(
                    QualificationRunError, "自動整備対象外"
                ):
                    coordinator._review_write_contract(
                        question,
                        {**review, "investigationScope": "current_question"},
                    )

    def test_target_files_cannot_enable_question_issue_corrections(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            with self.assertRaisesRegex(
                QualificationRunError, "専用workflow"
            ):
                coordinator._review_write_contract(
                    {
                        "paths": {
                            "source": (
                                "output/sample/questions_json/2026/"
                                "00_source/question_2026_1.json"
                            )
                        }
                    },
                    {
                        "fields": ["explanationText"],
                        "targetFiles": [
                            "output/sample/questions_json/2026/"
                            "24_questionIssueCorrections/crafted.json"
                        ],
                        "investigationScope": "current_question",
                    },
                )

    def test_question_type_still_uses_only_its_patch_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            patch_dirs, _areas, patch_files, _write_files = (
                coordinator._review_write_contract(
                    {
                        "paths": {
                            "source": (
                                "output/sample/questions_json/2026/"
                                "00_source/question_2026_1.json"
                            )
                        }
                    },
                    {
                        "fields": ["questionType"],
                        "investigationScope": "current_question",
                    },
                )
            )

        self.assertEqual(
            patch_dirs, {"10_questionType_fixed", "99_model_review_flags"}
        )
        self.assertFalse(
            any("24_questionIssueCorrections" in path for path in patch_files)
        )
        self.assertTrue(
            any("_questionType_needs_5_5_high_review.jsonl" in path for path in patch_files)
        )
        self.assertFalse(
            any("_explanationText_needs_5_5_high_review.jsonl" in path for path in patch_files)
        )
        self.assertFalse(
            any("_lawRevision_needs_5_5_high_review.jsonl" in path for path in patch_files)
        )

    def test_category_setup_has_only_exact_non_patch_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = {
                "qualification": "sample",
                "stageId": "category_setup",
                "stageIds": ["category_setup"],
                "sourceFiles": [
                    "output/sample/questions_json/2026/00_source/q1.json"
                ],
                "outputFiles": [
                    "output/sample/category/category.json",
                    "prompt/qualification_docs/sample/03_category_preparation.md",
                ],
            }

            coordinator._apply_plan_write_contract(plan)

        self.assertEqual(plan["allowedPatchDirs"], [])
        self.assertEqual(plan["allowedPatchFiles"], [])
        self.assertEqual(
            plan["allowedWriteAreas"], ["category", "qualification_docs"]
        )
        self.assertEqual(
            plan["allowedWriteFiles"], sorted(plan["outputFiles"])
        )

    def test_multi_stage_contract_does_not_cross_product_sources_and_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            q1 = "output/sample/questions_json/2026/00_source/q1.json"
            q2 = "output/sample/questions_json/2026/00_source/q2.json"
            plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["question_type", "explanation"],
                "stagePlans": [
                    {
                        "stageId": "question_type",
                        "stageIds": ["question_type"],
                        "sourceFiles": [q1],
                        "outputFiles": [],
                        "targetGroupIds": ["2026"],
                    },
                    {
                        "stageId": "explanation",
                        "stageIds": ["explanation"],
                        "sourceFiles": [q2],
                        "outputFiles": [],
                        "targetGroupIds": ["2026"],
                    },
                ],
            }

            coordinator._apply_plan_write_contract(plan)

        joined = "\n".join(plan["allowedPatchFiles"])
        self.assertIn("10_questionType_fixed/q1_questionType_fixed.json", joined)
        self.assertIn("21_explanationText_added/q2_merged_explanationText_added.json", joined)
        self.assertNotIn("21_explanationText_added/q1_", joined)
        self.assertNotIn("10_questionType_fixed/q2_", joined)
        self.assertIn("q1_questionType_needs_5_5_high_review.jsonl", joined)
        self.assertIn("q2_explanationText_needs_5_5_high_review.jsonl", joined)
        self.assertNotIn("q1_explanationText_needs_5_5_high_review.jsonl", joined)

    def test_year_scoped_law_plan_allows_only_its_review_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = {
                "qualification": "sample",
                "stageId": "law_audit",
                "stageIds": ["law_audit"],
                "sourceFiles": [
                    "output/sample/questions_json/2026/00_source/q1.json"
                ],
                "outputFiles": [],
                "targetGroupIds": ["2026"],
            }
            coordinator._apply_plan_write_contract(plan)
            run = {**plan, "runId": "run-1"}
            roots = coordinator._maintenance_root_candidates(
                "sample", "run-1", run
            )

            allowed = coordinator._maintenance_path_allowed_for_run(
                Path(
                    "output/sample/review/law_revision_audit/"
                    "2026_law_revision_audit.jsonl"
                ),
                roots,
                run,
            )
            rejected = coordinator._maintenance_path_allowed_for_run(
                Path(
                    "output/sample/review/law_revision_audit/"
                    "2025_law_revision_audit.jsonl"
                ),
                roots,
                run,
            )

        self.assertEqual(plan["allowedWriteAreas"], ["review"])
        self.assertNotIn("law_evidence", plan["allowedWriteAreas"])
        self.assertNotIn("reports", plan["allowedWriteAreas"])
        self.assertTrue(allowed)
        self.assertFalse(rejected)

    def test_current_question_law_sidecar_is_an_exact_file_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            question = {
                "qualification": "sample",
                "listGroupId": "2026",
                "paths": {
                    "source": (
                        "output/sample/questions_json/2026/"
                        "00_source/question_2026_1.json"
                    )
                },
            }
            patch_dirs, write_areas, patch_files, write_files = (
                coordinator._review_write_contract(
                    question,
                    {
                        "fields": ["lawRevisionFacts"],
                        "issueTypes": ["law_audit_metadata_incomplete"],
                        "investigationScope": "current_question",
                    },
                )
            )
            run = {
                "qualification": "sample",
                "stageIds": ["maintenance"],
                "targetGroupIds": ["2026"],
                "allowedPatchDirs": sorted(patch_dirs),
                "allowedPatchFiles": sorted(patch_files),
                "allowedWriteAreas": sorted(write_areas),
                "allowedWriteFiles": sorted(write_files),
            }
            roots = coordinator._maintenance_root_candidates(
                "sample", "run-1", run
            )

            target_allowed = coordinator._maintenance_path_allowed_for_run(
                Path(next(iter(write_files))), roots, run
            )
            other_group_allowed = coordinator._maintenance_path_allowed_for_run(
                Path(
                    "output/sample/review/law_revision_audit/"
                    "2025_law_revision_audit.jsonl"
                ),
                roots,
                run,
            )

        self.assertEqual(write_areas, {"review"})
        self.assertTrue(target_allowed)
        self.assertFalse(other_group_allowed)

    def test_current_question_prefers_the_selected_timestamp_patch(self):
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
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            _dirs, _areas, patch_files, _write_files = (
                coordinator._review_write_contract(
                    {
                        "paths": {
                            "source": (
                                "output/sample/questions_json/2026/"
                                "00_source/question_2026_1.json"
                            ),
                            "patches": [],
                        }
                    },
                    {
                        "fields": ["explanationText"],
                        "investigationScope": "current_question",
                    },
                )
            )

        self.assertIn(str(latest.relative_to(root)), patch_files)
        self.assertNotIn(str(fixed.relative_to(root)), patch_files)

    def test_qualification_plan_rejects_an_unplanned_same_stage_file(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = coordinator._plan(
                "sample", "law_audit", "remaining", None
            )
            run = {**plan, "runId": "run-1"}
            roots = coordinator._maintenance_root_candidates(
                "sample", "run-1", run
            )

            planned = coordinator._maintenance_path_allowed_for_run(
                Path(plan["allowedPatchFiles"][0]), roots, run
            )
            unplanned = coordinator._maintenance_path_allowed_for_run(
                Path(
                    "output/sample/questions_json/2026/"
                    "21_explanationText_added/unplanned.json"
                ),
                roots,
                run,
            )

        self.assertTrue(planned)
        self.assertFalse(unplanned)

    def test_record_scope_rejects_a_different_question_in_aggregate_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/aggregate.json"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"original_question_id": "q1", "value": 1},
                            {"original_question_id": "q2", "value": 1},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan.update(
                {
                    "stageIds": ["law_audit"],
                    "targetRecordAliases": ["q1"],
                    "allowedPatchDirs": ["21_explanationText_added"],
                    "allowedPatchFiles": [relative.as_posix()],
                    "targetRecordScopes": {relative.as_posix(): [["q1"]]},
                }
            )
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (path.parent, (root / run["resultReceiptPath"]).parent),
            )
            path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"original_question_id": "q1", "value": 1},
                            {"original_question_id": "q2", "value": 2},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )

            with self.assertRaisesRegex(
                QualificationRunError, "対象問題以外"
            ):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {relative},
                )

    def test_record_scope_allows_only_the_target_record_to_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/aggregate.json"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            before = {
                "question_bodies": [
                    {"original_question_id": "q1", "value": 1},
                    {"original_question_id": "q2", "value": 1},
                ]
            }
            path.write_text(json.dumps(before), encoding="utf-8")
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan.update(
                {
                    "stageIds": ["law_audit"],
                    "targetRecordAliases": ["q1"],
                    "allowedPatchDirs": ["21_explanationText_added"],
                    "allowedPatchFiles": [relative.as_posix()],
                    "targetRecordScopes": {relative.as_posix(): [["q1"]]},
                }
            )
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (path.parent, (root / run["resultReceiptPath"]).parent),
            )
            before["question_bodies"][0]["value"] = 2
            path.write_text(json.dumps(before), encoding="utf-8")
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )

            coordinator._validate_record_scope(
                "sample",
                run["runId"],
                store.get("sample", run["runId"]),
                {relative},
            )

    def test_record_scope_rejects_target_record_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/aggregate.json"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"originalQuestionId": "q1", "value": 1},
                            {"originalQuestionId": "q2", "value": 1},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan.update(
                {
                    "targetRecordAliasGroups": [["q1"]],
                    "allowedPatchDirs": ["21_explanationText_added"],
                    "allowedPatchFiles": [relative.as_posix()],
                    "targetRecordScopes": {relative.as_posix(): [["q1"]]},
                }
            )
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (path.parent, (root / run["resultReceiptPath"]).parent),
            )
            path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"originalQuestionId": "q2", "value": 1}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )

            with self.assertRaisesRegex(QualificationRunError, "record削除"):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {relative},
                )

    def test_record_scope_rejects_protected_body_change_through_other_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_relative = Path(
                "output/sample/questions_json/2026/00_source/q1.json"
            )
            patch_relative = Path(
                "output/sample/questions_json/2026/10_questionType_fixed/q1.json"
            )
            source = root / source_relative
            patch = root / patch_relative
            source.parent.mkdir(parents=True)
            patch.parent.mkdir(parents=True)
            source.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "originalQuestionId": "q1",
                                "questionBodyText": "変更禁止の問題文",
                                "choiceTextList": ["A", "B"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            patch.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "originalQuestionId": "q1",
                                "questionType": "single",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "question_type", "remaining")
            plan.update(
                {
                    "sourceFiles": [source_relative.as_posix()],
                    "targetRecordAliasGroups": [["q1"]],
                    "allowedPatchDirs": ["10_questionType_fixed"],
                    "allowedPatchFiles": [patch_relative.as_posix()],
                    "targetRecordScopes": {
                        patch_relative.as_posix(): [["q1"]]
                    },
                }
            )
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (patch.parent, (root / run["resultReceiptPath"]).parent),
            )
            patch.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "originalQuestionId": "q1",
                                "questionType": "single",
                                "questionBodyText": "Codexが変更した問題文",
                                "choiceTextList": ["A", "B"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )

            with self.assertRaisesRegex(
                QualificationRunError, "自動整備対象外field"
            ):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {patch_relative},
                )

    def test_record_scope_allows_sparse_patch_and_rejects_identity_injection(self):
        def validate(
            source_record,
            before_records,
            after_records,
            *,
            target_aliases=("q1",),
        ):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source_relative = Path(
                    "output/sample/questions_json/2026/00_source/q1.json"
                )
                patch_relative = Path(
                    "output/sample/questions_json/2026/"
                    "10_questionType_fixed/q1.json"
                )
                source = root / source_relative
                patch = root / patch_relative
                source.parent.mkdir(parents=True)
                patch.parent.mkdir(parents=True)
                source.write_text(
                    json.dumps({"question_bodies": [source_record]}),
                    encoding="utf-8",
                )
                if before_records is not None:
                    patch.write_text(
                        json.dumps({"question_bodies": before_records}),
                        encoding="utf-8",
                    )
                store = QualificationRunStore(root)
                plan = FakeWorkflow().plan("sample", "question_type", "remaining")
                plan.update(
                    {
                        "sourceFiles": [source_relative.as_posix()],
                        "targetRecordAliasGroups": [list(target_aliases)],
                        "allowedPatchDirs": ["10_questionType_fixed"],
                        "allowedPatchFiles": [patch_relative.as_posix()],
                        "targetRecordScopes": {
                            patch_relative.as_posix(): [list(target_aliases)]
                        },
                    }
                )
                run = store.create(plan, status="running", prompt="work")
                store.write_baseline(
                    "sample",
                    run["runId"],
                    (patch.parent, (root / run["resultReceiptPath"]).parent),
                )
                patch.write_text(
                    json.dumps({"question_bodies": after_records}),
                    encoding="utf-8",
                )
                coordinator = QualificationRunCoordinator(
                    root,
                    FakeWorkflow(),
                    FakeSynchronizer(),
                    JobManager(),
                    "secret",
                    store=store,
                )
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {patch_relative},
                )

        validate(
            {
                "originalQuestionId": "q1",
                "questionBodyText": "変更禁止の問題文",
                "choiceTextList": ["A", "B"],
            },
            [{"originalQuestionId": "q1", "questionType": "single"}],
            [{"originalQuestionId": "q1", "questionType": "multiple"}],
        )
        validate(
            {"public_question_id": "q1"},
            None,
            [{"original_question_id": "q1", "questionType": "single"}],
        )
        with self.assertRaisesRegex(QualificationRunError, "ID fieldが空又は不正"):
            validate(
                {"public_question_id": "q1"},
                None,
                [
                    {
                        "originalQuestionId": "q1",
                        "questionId": None,
                        "questionType": "single",
                    }
                ],
            )
        with self.assertRaisesRegex(QualificationRunError, "ID fieldが空又は不正"):
            validate(
                {"public_question_id": "q1"},
                None,
                [
                    {
                        "originalQuestionId": "q1",
                        "firestoreQuestionIds": ["q1", None],
                        "questionType": "single",
                    }
                ],
            )
        with self.assertRaisesRegex(QualificationRunError, "sourceと異なるID"):
            validate(
                {"public_question_id": "q1"},
                None,
                [
                    {
                        "originalQuestionId": "q1",
                        "questionId": "ui-hash",
                        "questionType": "single",
                    }
                ],
                target_aliases=("q1", "ui-hash"),
            )
        with self.assertRaisesRegex(QualificationRunError, "既存ID fieldの変更"):
            validate(
                {"originalQuestionId": "q1"},
                [
                    {
                        "originalQuestionId": "q1",
                        "questionId": "firestore-1",
                        "questionType": "single",
                    }
                ],
                [
                    {
                        "originalQuestionId": "replaced",
                        "questionId": "firestore-1",
                        "questionType": "single",
                    }
                ],
            )

    def test_record_scope_is_file_specific_across_year_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_relative = Path(
                "output/sample/review/law_revision_audit/"
                "2025_law_revision_audit.jsonl"
            )
            second_relative = Path(
                "output/sample/review/law_revision_audit/"
                "2026_law_revision_audit.jsonl"
            )
            first = root / first_relative
            second = root / second_relative
            first.parent.mkdir(parents=True)
            first.write_text(
                json.dumps({"originalQuestionId": "q25", "value": 1}) + "\n",
                encoding="utf-8",
            )
            second.write_text(
                json.dumps({"originalQuestionId": "q26", "value": 1}) + "\n",
                encoding="utf-8",
            )
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan.update(
                {
                    "targetRecordAliasGroups": [["q25"], ["q26"]],
                    "allowedPatchDirs": [],
                    "allowedPatchFiles": [],
                    "allowedWriteAreas": ["review"],
                    "allowedWriteFiles": [
                        first_relative.as_posix(),
                        second_relative.as_posix(),
                    ],
                    "targetRecordScopes": {
                        first_relative.as_posix(): [["q25"]],
                        second_relative.as_posix(): [["q26"]],
                    },
                }
            )
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (first.parent, (root / run["resultReceiptPath"]).parent),
            )
            first.write_text(
                "\n".join(
                    (
                        json.dumps({"originalQuestionId": "q25", "value": 1}),
                        json.dumps({"originalQuestionId": "q26", "value": 2}),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )

            with self.assertRaisesRegex(QualificationRunError, "sourceと異なるID"):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {first_relative},
                )

    def test_record_scope_protects_other_lines_in_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/99_model_review_flags/"
                "question_2026_1_explanationText_needs_5_5_high_review.jsonl"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                "\n".join(
                    (
                        json.dumps({"originalQuestionId": "q1", "value": 1}),
                        json.dumps({"originalQuestionId": "q2", "value": 1}),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan.update(
                {
                    "stageIds": ["law_audit"],
                    "targetRecordAliases": ["q1"],
                    "allowedPatchDirs": ["99_model_review_flags"],
                    "allowedPatchFiles": [relative.as_posix()],
                    "targetRecordScopes": {relative.as_posix(): [["q1"]]},
                }
            )
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (path.parent, (root / run["resultReceiptPath"]).parent),
            )
            path.write_text(
                "\n".join(
                    (
                        json.dumps({"originalQuestionId": "q1", "value": 1}),
                        json.dumps({"originalQuestionId": "q2", "value": 2}),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )

            with self.assertRaisesRegex(
                QualificationRunError, "対象問題以外"
            ):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {relative},
                )

    def test_record_scope_rejects_a_non_unique_target_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/aggregate.json"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            payload = {
                "question_bodies": [
                    {"original_question_id": "q1", "value": 1},
                    {"original_question_id": "q1", "value": 2},
                ]
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan.update(
                {
                    "stageIds": ["law_audit"],
                    "targetRecordAliases": ["q1"],
                    "allowedPatchDirs": ["21_explanationText_added"],
                    "allowedPatchFiles": [relative.as_posix()],
                    "targetRecordScopes": {relative.as_posix(): [["q1"]]},
                }
            )
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (path.parent, (root / run["resultReceiptPath"]).parent),
            )
            payload["question_bodies"][0]["value"] = 3
            path.write_text(json.dumps(payload), encoding="utf-8")
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )

            with self.assertRaisesRegex(QualificationRunError, "重複"):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {relative},
                )

    def test_machine_preview_does_not_require_app_server_binary(self):
        class MissingAppServer:
            configured = False
            provider = "Codex App Server"

        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                app_server=MissingAppServer(),
            )

            machine = coordinator.preview("sample", "delivery", "remaining")
            human = coordinator.preview("sample", "law_audit", "remaining")

        self.assertTrue(machine["canStart"])
        self.assertFalse(human["canStart"])

    def test_rejects_changed_files_outside_maintenance_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            with self.assertRaises(QualificationRunError):
                coordinator._validate_changed_files(
                    "sample",
                    "run-1",
                    {
                        "stageId": "law_audit",
                        "stageIds": ["law_audit"],
                        "targetGroupIds": ["2026"],
                        "result": {
                            "changedFiles": ["tools/question_review_console/server.py"]
                        }
                    },
                    (),
                )

            with self.assertRaises(QualificationRunError):
                coordinator._validate_changed_files(
                    "sample",
                    "run-1",
                    {"result": {"changedFiles": []}},
                    (),
                    ("document/operations/local_question_review_console.md",),
                )

    def test_human_run_executes_in_fresh_app_server_thread_and_records_receipt(self):
        class FakeAppServer:
            configured = True
            provider = "Codex App Server"

            def assert_subscription_access(self, *, force=True):
                return {"allowed": True, "planType": "pro"}

            def run_turn(self, prompt, **kwargs):
                self.kwargs = kwargs
                kwargs["on_thread_started"](
                    "thread-maintenance-1", "session-maintenance-1"
                )
                kwargs["on_turn_started"](
                    "thread-maintenance-1", "turn-maintenance-1"
                )
                receipt_line = next(
                    line for line in prompt.splitlines() if "完了時に検証結果を次へJSONで保存" in line
                )
                receipt_path = Path(receipt_line.split("`")[1])
                receipt_path.write_text(
                    json.dumps(
                        {
                            "status": "succeeded",
                            "summary": "対象工程を整備した。",
                            "commands": [
                                {"command": "python check.py", "status": "pass"}
                            ],
                            "changedFiles": [
                                "output/sample/questions_json/2026/"
                                "21_explanationText_added/patch.json"
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return AppServerTurnResult(
                    thread_id="thread-maintenance-1",
                    session_id="session-maintenance-1",
                    turn_id="turn-maintenance-1",
                    final_message="整備完了",
                    model="gpt-test",
                    service_tier=None,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FakeAppServer()
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=app_server,
            )
            snapshots = iter(
                [
                    {},
                    {
                        Path(
                            "output/sample/questions_json/2026/"
                            "21_explanationText_added/patch.json"
                        ): "sha256:after"
                    },
                ]
            )
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertIsNone(started["prompt"])
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["workType"], "maintenance")
        self.assertEqual(run["sandbox"], "workspace-write")
        self.assertEqual(run["threadId"], "thread-maintenance-1")
        self.assertEqual(run["sessionId"], "session-maintenance-1")
        self.assertEqual(run["turnId"], "turn-maintenance-1")
        self.assertEqual(run["model"], "gpt-test")
        self.assertIsNone(run["serviceTier"])
        self.assertEqual(run["reasoningEffort"], "high")
        self.assertEqual(app_server.kwargs["work_type"], "maintenance")
        self.assertNotEqual(app_server.kwargs["cwd"], root)
        self.assertTrue(app_server.kwargs["writable_roots"])
        self.assertTrue(
            all(
                path.resolve().is_relative_to(root.resolve())
                for path in app_server.kwargs["writable_roots"]
            )
        )
        run_dir = (
            root / "output/question_review_console/workflow_runs/sample" / run["runId"]
        ).resolve()
        self.assertIn(
            run_dir / "agent_output",
            app_server.kwargs["writable_roots"],
        )
        self.assertNotIn(run_dir, app_server.kwargs["writable_roots"])

    def test_success_receipt_must_match_the_actual_final_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            with self.assertRaisesRegex(
                QualificationRunError, "実際の最終差分にない"
            ):
                coordinator._validate_changed_files(
                    "sample",
                    "run-1",
                    {
                        "stageId": "law_audit",
                        "stageIds": ["law_audit"],
                        "targetGroupIds": ["2026"],
                        "result": {
                            "changedFiles": [
                                "output/sample/questions_json/2026/"
                                "21_explanationText_added/patch.json"
                            ]
                        }
                    },
                    (),
                    (),
                )

    def test_agent_output_rejects_files_other_than_the_result_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            extra = (
                "output/question_review_console/workflow_runs/sample/"
                "run-1/agent_output/notes.json"
            )

            with self.assertRaisesRegex(QualificationRunError, "result.json以外"):
                coordinator._validate_changed_files(
                    "sample",
                    "run-1",
                    {
                        "stageId": "law_audit",
                        "stageIds": ["law_audit"],
                        "targetGroupIds": ["2026"],
                        "result": {"changedFiles": [extra]},
                    },
                    (extra,),
                    (extra,),
                )

    def test_success_receipt_can_resolve_only_a_preexisting_failed_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            path = (
                "output/sample/questions_json/2026/"
                "21_explanationText_added/verified.json"
            )
            run = {
                "stageId": "law_audit",
                "stageIds": ["law_audit"],
                "targetGroupIds": ["2026"],
                "resolvableFailedDeltaPaths": [path],
                "result": {
                    "changedFiles": [],
                    "resolvedFailedDeltaPaths": [path],
                },
            }

            coordinator._validate_changed_files(
                "sample", "run-1", run, (), ()
            )

            run["resolvableFailedDeltaPaths"] = []
            with self.assertRaisesRegex(
                QualificationRunError, "未確定でなかった"
            ):
                coordinator._validate_changed_files(
                    "sample", "run-1", run, (), ()
                )

    def test_success_receipt_can_explicitly_resolve_unknown_delta_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            sentinel = (
                "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json"
            )
            run = {
                "qualification": "sample",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "targetGroupIds": ["2026"],
                "resolvableFailedDeltaPaths": [sentinel],
                "result": {
                    "changedFiles": [],
                    "resolvedFailedDeltaPaths": [sentinel],
                },
            }

            coordinator._validate_changed_files(
                "sample", "run-1", run, (), ()
            )

    def test_failed_turn_still_records_the_actual_repository_diff(self):
        class FailingAppServer:
            configured = True
            provider = "Codex App Server"

            def assert_subscription_access(self, *, force=True):
                return {"allowed": True, "planType": "pro"}

            def run_turn(self, prompt, **kwargs):
                kwargs["on_thread_started"](
                    "thread-failed-1", "session-failed-1"
                )
                kwargs["on_turn_started"]("thread-failed-1", "turn-failed-1")
                raise RuntimeError("turn crashed")

        changed_path = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/patch.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=FailingAppServer(),
            )
            snapshots = iter([{}, {changed_path: "sha256:partial"}])
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertIn("turn crashed", job["error"])
        self.assertIn(str(changed_path), job["error"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["result"]["changedFiles"], [str(changed_path)])
        self.assertEqual(run["threadId"], "thread-failed-1")
        self.assertEqual(run["turnId"], "turn-failed-1")

    def test_review_qualification_scope_investigates_broadly_but_writes_anchor_group(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = FakeWorkflow()
            workflow.inventory = MultiGroupSourceInventory()
            coordinator = QualificationRunCoordinator(
                Path(directory),
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            groups = coordinator._review_target_group_ids(
                {
                    "qualification": "new-exam",
                    "listGroupId": "2026",
                },
                {"investigationScope": "qualification"},
            )

        self.assertEqual(groups, ["2026"])

    def test_review_cannot_write_across_qualifications_in_one_session(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            with self.assertRaisesRegex(QualificationRunError, "1資格ずつ"):
                coordinator._review_target_group_ids(
                    {"qualification": "sample", "listGroupId": "2026"},
                    {"investigationScope": "all_qualifications"},
                )

    def test_source_only_qualification_starts_setup_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, SourceOnlyInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("new-exam", "setup", "remaining")
            started = coordinator.start(
                "new-exam", "setup", "remaining", preview["previewToken"]
            )

        self.assertEqual(preview["targetCount"], 1)
        self.assertIn("prompt/qualification_docs/README.md", preview["canonicalDocs"])
        self.assertEqual(preview["sourceFileCount"], 1)
        self.assertEqual(preview["outputFileCount"], 3)
        self.assertEqual(started["run"]["stageId"], "setup")
        self.assertIn("qualification_docs/new-exam", started["prompt"])
        self.assertIn("## 完了記録", started["prompt"])
        self.assertIn("result.json", started["prompt"])
        self.assertNotIn("## 問題文", started["prompt"])

    def test_multi_stage_year_refresh_is_saved_as_one_human_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, MultiGroupSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            stage_ids = ["question_type", "question_intent", "correct_choice"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "group_refresh",
                stage_ids=stage_ids,
                list_group_ids=["2025", "2026"],
            )
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "group_refresh",
                preview["previewToken"],
                stage_ids=stage_ids,
                list_group_ids=["2025", "2026"],
            )

        self.assertEqual(preview["stageId"], "multi")
        self.assertEqual(preview["stageIds"], stage_ids)
        self.assertEqual(preview["stageCount"], 3)
        self.assertEqual(preview["targetCount"], 2)
        self.assertEqual(preview["workItemCount"], 6)
        self.assertEqual(preview["targetGroupIds"], ["2025", "2026"])
        self.assertEqual(preview["scopeListGroupIds"], ["2025", "2026"])
        self.assertEqual(started["run"]["stageIds"], stage_ids)
        self.assertEqual(started["run"]["workItemCount"], 6)
        self.assertIsNone(started["run"]["scopeListGroupId"])
        self.assertEqual(started["run"]["scopeListGroupIds"], ["2025", "2026"])
        self.assertIn("対象listGroupId: `2025`, `2026`", started["prompt"])
        self.assertIn("一問を読み、その問題について選択工程", started["prompt"])

    def test_human_run_persists_prompt_and_can_resume_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = FakeWorkflow()
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            restarted = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            recent = restarted.recent("sample")
            resumed = restarted.resume_prompt(
                "sample", started["run"]["runId"]
            )

        self.assertEqual(started["run"]["status"], "awaiting_changes")
        self.assertIsNone(started["job"])
        self.assertIsNone(recent["activeRun"])
        self.assertEqual(recent["runs"][0]["runId"], started["run"]["runId"])
        self.assertIn("資格単位の問題整備", resumed["prompt"])

    def test_human_run_converges_after_valid_result_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            receipt_path = root / started["run"]["resultReceiptPath"]
            receipt_path.write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "summary": "全対象を監査した。",
                        "commands": [
                            {"command": "python check.py", "status": "pass"}
                        ],
                        "changedFiles": ["output/sample/patch.json"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            recent = coordinator.recent("sample")

        self.assertIsNone(recent["activeRun"])
        self.assertEqual(recent["runs"][0]["status"], "succeeded")
        self.assertEqual(recent["runs"][0]["result"]["summary"], "全対象を監査した。")

    def test_invalid_success_receipt_does_not_complete_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            receipt_path = root / started["run"]["resultReceiptPath"]
            receipt_path.write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "summary": "検証していない。",
                        "commands": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            recent = coordinator.recent("sample")

        self.assertIsNone(recent["activeRun"])
        self.assertEqual(recent["runs"][0]["status"], "awaiting_changes")
        self.assertIn("pass検証", recent["runs"][0]["receiptError"])

    def test_result_receipt_path_in_manifest_cannot_redirect_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            redirected = root / "outside-result.json"
            redirected.write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "summary": "外部",
                        "commands": [{"command": "fake", "status": "pass"}],
                    }
                ),
                encoding="utf-8",
            )
            coordinator.store.update(
                "sample",
                started["run"]["runId"],
                resultReceiptPath=str(redirected),
            )
            recent = coordinator.recent("sample")

        self.assertIsNone(recent["activeRun"])
        self.assertEqual(recent["runs"][0]["status"], "awaiting_changes")

    def test_delivery_run_processes_all_groups_and_records_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synchronizer = FakeSynchronizer()
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root, FakeWorkflow(), synchronizer, jobs, "secret"
            )
            preview = coordinator.preview("sample", "delivery", "refresh")
            started = coordinator.start(
                "sample", "delivery", "refresh", preview["previewToken"]
            )
            job_id = started["job"]["jobId"]
            deadline = time.monotonic() + 2
            job = jobs.get(job_id)
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(job_id)
            recent = coordinator.recent("sample")

        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(
            synchronizer.calls,
            [("sample", "2025", True), ("sample", "2026", True)],
        )
        self.assertEqual(recent["runs"][0]["status"], "succeeded")
        self.assertEqual(recent["runs"][0]["completedGroupIds"], ["2025", "2026"])

    def test_running_manifest_becomes_resumable_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            run = store.create(
                FakeWorkflow().plan("sample", "delivery", "remaining"),
                status="running",
            )
            recovered = QualificationRunStore(root).list("sample")

        self.assertEqual(recovered[0]["runId"], run["runId"])
        self.assertEqual(recovered[0]["status"], "interrupted")
        self.assertIn("再開", recovered[0]["error"])

    def test_running_human_manifest_recovers_partial_delta_from_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_root = (
                root
                / "output/sample/questions_json/2026/21_explanationText_added"
            )
            patch_root.mkdir(parents=True)
            deleted = patch_root / "deleted.json"
            deleted.write_text("before\n", encoding="utf-8")
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan["stageIds"] = ["law_audit"]
            plan["workType"] = "maintenance"
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (
                    patch_root,
                    (root / run["resultReceiptPath"]).parent,
                ),
            )
            deleted.unlink()
            created = patch_root / "partial.json"
            created.write_text("partial\n", encoding="utf-8")

            recovered = QualificationRunStore(root).get("sample", run["runId"])

        self.assertEqual(recovered["status"], "failed")
        self.assertFalse(recovered["deltaUnknown"])
        self.assertEqual(
            recovered["result"]["changedFiles"],
            sorted(
                [
                    str(deleted.relative_to(root)),
                    str(created.relative_to(root)),
                ]
            ),
        )

    def test_running_human_without_baseline_is_unknown_and_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan["stageIds"] = ["law_audit"]
            plan["workType"] = "maintenance"
            run = store.create(plan, status="running", prompt="work")

            recovered = QualificationRunStore(root).get("sample", run["runId"])

        self.assertEqual(recovered["status"], "interrupted")
        self.assertTrue(recovered["deltaUnknown"])

    def test_resume_preview_excludes_completed_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            previous = store.create(
                FakeWorkflow().plan("sample", "delivery", "refresh"),
                status="failed",
            )
            store.update(
                "sample",
                previous["runId"],
                completedGroupIds=["2025"],
                error="2026で失敗",
            )
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )
            preview = coordinator.preview(
                "sample",
                "delivery",
                "refresh",
                resumed_from=previous["runId"],
            )

        self.assertEqual(preview["targetGroupIds"], ["2026"])
        self.assertEqual(preview["targetCount"], 1)


if __name__ == "__main__":
    unittest.main()
