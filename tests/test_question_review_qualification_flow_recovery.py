from tests.qualification_run_test_support import *  # noqa: F403
from tools.question_review_console.qualification_runs import (
    _derive_resume_merge_dependencies,
)


class QualificationFlowRecoveryTests(QualificationRunTestSupport):

    @staticmethod
    def _queue_recovery_plan(*, stage_status="committing", stage_ids=None):
        stages = list(stage_ids or ["question_type"])
        identity = {
            "id": "q1",
            "questionKey": "q1",
            "sourceQuestionKey": "source-q1",
            "reviewQuestionId": "review-q1",
            "sourceRecordRef": "record-q1",
            "listGroupId": "2026",
            "displayLabel": "2026 問1",
        }
        plan = FakeWorkflow().plan("sample", stages[0], "remaining")
        plan.update(
            kind="orchestration",
            workType="maintenance_flow",
            stageId="multi",
            stageIds=stages,
            confirmedGroupIds=[],
            questionExecutions=[
                {
                    **identity,
                    "questionId": "q1",
                    "status": stage_status,
                    "stages": [
                        {
                            "workItemKey": f"work-{stage_id}",
                            "stageId": stage_id,
                            "status": (
                                stage_status if index == len(stages) - 1 else "validated"
                            ),
                            "childRunIds": [],
                            "error": None,
                        }
                        for index, stage_id in enumerate(stages)
                    ],
                }
            ],
            phaseExecutions=[
                {
                    "id": stage_id,
                    "index": index,
                    "label": stage_id,
                    "stageIds": [stage_id],
                    "status": "running",
                }
                for index, stage_id in enumerate(stages)
            ],
        )
        return plan, identity

    @staticmethod
    def _create_completed_child(store, parent, identity, *, identity_override=None):
        child_identity = dict(identity)
        child_identity.update(identity_override or {})
        child_plan = FakeWorkflow().plan("sample", "question_type", "remaining")
        child_plan.update(
            parentRunId=parent["runId"],
            flowPhaseId="question_type",
            stageId="question_type",
            stageIds=["question_type"],
            targetCount=1,
            progressTargets=[child_identity],
        )
        child = store.create(child_plan, status="succeeded", prompt="child")
        store.update(
            "sample",
            child["runId"],
            receiptValidated=True,
            result={
                "status": "succeeded",
                "summary": "一問を確定しました。",
                "commands": [],
                "changedFiles": [],
            },
            deltaUnknown=False,
            workVersionReceipt={"recordedCount": 1, "items": ["q1"]},
        )
        store.update_question_stage(
            "sample",
            parent["runId"],
            "q1",
            "question_type",
            status="committing",
            childRunIds=[child["runId"]],
        )
        return child

    def test_every_top_maintenance_stage_has_its_own_session_phase(self):
        stage_ids = [
            "question_type",
            "question_intent",
            "correct_choice",
            "law_context",
            "explanation",
            "law_audit",
            "category_setup",
            "question_set",
        ]
        plan = {
            "stagePlans": [
                {
                    "stageId": stage_id,
                    "stageLabel": stage_id,
                    "stageCode": str(index),
                    "sessionGroup": (
                        "maintenance"
                        if index <= 5
                        else "law_audit"
                        if index == 6
                        else "question_set"
                    ),
                    "sessionLabel": (
                        "問題を整備"
                        if index <= 5
                        else "現行法を監査"
                        if index == 6
                        else "問題集を整備"
                    ),
                }
                for index, stage_id in enumerate(stage_ids, start=1)
            ]
        }

        phases = _maintenance_session_phases(plan)

        self.assertEqual([phase["id"] for phase in phases], stage_ids)
        self.assertEqual(
            [phase["sessionGroup"] for phase in phases],
            ["maintenance"] * 5 + ["law_audit", "question_set", "question_set"],
        )

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

    def test_top_maintenance_uses_fresh_writer_sessions_for_separate_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []

            def write_law_sidecar(work_type):
                if work_type != "maintenance_law_audit":
                    return
                self._write_law_audit_sidecar(
                    root,
                    "2026",
                    [
                        {
                            "reviewQuestionId": "new-exam-2026-q1",
                            "isLawRelated": True,
                            "auditStatus": "same_as_current",
                            "reviewState": "secondary_verified",
                            "lawReferences": [
                                [
                                    {
                                        "lawTitle": "ガス事業法",
                                        "lawId": "329AC0000000051",
                                        "article": "2",
                                        "verificationStatus": "verified",
                                    }
                                ]
                            ],
                        }
                    ],
                )

            app_server = FlowAppServer(
                events=events,
                before_receipt=write_law_sidecar,
            )
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            original_merge = synchronizer.refresh_merged_views

            def refresh_merged_views(qualification, list_group_id, emit):
                events.append("merge")
                return original_merge(qualification, list_group_id, emit)

            synchronizer.refresh_merged_views = refresh_merged_views
            original_sync = synchronizer.run

            def run_sync(qualification, list_group_id, token, emit, *, force=False):
                events.append("final-sync")
                return original_sync(
                    qualification, list_group_id, token, emit, force=force
                )

            synchronizer.run = run_sync
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            stage_ids = ["question_type", "law_audit"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "outdated",
                stage_ids=stage_ids,
                list_group_ids=["2026"],
            )
            self.assertEqual(preview["scopeListGroupIds"], ["2026"])
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            job = self._wait_for_job(jobs, started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])
            recent = coordinator.recent("new-exam")

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["workType"], "maintenance_flow")
        self.assertEqual(
            [item["id"] for item in run["phaseExecutions"]],
            ["question_type", "law_audit"],
        )
        self.assertTrue(
            all(item["status"] == "succeeded" for item in run["phaseExecutions"])
        )
        self.assertEqual(len(run["childRunIds"]), 2)
        sessions = {item["sessionId"] for item in run["phaseExecutions"]}
        threads = {item["threadId"] for item in run["phaseExecutions"]}
        self.assertEqual(len(sessions), 2)
        self.assertEqual(len(threads), 2)
        self.assertEqual(
            [kwargs["work_type"] for _, kwargs in app_server.calls],
            [
                "maintenance_prepare_question_type",
                "maintenance_question_type",
                "maintenance_prepare_law_audit",
                "maintenance_law_audit",
            ],
        )
        self.assertEqual(synchronizer.merge_calls, [("new-exam", "2026")])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])
        self.assertEqual(
            events,
            [
                "session:maintenance_prepare_question_type",
                "session:maintenance_question_type",
                "merge",
                "session:maintenance_prepare_law_audit",
                "session:maintenance_law_audit",
                "final-sync",
            ],
        )
        self.assertEqual(recent["runs"][0]["runId"], run["runId"])
        self.assertTrue(all(not item.get("parentRunId") for item in recent["runs"]))

    def test_top_maintenance_keeps_validated_work_when_final_sync_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            synchronizer.can_sync = False
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                JobManager(),
                "secret",
            )
            parent_plan = FakeWorkflow().plan("sample", "law_audit")
            parent_plan.update(
                {
                    "stageId": "multi",
                    "stageIds": ["law_audit"],
                    "stageCode": "03b",
                    "stageLabel": "トップ整備",
                    "workType": "maintenance_flow",
                    "phaseExecutions": [
                        {
                            "id": "law_audit",
                            "index": 0,
                            "label": "現行法監査",
                            "stageIds": ["law_audit"],
                            "stageCodes": ["03b"],
                            "status": "pending",
                        }
                    ],
                }
            )
            parent = coordinator.store.create(parent_plan, status="queued")
            phase_plan = FakeWorkflow().plan("sample", "law_audit")
            phase_plan.update(
                {
                    "workType": "maintenance_law_audit",
                    "parentRunId": parent["runId"],
                    "flowPhaseId": "law_audit",
                    "phaseIndex": 0,
                }
            )
            coordinator._flow_phase_plan_prompt = (
                lambda _parent, _phase: (phase_plan, "phase prompt")
            )

            def complete_child(qualification, run_id, *_args, **_kwargs):
                coordinator.store.update(
                    qualification,
                    run_id,
                    status="succeeded",
                    receiptValidated=True,
                    workVersionReceipt={"recordedCount": 3},
                    artifactSync={"status": "deferred", "groups": []},
                )

            coordinator._run_human = complete_child

            result = coordinator._run_maintenance_flow(
                "sample", parent["runId"], lambda _message: None
            )
            run = coordinator.store.refresh("sample", parent["runId"])

        self.assertTrue(result["warning"])
        self.assertEqual(result["artifactSync"]["status"], "blocked")
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["artifactSync"]["status"], "blocked")
        self.assertIsNone(run["receiptError"])
        self.assertIsNone(run["error"])
        self.assertEqual(synchronizer.calls, [])

    def test_scope_phase_failure_fails_parent_before_question_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synchronizer = FakeSynchronizer()
            app_server = FlowAppServer(fail_on_writer=1)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                JobManager(),
                "secret",
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            parent_plan = FakeWorkflow().plan("sample", "category_setup")
            parent_plan.update(
                {
                    "stageId": "multi",
                    "stageIds": ["category_setup"],
                    "workType": "maintenance_flow",
                    "phaseExecutions": [
                        {
                            "id": "category_setup",
                            "index": 0,
                            "label": "カテゴリ準備",
                            "stageIds": ["category_setup"],
                            "stageCodes": ["03c"],
                            "status": "pending",
                        }
                    ],
                }
            )
            parent = coordinator.store.create(parent_plan, status="queued")
            phase_plan = FakeWorkflow().plan("sample", "category_setup")
            phase_plan.update(
                {
                    "workType": "maintenance_category_setup",
                    "parentRunId": parent["runId"],
                    "flowPhaseId": "category_setup",
                    "phaseIndex": 0,
                }
            )
            coordinator._flow_phase_plan_prompt = (
                lambda _parent, _phase: (phase_plan, "phase prompt")
            )

            with self.assertRaisesRegex(QualificationRunError, "カテゴリ準備で停止"):
                coordinator._run_maintenance_flow(
                    "sample", parent["runId"], lambda _message: None
                )
            run = coordinator.store.get("sample", parent["runId"])

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["phaseExecutions"][0]["status"], "failed")
        self.assertEqual(synchronizer.calls, [])

    def test_top_maintenance_keeps_validated_work_when_final_result_write_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            parent_plan = FakeWorkflow().plan("sample", "law_audit")
            parent_plan.update(
                {
                    "stageId": "multi",
                    "stageIds": ["law_audit"],
                    "stageCode": "03b",
                    "stageLabel": "トップ整備",
                    "workType": "maintenance_flow",
                    "phaseExecutions": [
                        {
                            "id": "law_audit",
                            "index": 0,
                            "label": "現行法監査",
                            "stageIds": ["law_audit"],
                            "stageCodes": ["03b"],
                            "status": "pending",
                        }
                    ],
                }
            )
            parent = coordinator.store.create(parent_plan, status="queued")
            phase_plan = FakeWorkflow().plan("sample", "law_audit")
            phase_plan.update(
                {
                    "workType": "maintenance_law_audit",
                    "parentRunId": parent["runId"],
                    "flowPhaseId": "law_audit",
                    "phaseIndex": 0,
                }
            )
            coordinator._flow_phase_plan_prompt = (
                lambda _parent, _phase: (phase_plan, "phase prompt")
            )

            def complete_child(qualification, run_id, *_args, **_kwargs):
                coordinator.store.update(
                    qualification,
                    run_id,
                    status="succeeded",
                    receiptValidated=True,
                    workVersionReceipt={"recordedCount": 3},
                    artifactSync={"status": "deferred", "groups": []},
                )

            coordinator._run_human = complete_child
            original_write_result = coordinator.store.write_result
            failed_once = False

            def flaky_write_result(qualification, run_id, result):
                nonlocal failed_once
                if run_id == parent["runId"] and not failed_once:
                    failed_once = True
                    raise OSError("simulated final receipt write failure")
                return original_write_result(qualification, run_id, result)

            coordinator.store.write_result = flaky_write_result

            result = coordinator._run_maintenance_flow(
                "sample", parent["runId"], lambda _message: None
            )
            run = coordinator.store.refresh("sample", parent["runId"])

        self.assertTrue(result["warning"])
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["artifactSync"]["status"], "failed")
        self.assertEqual(run["result"]["status"], "succeeded")
        self.assertIsNone(run["error"])

    def test_flow_phase_recomputes_failed_delta_after_specializing_work_type(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            coordinator._plan = lambda *_args, **_kwargs: {
                "targetCount": 1,
                "targetGroupIds": ["2026"],
                "workType": "maintenance",
            }
            coordinator._resolvable_for_plan = (
                lambda _qualification, _group_ids, plan: (
                    ["resolved-by-law-audit"]
                    if plan.get("workType") == "maintenance_law_audit"
                    else []
                )
            )

            plan, _prompt = coordinator._flow_phase_plan_prompt(
                {
                    "qualification": "sample",
                    "mode": "outdated",
                    "scopeListGroupIds": [],
                    "runId": "parent-run",
                },
                {
                    "id": "law_audit",
                    "index": 0,
                    "stageIds": ["law_audit"],
                },
            )

        self.assertEqual(plan["workType"], "maintenance_law_audit")
        self.assertEqual(
            plan["resolvableFailedDeltaPaths"],
            ["resolved-by-law-audit"],
        )

    def test_flow_phase_promotes_to_group_refresh_for_failed_aggregate_delta(self):
        class ScopedWorkflow(FakeWorkflow):
            def prompt(self, qualification, stage_id, mode="remaining", **_scope):
                return {
                    "qualification": qualification,
                    "stageId": stage_id,
                    "mode": mode,
                    "targetCount": 3,
                    "prompt": f"prompt:{mode}",
                }

        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                ScopedWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                app_server=ConfiguredAppServer(),
            )

            def phase_plan(_qualification, _stage_id, mode, _resumed, **_scope):
                return {
                    "targetCount": 58 if mode == "group_refresh" else 34,
                    "targetGroupIds": ["2026"],
                    "workType": "maintenance",
                    "mode": mode,
                }

            coordinator._plan = phase_plan
            coordinator._resolvable_for_plan = (
                lambda _qualification, _group_ids, plan: (
                    ["failed-aggregate.json"]
                    if plan.get("workType") == "maintenance_law_audit"
                    and plan.get("targetCount") == 58
                    else []
                )
            )

            plan, prompt = coordinator._flow_phase_plan_prompt(
                {
                    "qualification": "sample",
                    "mode": "outdated",
                    "scopeListGroupIds": ["2026"],
                    "runId": "parent-run",
                },
                {
                    "id": "law_audit",
                    "index": 0,
                    "stageIds": ["law_audit"],
                },
            )

        self.assertEqual(plan["mode"], "group_refresh")
        self.assertEqual(plan["targetCount"], 58)
        self.assertEqual(
            plan["resolvableFailedDeltaPaths"],
            ["failed-aggregate.json"],
        )
        self.assertEqual(prompt, "prompt:group_refresh")

    def test_top_maintenance_skips_current_phase_before_outdated_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = LawSourceInventory()
            workflow = QualificationWorkflow(root, inventory)
            question = inventory.group("new-exam", "2026")["questions"][0]
            workflow.work_versions.record_stage(
                [question],
                workflow.versioned_policies("new-exam")["question_type"],
                run_id="completed-question-type",
                source="test",
            )

            def write_law_sidecar(work_type):
                if work_type != "maintenance_law_audit":
                    return
                self._write_law_audit_sidecar(
                    root,
                    "2026",
                    [
                        {
                            "reviewQuestionId": "new-exam-2026-q1",
                            "isLawRelated": True,
                            "auditStatus": "same_as_current",
                            "reviewState": "secondary_verified",
                            "lawReferences": [
                                [
                                    {
                                        "lawTitle": "ガス事業法",
                                        "lawId": "329AC0000000051",
                                        "article": "2",
                                        "verificationStatus": "verified",
                                    }
                                ]
                            ],
                        }
                    ],
                )

            app_server = FlowAppServer(before_receipt=write_law_sidecar)
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            stage_ids = ["question_type", "law_audit"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "outdated",
                stage_ids=stage_ids,
                list_group_ids=["2026"],
            )
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            job = self._wait_for_job(jobs, started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])

        self.assertEqual(preview["targetCount"], 1)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(
            [item["status"] for item in run["phaseExecutions"]],
            ["skipped", "succeeded"],
        )
        self.assertEqual(len(run["childRunIds"]), 1)
        self.assertEqual(
            [kwargs["work_type"] for _, kwargs in app_server.calls],
            ["maintenance_prepare_law_audit", "maintenance_law_audit"],
        )
        self.assertEqual(synchronizer.merge_calls, [])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

    def test_top_maintenance_blocks_only_failed_question_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer(fail_on_writer=2)
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            stage_ids = ["question_type", "law_audit"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "outdated",
                stage_ids=stage_ids,
                list_group_ids=["2026"],
            )
            self.assertEqual(preview["scopeListGroupIds"], ["2026"])
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            job = self._wait_for_job(jobs, started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["queueStatus"], "partial")
        self.assertEqual(
            [item["status"] for item in run["phaseExecutions"]],
            ["succeeded", "partial"],
            job,
        )
        self.assertEqual(len(run["childRunIds"]), 2)
        self.assertEqual(len(app_server.calls), 4)
        self.assertEqual(synchronizer.merge_calls, [("new-exam", "2026")])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])
        self.assertEqual(run["workVersionReceipt"]["recordedCount"], 1)
        self.assertEqual(run["blockedQuestionCount"], 1)
        self.assertEqual(run["validatedWorkItemCount"], 1)
        self.assertIn(
            "phase 2 failed",
            run["questionExecutions"][0]["stages"][1]["error"],
        )
        self.assertIsNone(run["error"])

    def test_per_question_queue_continues_sibling_after_one_writer_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed_question_id = "new-exam-2026-q1"
            app_server = PerQuestionQueueAppServer(
                failed_question_id=failed_question_id
            )
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            workflow = QualificationWorkflow(root, TwoQuestionSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
            )
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            job = self._wait_for_job(jobs, started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])
            first_calls = list(app_server.calls)
            app_server.calls.clear()
            app_server.failed_question_id = ""
            app_server.wait_for_parallel_preparations = False
            retry_preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
                resumed_from=run["runId"],
            )
            retried = coordinator.start(
                "new-exam",
                retry_preview["stageId"],
                "outdated",
                retry_preview["previewToken"],
                list_group_ids=retry_preview["scopeListGroupIds"],
                resumed_from=run["runId"],
            )
            retry_job = self._wait_for_job(jobs, retried["job"]["jobId"])
            retry_run = coordinator.store.get(
                "new-exam", retried["run"]["runId"]
            )
            retry_calls = list(app_server.calls)

        questions = {
            item["questionId"]: item for item in run["questionExecutions"]
        }
        calls_by_type = {}
        for question_id, prompt, kwargs in first_calls:
            calls_by_type.setdefault(kwargs["work_type"], []).append(
                (question_id, prompt)
            )

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["queueStatus"], "partial")
        self.assertEqual(run["blockedQuestionCount"], 1)
        self.assertEqual(run["validatedQuestionCount"], 1)
        self.assertEqual(
            questions[failed_question_id]["stages"][0]["status"],
            "blocked",
        )
        self.assertIn(
            "writer検証に失敗",
            questions[failed_question_id]["stages"][0]["error"],
        )
        succeeded_question_id = "new-exam-2026-q2"
        self.assertEqual(
            questions[succeeded_question_id]["stages"][0]["status"],
            "validated",
        )
        self.assertEqual(app_server.max_active_preparations, 2)
        self.assertEqual(app_server.max_active_writers, 1)
        self.assertEqual(
            sorted(
                question_id
                for question_id, _prompt in calls_by_type[
                    "maintenance_prepare_question_type"
                ]
            ),
            [failed_question_id, succeeded_question_id],
        )
        self.assertEqual(
            [
                question_id
                for question_id, _prompt in calls_by_type[
                    "maintenance_question_type"
                ]
            ],
            [failed_question_id, succeeded_question_id],
        )
        succeeded_writer_prompt = calls_by_type["maintenance_question_type"][1][1]
        self.assertIn(
            f"{succeeded_question_id}の読取専用の判断案",
            succeeded_writer_prompt,
        )
        self.assertNotIn(
            f"{failed_question_id}の読取専用の判断案",
            succeeded_writer_prompt,
        )
        self.assertEqual(run["workVersionReceipt"]["recordedCount"], 1)
        self.assertEqual(
            synchronizer.calls,
            [
                ("new-exam", "2026", True),
                ("new-exam", "2026", True),
            ],
        )
        self.assertIsNone(run["error"])
        self.assertEqual(retry_preview["targetCount"], 1)
        self.assertEqual(retry_preview["workItemCount"], 1)
        self.assertEqual(retry_job["status"], "succeeded", retry_job)
        self.assertEqual(retry_run["status"], "succeeded")
        self.assertEqual(retry_run["queueStatus"], "succeeded")
        self.assertEqual(retry_run["resumedFrom"], run["runId"])
        self.assertEqual(
            [item["questionId"] for item in retry_run["questionExecutions"]],
            [failed_question_id],
        )
        self.assertEqual(
            retry_run["questionExecutions"][0]["stages"][0]["status"],
            "validated",
        )
        self.assertEqual(
            [
                (question_id, kwargs["work_type"])
                for question_id, _prompt, kwargs in retry_calls
            ],
            [
                (failed_question_id, "maintenance_prepare_question_type"),
                (failed_question_id, "maintenance_question_type"),
            ],
        )

    def test_top_maintenance_prepares_category_then_uses_separate_question_set_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category_path = Path("output/new-exam/category/category.json")

            def write_category(work_type):
                if work_type != "maintenance_category_setup":
                    return
                absolute = root / category_path
                absolute.parent.mkdir(parents=True, exist_ok=True)
                absolute.write_text(
                    json.dumps(
                        {
                            "folders": [{"folderId": "folder-1"}],
                            "questionSets": [
                                {
                                    "questionSetId": "set-1",
                                    "folderId": "folder-1",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            app_server = FlowAppServer(
                changed_files_by_work_type={
                    "maintenance_category_setup": [category_path.as_posix()]
                },
                before_receipt=write_category,
            )
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            snapshots = iter(
                [
                    {},
                    {category_path: "sha256:category"},
                    {category_path: "sha256:category"},
                    {category_path: "sha256:category"},
                ]
            )
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            stage_ids = ["category_setup", "question_set"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "outdated",
                stage_ids=stage_ids,
                list_group_ids=["2026"],
            )
            self.assertEqual(preview["scopeListGroupIds"], ["2026"])
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            job = self._wait_for_job(jobs, started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(
            [item["id"] for item in run["phaseExecutions"]],
            ["category_setup", "question_set"],
        )
        self.assertEqual(
            len({item["sessionId"] for item in run["phaseExecutions"]}),
            2,
        )
        self.assertEqual(
            [kwargs["work_type"] for _, kwargs in app_server.calls],
            [
                "maintenance_category_setup",
                "maintenance_prepare_question_set",
                "maintenance_question_set",
            ],
        )
        self.assertEqual(run["stageIds"], preview["stageIds"])
        self.assertEqual(run["scopeListGroupIds"], preview["scopeListGroupIds"])
        self.assertEqual(run["targetCount"], preview["targetCount"])
        self.assertEqual(run["workItemCount"], preview["workItemCount"])
        self.assertEqual(synchronizer.merge_calls, [])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

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
        self.assertTrue(recent["runs"][0]["receiptValidated"])
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

    def test_result_receipt_normalizes_past_tense_command_statuses(self):
        succeeded = QualificationRunStore._validated_result_receipt(
            {
                "status": "succeeded",
                "summary": "検証済み。",
                "commands": [{"command": "check", "status": "passed"}],
            }
        )
        failed = QualificationRunStore._validated_result_receipt(
            {
                "status": "failed",
                "summary": "検証失敗。",
                "commands": [{"command": "check", "status": "failed"}],
            }
        )

        self.assertEqual(succeeded["commands"][0]["status"], "pass")
        self.assertEqual(failed["commands"][0]["status"], "fail")

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
            job = self._wait_for_job(jobs, job_id, timeout=2)
            recent = coordinator.recent("sample")

        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(
            synchronizer.calls,
            [("sample", "2025", True), ("sample", "2026", True)],
        )
        self.assertEqual(recent["runs"][0]["status"], "succeeded")
        self.assertTrue(recent["runs"][0]["receiptValidated"])
        self.assertEqual(
            recent["runs"][0]["artifactSync"]["status"], "succeeded"
        )
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

    def test_merge_exception_isolated_only_after_source_immutability_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synchronizer = FakeSynchronizer()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                JobManager(),
                "secret",
            )
            plan, _identity = self._queue_recovery_plan(stage_status="queued")
            parent = coordinator.store.create(plan, status="running")
            synchronizer.refresh_merged_views = (
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("merge crash")
                )
            )
            coordinator._check_source_immutability = lambda _emit: None

            result = coordinator._refresh_merged_views_transaction(
                "sample",
                parent["runId"],
                "2026",
                "question_type",
                lambda _message: None,
            )
            recovered = coordinator.store.get("sample", parent["runId"])

            self.assertEqual(result["status"], "failed")
            self.assertEqual(
                recovered["resumeMergeDependencies"][0]["status"], "failed"
            )

            coordinator._check_source_immutability = (
                lambda _emit: (_ for _ in ()).throw(
                    QualificationRunError("00_source不変検証に失敗")
                )
            )
            with self.assertRaisesRegex(QualificationRunError, "00_source"):
                coordinator._refresh_merged_views_transaction(
                    "sample",
                    parent["runId"],
                    "2026",
                    "question_type",
                    lambda _message: None,
                )

    def test_running_merge_restart_requires_remerge_before_resumed_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan, identity = self._queue_recovery_plan(
                stage_status="queued",
                stage_ids=["question_type", "explanation"],
            )
            plan["resumeMergeDependencies"] = [
                {
                    "listGroupId": "2026",
                    "afterStageId": "question_type",
                    "status": "running",
                    "message": "merge中",
                }
            ]
            parent = store.create(plan, status="running")

            recovered_store = QualificationRunStore(root)
            recovered = recovered_store.get("sample", parent["runId"])
            recovered_stage = recovered["questionExecutions"][0]["stages"][1]
            self.assertEqual(recovered["status"], "interrupted")
            self.assertEqual(recovered["queueStatus"], "partial")
            self.assertTrue(recovered["retrySafe"])
            self.assertEqual(recovered_stage["status"], "blocked")
            self.assertEqual(
                recovered["resumeMergeDependencies"][0]["status"],
                "interrupted",
            )
            self.assertTrue(
                recovered["resumeMergeDependencies"][0]["remergeRequired"]
            )

            resume_plan = {
                "stageIds": ["question_type", "explanation"],
                "targetGroupIds": ["2026"],
                "stagePlans": [
                    {
                        "stageId": "explanation",
                        "progressTargets": [identity],
                    }
                ],
            }
            dependencies = _derive_resume_merge_dependencies(
                recovered,
                resume_plan,
            )
            self.assertEqual(dependencies[0]["status"], "pending")

            resumed_plan, _ = self._queue_recovery_plan(
                stage_status="queued",
                stage_ids=["question_type", "explanation"],
            )
            resumed_plan["resumeMergeDependencies"] = dependencies
            resumed = recovered_store.create(
                resumed_plan,
                status="running",
                resumed_from=parent["runId"],
            )
            synchronizer = FakeSynchronizer()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                JobManager(),
                "secret",
            )
            coordinator._refresh_resume_merge_dependencies(
                "sample",
                resumed["runId"],
                "explanation",
                lambda _message: None,
                set(),
            )
            after_remerge = coordinator.store.get("sample", resumed["runId"])

        self.assertEqual(synchronizer.merge_calls, [("sample", "2026")])
        self.assertEqual(
            after_remerge["resumeMergeDependencies"][0]["status"],
            "succeeded",
        )

    def test_committing_stage_recovers_from_completed_bound_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan, identity = self._queue_recovery_plan()
            parent = store.create(plan, status="running")
            self._create_completed_child(store, parent, identity)

            recovered = QualificationRunStore(root).get("sample", parent["runId"])

        stage = recovered["questionExecutions"][0]["stages"][0]
        self.assertEqual(stage["status"], "validated")
        self.assertTrue(stage["outputFingerprint"])
        self.assertEqual(recovered["confirmedGroupIds"], ["2026"])
        self.assertEqual(recovered["status"], "succeeded")
        self.assertTrue(recovered["receiptValidated"])
        self.assertEqual(recovered["artifactSync"]["status"], "interrupted")

    def test_committing_stage_identity_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan, identity = self._queue_recovery_plan()
            parent = store.create(plan, status="running")
            child = self._create_completed_child(
                store,
                parent,
                identity,
                identity_override={"sourceRecordRef": "other-record"},
            )

            recovered = QualificationRunStore(root).get("sample", parent["runId"])

        self.assertFalse(recovered["retrySafe"])
        self.assertEqual(recovered["unsafeChildRunId"], child["runId"])
        self.assertEqual(
            recovered["questionExecutions"][0]["stages"][0]["status"],
            "blocked",
        )
        self.assertNotEqual(recovered["status"], "succeeded")

    def test_resumed_flow_keeps_prior_confirmed_groups_and_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synchronizer = FakeSynchronizer()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                JobManager(),
                "secret",
            )
            receipt = {"recordedCount": 2, "items": ["prior"]}
            plan = FakeWorkflow().plan("sample", "question_type", "remaining")
            plan.update(
                kind="orchestration",
                workType="maintenance_flow",
                stageId="multi",
                stageIds=[],
                phaseExecutions=[],
                questionExecutions=[],
                confirmedGroupIds=["2026"],
                workVersionReceipt={
                    "recordedCount": 4,
                    "items": [receipt, receipt],
                },
            )
            parent = coordinator.store.create(
                plan,
                status="queued",
                resumed_from="prior-run",
            )

            coordinator._run_maintenance_flow(
                "sample",
                parent["runId"],
                lambda _message: None,
            )
            recovered = coordinator.store.get("sample", parent["runId"])

        self.assertEqual(recovered["confirmedGroupIds"], ["2026"])
        self.assertEqual(recovered["workVersionReceipt"]["items"], [receipt])
        self.assertEqual(recovered["workVersionReceipt"]["recordedCount"], 2)
        self.assertEqual(synchronizer.calls, [("sample", "2026", True)])

    def test_validated_patch_run_recovers_as_success_when_auto_sync_is_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            run = store.create(
                FakeWorkflow().plan("sample", "law_audit", "remaining"),
                status="validating",
                prompt="work",
            )
            store.update(
                "sample",
                run["runId"],
                receiptValidated=True,
                artifactSync={"status": "running", "groups": []},
            )

            recovered = QualificationRunStore(root).get("sample", run["runId"])

        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["artifactSync"]["status"], "interrupted")
        self.assertIsNone(recovered["error"])

    def test_validated_parent_flow_recovers_as_success_when_auto_sync_is_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan["kind"] = "orchestration"
            plan["workType"] = "maintenance_flow"
            run = store.create(plan, status="validating")
            store.update(
                "sample",
                run["runId"],
                receiptValidated=True,
                artifactSync={"status": "running", "groups": []},
            )

            recovered = QualificationRunStore(root).get(
                "sample", run["runId"]
            )
            persisted = json.loads(
                (
                    root
                    / "output/question_review_console/workflow_runs/sample"
                    / run["runId"]
                    / "manifest.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["artifactSync"]["status"], "interrupted")
        self.assertEqual(recovered["result"]["status"], "succeeded")
        self.assertTrue(persisted["resultReceiptHash"])
        self.assertIsNone(recovered["error"])

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
            restored_content = deleted.read_text(encoding="utf-8")
            created_exists = created.exists()

        self.assertEqual(recovered["status"], "failed")
        self.assertFalse(recovered["deltaUnknown"])
        self.assertEqual(recovered["result"]["changedFiles"], [])
        self.assertEqual(recovered["rollback"]["status"], "succeeded")
        self.assertEqual(restored_content, "before\n")
        self.assertFalse(created_exists)

    def test_rollback_marks_delta_unknown_when_restore_and_resnapshot_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_root = (
                root
                / "output/sample/questions_json/2026/"
                "21_explanationText_added"
            )
            patch_root.mkdir(parents=True)
            (patch_root / "patch.json").write_text(
                "before\n", encoding="utf-8"
            )
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan["stageIds"] = ["law_audit"]
            plan["workType"] = "maintenance"
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline("sample", run["runId"], (patch_root,))
            store._recover_baseline_delta = lambda *_args: None

            with patch(
                "tools.question_review_console.qualification_runs."
                "restore_write_snapshot",
                side_effect=OSError("restore unavailable"),
            ):
                rollback = store.rollback_baseline(
                    "sample", run["runId"]
                )
            recovered = store.get("sample", run["runId"])

        self.assertEqual(rollback["status"], "failed")
        self.assertTrue(rollback["deltaUnknown"])
        self.assertTrue(recovered["deltaUnknown"])

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
    unittest.main()  # noqa: F405
