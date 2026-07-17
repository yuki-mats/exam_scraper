from tests.qualification_run_test_support import *  # noqa: F403
from tools.question_review_console.codex_app_server import CodexAppServerError
from tools.question_review_console.question_work_queue import (
    input_fingerprint,
    specialize_question_plan,
)


class QualificationProgressObservabilityTests(QualificationRunTestSupport):

    def test_technical_log_is_append_only_structured_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
            )
            plan = FakeWorkflow().plan("sample", "delivery")
            run = coordinator.store.create(plan, status="queued")

            def worker(emit):
                emit("Authorization: Bearer should-not-be-saved")
                event = {
                    "level": "error",
                    "message": "command failed: python verify.py",
                    "commandStatus": "failed",
                    "exitCode": 7,
                    "outputTail": "token=should-not-be-saved",
                    "changedPaths": ["output/sample/patch.json"],
                    "thought": "never persist this",
                }
                getattr(emit, "event")(event)
                getattr(emit, "event")(event)
                return {"ok": True}

            started = jobs.start(
                kind="test-log",
                key="test-log",
                worker=lambda emit: coordinator._run_with_technical_log(
                    "sample",
                    run["runId"],
                    emit,
                    worker,
                ),
            )
            job = self._wait_for_job(jobs, started["jobId"], timeout=2)
            log_path = root / run["technicalLogPath"]
            first_bytes = log_path.read_bytes()
            coordinator.store.append_technical_log(
                "sample", run["runId"], {"message": "last event"}
            )
            final_bytes = log_path.read_bytes()
            events = [
                json.loads(line)
                for line in final_bytes.decode("utf-8").splitlines()
            ]

        self.assertEqual(job["status"], "succeeded")
        self.assertTrue(final_bytes.startswith(first_bytes))
        self.assertEqual([event["sequence"] for event in events], [1, 2, 3])
        self.assertTrue(all(event["observedAt"] for event in events))
        self.assertEqual(events[1]["commandStatus"], "failed")
        self.assertEqual(events[1]["exitCode"], 7)
        self.assertEqual(events[1]["changedPaths"], ["output/sample/patch.json"])
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("should-not-be-saved", serialized)
        self.assertNotIn("thought", serialized)
        self.assertIn("<redacted sensitive content>", serialized)

    def test_technical_log_failure_does_not_fail_the_run(self):
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
                prompt="work",
            )
            emitted: list[str] = []

            with patch.object(
                coordinator.store,
                "append_technical_log",
                side_effect=OSError("read only"),
            ):
                result = coordinator._run_with_technical_log(
                    "sample",
                    run["runId"],
                    emitted.append,
                    lambda emit: (emit("working"), {"ok": True})[1],
                )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(emitted[0], "working")
        self.assertIn("整備処理は継続します", emitted[1])

    def test_child_heartbeat_updates_parent_run_and_job_activity(self):
        class HeartbeatAppServer(SuccessfulAppServer):
            def run_turn(self, prompt, **kwargs):
                kwargs["heartbeat"]()
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            touched_jobs = []
            original_touch = jobs.touch

            def observe_touch(job_id):
                touched_jobs.append(job_id)
                original_touch(job_id)

            jobs.touch = observe_touch
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=HeartbeatAppServer(),
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            parent_plan = {
                **FakeWorkflow().plan("sample", "law_audit"),
                "kind": "orchestration",
                "workType": "maintenance_flow",
            }
            parent = coordinator.store.create(parent_plan, status="running")
            coordinator.store.update(
                "sample", parent["runId"], heartbeatAt="stale-parent"
            )
            child_plan = {
                **FakeWorkflow().plan("sample", "law_audit"),
                "targetCount": 1,
                "workItemCount": 1,
                "parentRunId": parent["runId"],
            }
            child = coordinator.store.create(
                child_plan,
                status="queued",
                prompt="整備する。",
            )
            prompt = coordinator.store.prompt("sample", child["runId"])
            started = jobs.start(
                kind="heartbeat-test",
                key="heartbeat-test",
                worker=lambda emit: coordinator._run_with_technical_log(
                    "sample",
                    child["runId"],
                    emit,
                    lambda logged_emit: coordinator._run_human(
                        "sample",
                        child["runId"],
                        prompt,
                        "maintenance",
                        logged_emit,
                    ),
                ),
            )
            job = self._wait_for_job(jobs, started["jobId"])
            parent_after = coordinator.store.get("sample", parent["runId"])
            child_after = coordinator.store.get("sample", child["runId"])

        self.assertEqual(job["status"], "succeeded", job)
        self.assertIn(started["jobId"], touched_jobs)
        self.assertNotEqual(parent_after["heartbeatAt"], "stale-parent")
        self.assertTrue(child_after["heartbeatAt"])
        self.assertTrue(job["lastActivityAt"])

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
                "workItemCount": 3,
                "targetGroupIds": ["2026"],
                "targetQuestionKeys": ["sample:2026:q01", "sample:2026:q02"],
                "policyTargets": {
                    "correct_choice": ["source-q1"],
                    "explanation": ["source-q1", "source-q2"],
                },
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
                            {"event": "question_started", "questionId": "ui-q1"},
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
                            {
                                "event": "stage_completed",
                                "questionId": "ui-q2",
                                "stageId": "correct_choice",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "stage_completed", "questionId": "scope外", "stageId": "explanation"},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "question_started", "questionId": "source-q2"},
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            store.update(
                "sample",
                run["runId"],
                status="succeeded",
                receiptValidated=True,
            )
            progress = store.progress("sample", run["runId"])
            prompt = store.prompt("sample", run["runId"])

        self.assertEqual(progress["completedQuestionCount"], 0)
        self.assertEqual(progress["touchedQuestionCount"], 1)
        self.assertEqual(progress["processedQuestionCount"], 0)
        self.assertEqual(progress["completedWorkItemCount"], 1)
        self.assertEqual(progress["percent"], 0)
        self.assertEqual(progress["current"]["questionId"], "ui-q1")
        self.assertEqual(progress["groups"][0]["percent"], 0)
        self.assertEqual(progress["invalidEventCount"], 4)
        self.assertNotIn("privateReasoning", progress["events"][1]["result"])
        self.assertEqual(len(progress["questions"]), 1)
        self.assertFalse(progress["questions"][0]["completed"])
        self.assertEqual(len(progress["questions"][0]["outputs"]), 1)
        self.assertIn("画面用の問題別進捗", prompt)
        self.assertIn("progressTargets", prompt)
        self.assertIn("policyTargets", prompt)

    def test_new_run_rejects_ambiguous_policy_target(self):
        plan = FakeWorkflow().plan("sample", "explanation", "outdated")
        plan.update(
            {
                "targetCount": 2,
                "workItemCount": 2,
                "policyTargets": {"explanation": ["shared-source-key"]},
                "progressTargets": [
                    {
                        "id": f"ui-q{number}",
                        "aliases": ["shared-source-key"],
                    }
                    for number in (1, 2)
                ],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            store = QualificationRunStore(Path(directory))
            with self.assertRaisesRegex(
                QualificationRunError,
                "実行対象ID契約が不正",
            ):
                store.create(plan, status="running", prompt="work")

    def test_progress_marks_ambiguous_old_policy_contract_invalid(self):
        manifest = {
            "runId": "legacy-run",
            "status": "running",
            "targetCount": 2,
            "workItemCount": 2,
            "progressStages": [
                {"id": "explanation", "code": "03", "label": "解説"}
            ],
            "progressTargets": [
                {
                    "id": f"ui-q{number}",
                    "uiQuestionId": f"ui-q{number}",
                    "questionKey": "shared-source-key",
                    "aliases": ["shared-source-key"],
                    "listGroupId": "2026",
                }
                for number in (1, 2)
            ],
            "policyTargets": {"explanation": ["shared-source-key"]},
        }

        progress = QualificationRunStore._parsed_progress(manifest, b"\n")

        self.assertEqual(progress["invalidEventCount"], 1)
        self.assertEqual(progress["processedQuestionCount"], 0)

    def test_progress_keeps_unique_legacy_policy_target_compatible(self):
        manifest = {
            "runId": "legacy-run",
            "status": "running",
            "targetCount": 1,
            "workItemCount": 1,
            "progressStages": [
                {"id": "explanation", "code": "03", "label": "解説"}
            ],
            "progressTargets": [
                {
                    "id": "ui-q1",
                    "uiQuestionId": "ui-q1",
                    "questionKey": "legacy-source-key",
                    "aliases": ["legacy-source-key"],
                    "listGroupId": "2026",
                }
            ],
            "policyTargets": {"explanation": ["legacy-source-key"]},
        }
        events = [
            {"event": "question_started", "questionId": "ui-q1"},
            {
                "event": "stage_completed",
                "questionId": "ui-q1",
                "stageId": "explanation",
            },
            {"event": "question_completed", "questionId": "ui-q1"},
        ]
        raw = "".join(json.dumps(event) + "\n" for event in events).encode()

        progress = QualificationRunStore._parsed_progress(manifest, raw)

        self.assertEqual(progress["invalidEventCount"], 0)
        self.assertEqual(progress["processedQuestionCount"], 1)

    def test_progress_rejects_out_of_order_and_duplicate_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["first", "second", "third"],
                "stageCode": "01 → 02 → 03",
                "stageLabel": "複数工程",
                "mode": "outdated",
                "modeLabel": "未整備のみ",
                "kind": "human",
                "targetCount": 1,
                "workItemCount": 3,
                "targetGroupIds": ["2026"],
                "policyTargets": {
                    "first": ["q1"],
                    "second": ["q1"],
                },
                "progressTargets": [
                    {
                        "id": "q1",
                        "questionKey": "sample:2026:q1",
                        "listGroupId": "2026",
                    }
                ],
                "stagePlans": [
                    {"stageId": "first", "stageCode": "01", "stageLabel": "第一"},
                    {"stageId": "second", "stageCode": "02", "stageLabel": "第二"},
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            run = store.create(plan, status="running", prompt="整備する。")
            progress_path = root / run["progressReceiptPath"]
            raw_events = [
                {"event": "stage_completed", "questionId": "q1", "stageId": "first"},
                {"event": "question_started", "questionId": "q1"},
                {"event": "question_started", "questionId": "q1"},
                {"event": "stage_completed", "questionId": "q1", "stageId": "second"},
                {"event": "stage_completed", "questionId": "q1", "stageId": "first"},
                {"event": "stage_completed", "questionId": "q1", "stageId": "first"},
                {"event": "question_completed", "questionId": "q1"},
                {"event": "stage_completed", "questionId": "q1", "stageId": "second"},
                {"event": "question_completed", "questionId": "q1"},
                {"event": "question_completed", "questionId": "q1"},
            ]
            progress_path.write_text(
                "".join(
                    json.dumps(event, ensure_ascii=False) + "\n"
                    for event in raw_events
                ),
                encoding="utf-8",
            )

            progress = store.progress("sample", run["runId"])

        self.assertEqual(progress["invalidEventCount"], 6)
        self.assertEqual(progress["processedWorkItemCount"], 2)
        self.assertEqual(progress["processedQuestionCount"], 1)
        self.assertEqual(
            [event["event"] for event in progress["events"]],
            [
                "question_started",
                "stage_completed",
                "stage_completed",
                "question_completed",
            ],
        )

    def test_combined_progress_separates_processed_and_validated_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            base_target = {
                "id": "q1",
                "questionKey": "sample:2026:q1",
                "listGroupId": "2026",
            }
            parent_plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["first", "second"],
                "stageCode": "01 → 02",
                "stageLabel": "トップ整備",
                "mode": "outdated",
                "modeLabel": "未整備のみ",
                "kind": "orchestration",
                "workType": "maintenance_flow",
                "targetCount": 1,
                "workItemCount": 2,
                "targetGroupIds": ["2026"],
                "policyTargets": {
                    "first": ["q1"],
                    "second": ["q1"],
                    "third": ["q1"],
                },
                "progressTargets": [base_target],
                "stagePlans": [
                    {"stageId": "first", "stageCode": "01", "stageLabel": "第一"},
                    {"stageId": "second", "stageCode": "02", "stageLabel": "第二"},
                    {"stageId": "third", "stageCode": "03", "stageLabel": "第三"},
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            parent = store.create(parent_plan, status="running")
            child_ids = []
            for stage_id, status, validated in (
                ("first", "succeeded", True),
                ("second", "failed", False),
            ):
                child_plan = {
                    **parent_plan,
                    "stageId": stage_id,
                    "stageIds": [stage_id],
                    "stageCode": "01" if stage_id == "first" else "02",
                    "stageLabel": stage_id,
                    "kind": "human",
                    "workType": f"maintenance_{stage_id}",
                    "parentRunId": parent["runId"],
                    "workItemCount": 1,
                    "policyTargets": {stage_id: ["q1"]},
                    "stagePlans": [
                        {
                            "stageId": stage_id,
                            "stageCode": "01" if stage_id == "first" else "02",
                            "stageLabel": stage_id,
                        }
                    ],
                }
                child = store.create(
                    child_plan,
                    status="running",
                    prompt="整備する。",
                )
                child_ids.append(child["runId"])
                progress_path = root / child["progressReceiptPath"]
                progress_path.write_text(
                    "".join(
                        json.dumps(event, ensure_ascii=False) + "\n"
                        for event in (
                            {"event": "question_started", "questionId": "q1"},
                            {
                                "event": "stage_completed",
                                "questionId": "q1",
                                "stageId": stage_id,
                            },
                            {"event": "question_completed", "questionId": "q1"},
                        )
                    ),
                    encoding="utf-8",
                )
                store.update(
                    "sample",
                    child["runId"],
                    status=status,
                    receiptValidated=validated,
                )
            store.update(
                "sample",
                parent["runId"],
                status="failed",
                childRunIds=child_ids,
            )

            progress = store.combined_progress("sample", parent["runId"])

        self.assertEqual(progress["processedWorkItemCount"], 2)
        self.assertEqual(progress["validatedWorkItemCount"], 1)
        self.assertEqual(progress["touchedQuestionCount"], 1)
        self.assertEqual(progress["processedQuestionCount"], 0)
        self.assertEqual(progress["validatedQuestionCount"], 0)
        self.assertEqual(progress["completedQuestionCount"], 0)
        self.assertEqual(progress["questions"][0]["approvalState"], "failed_unapproved")
        self.assertFalse(progress["verified"])
        self.assertEqual(progress["invalidEventCount"], 0)

    def test_progress_summarizes_all_58_questions_beyond_recent_event_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            question_ids = [f"q{index}" for index in range(1, 59)]
            plan = {
                "qualification": "sample",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "stageCode": "03",
                "stageLabel": "解説",
                "mode": "outdated",
                "modeLabel": "洗い替え必要のみ",
                "kind": "human",
                "targetCount": 58,
                "workItemCount": 58,
                "targetGroupIds": ["2026"],
                "policyTargets": {"explanation": question_ids},
                "progressTargets": [
                    {
                        "id": question_id,
                        "questionKey": f"sample:2026:{question_id}",
                        "listGroupId": "2026",
                        "questionLabel": f"問{index}",
                        "bodyPreview": f"問題本文{index}",
                        "aliases": [],
                    }
                    for index, question_id in enumerate(question_ids, start=1)
                ],
                "stagePlans": [
                    {
                        "stageId": "explanation",
                        "stageCode": "03",
                        "stageLabel": "解説",
                    }
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            run = store.create(plan, status="running", prompt="整備する。")
            progress_path = root / run["progressReceiptPath"]
            lines = []
            for index, question_id in enumerate(question_ids, start=1):
                lines.extend(
                    [
                        {"event": "question_started", "questionId": question_id},
                        {
                            "event": "stage_completed",
                            "questionId": question_id,
                            "stageId": "explanation",
                            "result": {"explanationText": f"解説{index}"},
                        },
                        {"event": "question_completed", "questionId": question_id},
                    ]
                )
            progress_path.write_text(
                "\n".join(
                    json.dumps(line, ensure_ascii=False) for line in lines
                )
                + "\n",
                encoding="utf-8",
            )

            progress = store.progress("sample", run["runId"])

        self.assertEqual(len(progress["events"]), 40)
        self.assertEqual(len(progress["questions"]), 58)
        self.assertEqual(progress["questions"][0]["questionLabel"], "問1")
        self.assertEqual(progress["questions"][-1]["questionLabel"], "問58")
        self.assertEqual(
            progress["questions"][-1]["outputs"][0]["result"][
                "explanationText"
            ],
            "解説58",
        )

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


class QualificationQueueSafetyRegressionTests(QualificationRunTestSupport):

    @staticmethod
    def _mark_child_succeeded(coordinator, qualification, child_run_id):
        coordinator.store.update(
            qualification,
            child_run_id,
            status="succeeded",
            receiptValidated=True,
            workVersionReceipt={"recordedCount": 1},
            result={
                "status": "succeeded",
                "summary": "一問を確定した。",
                "commands": [{"command": "test", "status": "pass"}],
                "changedFiles": [],
            },
            error=None,
        )

    @staticmethod
    def _mark_question_prepared(
        coordinator,
        qualification,
        run_id,
        target,
        stage_id,
    ):
        question_id = str(target["id"])
        stage = coordinator._queue_stage(
            coordinator.store.get(qualification, run_id),
            question_id,
            stage_id,
        )
        proposal = coordinator.question_proposals.write(
            qualification,
            run_id,
            work_item_key=str(stage["workItemKey"]),
            question_id=question_id,
            stage_id=stage_id,
            input_fingerprint=str(stage["inputFingerprint"]),
            summary="一問の読取専用判断案",
            thread_id="thread-prepare-test",
            session_id="session-prepare-test",
            turn_id="turn-prepare-test",
        )
        coordinator.store.update_question_stage(
            qualification,
            run_id,
            question_id,
            stage_id,
            status="prepared",
            preparationPath=proposal["path"],
            preparationHash=proposal["hash"],
            error=None,
        )

    def _start_deferred_flow(
        self,
        root,
        inventory,
        stage_ids,
        app_server=None,
        group_ids=None,
    ):
        selected_groups = list(group_ids or ["2026"])
        synchronizer = FakeSynchronizer()
        app_server = app_server or FlowAppServer()
        coordinator = QualificationRunCoordinator(
            root,
            QualificationWorkflow(root, inventory),
            synchronizer,
            DeferredJobs(),
            "secret",
            app_server=app_server,
        )
        preview = coordinator.preview(
            "new-exam",
            stage_ids[0],
            "outdated",
            stage_ids=stage_ids,
            list_group_ids=selected_groups,
        )
        started = coordinator.start(
            "new-exam",
            preview["stageId"],
            "outdated",
            preview["previewToken"],
            stage_ids=preview["stageIds"],
            list_group_ids=preview["scopeListGroupIds"],
        )
        self.assertEqual(started["run"]["workType"], "maintenance_flow")
        return coordinator, synchronizer, app_server, started["run"]

    @staticmethod
    def _mark_parent_partial(coordinator, parent):
        question = parent["questionExecutions"][0]
        stage = question["stages"][0]
        coordinator.store.update_question_stage(
            "new-exam",
            parent["runId"],
            question["questionId"],
            stage["stageId"],
            status="blocked",
            error="再実行対象として保留した。",
            block_dependents=True,
        )
        return coordinator.store.update(
            "new-exam",
            parent["runId"],
            status="succeeded",
            queueStatus="partial",
        )

    @staticmethod
    def _attach_unsafe_child(coordinator, parent):
        child_plan = FakeWorkflow().plan("new-exam", "law_audit")
        child_plan.update(
            parentRunId=parent["runId"],
            stageIds=["question_type"],
            workType="maintenance_question_type",
        )
        child = coordinator.store.create(
            child_plan,
            status="failed",
            prompt="unsafe child",
        )
        child = coordinator.store.update(
            "new-exam",
            child["runId"],
            startedAt="started",
            deltaUnknown=True,
            rollback={
                "status": "failed",
                "deltaUnknown": True,
                "remainingChangedFiles": [],
            },
            result={
                "status": "failed",
                "summary": "rollback safety unknown",
                "commands": [],
                "changedFiles": [],
            },
            error="rollback safety unknown",
        )
        current = coordinator.store.get("new-exam", parent["runId"])
        coordinator.store.update(
            "new-exam",
            parent["runId"],
            childRunIds=[
                *list(current.get("childRunIds") or []),
                child["runId"],
            ],
        )
        return child

    def _resume_after_failed_phase_merge(
        self,
        root,
        *,
        app_server,
        merge_statuses,
    ):
        coordinator, synchronizer, _app_server, previous = (
            self._start_deferred_flow(
                root,
                MultiGroupSourceInventory(),
                ["question_type", "question_intent"],
                app_server=app_server,
                group_ids=["2025", "2026"],
            )
        )
        for question in previous["questionExecutions"]:
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question["questionId"],
                "question_type",
                status="validated",
                error=None,
            )
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question["questionId"],
                "question_intent",
                status="blocked",
                error="前工程後のmergeが未完了です。",
            )
        phase_executions = copy.deepcopy(previous["phaseExecutions"])
        phase_executions[0].update(
            status="partial",
            artifactSync={
                "status": "failed",
                "groups": [
                    {
                        "listGroupId": list_group_id,
                        "status": status,
                        "message": f"{list_group_id} merge {status}",
                    }
                    for list_group_id, status in merge_statuses.items()
                ],
            },
        )
        phase_executions[1].update(status="partial")
        previous = coordinator.store.update(
            "new-exam",
            previous["runId"],
            status="succeeded",
            queueStatus="partial",
            phaseExecutions=phase_executions,
        )
        preview = coordinator.preview(
            "new-exam",
            "question_type",
            "outdated",
            stage_ids=["question_type", "question_intent"],
            list_group_ids=["2025", "2026"],
            resumed_from=previous["runId"],
        )
        started = coordinator.start(
            "new-exam",
            preview["stageId"],
            "outdated",
            preview["previewToken"],
            stage_ids=preview["stageIds"],
            list_group_ids=preview["scopeListGroupIds"],
            resumed_from=previous["runId"],
        )
        return coordinator, synchronizer, started["run"]

    def test_unsafe_child_rollback_fails_closed_before_later_writer_or_sync(self):
        unsafe_children = {
            "rollback_failed": {
                "deltaUnknown": False,
                "rollback": {
                    "status": "failed",
                    "deltaUnknown": False,
                    "remainingChangedFiles": [],
                },
            },
            "delta_unknown": {
                "deltaUnknown": True,
                "rollback": {
                    "status": "succeeded",
                    "deltaUnknown": True,
                    "remainingChangedFiles": [],
                },
            },
            "remaining_delta": {
                "deltaUnknown": False,
                "rollback": {
                    "status": "succeeded",
                    "deltaUnknown": False,
                    "remainingChangedFiles": ["output/new-exam/unsafe.json"],
                },
            },
        }
        for case_name, unsafe_state in unsafe_children.items():
            with (
                self.subTest(case=case_name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                coordinator, synchronizer, _app_server, parent = (
                    self._start_deferred_flow(
                        root,
                        TwoQuestionSourceInventory(),
                        ["question_type"],
                    )
                )
                writer_calls = []

                def prepare_item(
                    qualification,
                    run_id,
                    _phase_prompt,
                    target,
                    stage_id,
                    _emit,
                ):
                    self._mark_question_prepared(
                        coordinator,
                        qualification,
                        run_id,
                        target,
                        stage_id,
                    )
                    return True

                def fail_unsafe_child(qualification, child_run_id, *_args, **_kwargs):
                    writer_calls.append(child_run_id)
                    coordinator.store.update(
                        qualification,
                        child_run_id,
                        status="failed",
                        receiptValidated=False,
                        result={
                            "status": "failed",
                            "summary": "writer failed",
                            "commands": [],
                            "changedFiles": [],
                        },
                        error="writer failed",
                        **unsafe_state,
                    )
                    raise RuntimeError("writer failed")

                coordinator._prepare_question_item = prepare_item
                coordinator._run_human = fail_unsafe_child

                with patch(
                    "tools.question_review_console.qualification_runs."
                    "sync_after_patch_update"
                ) as artifact_sync:
                    with self.assertRaisesRegex(
                        QualificationRunError,
                        "rollback完了を検証できない",
                    ):
                        coordinator._run_maintenance_flow(
                            "new-exam",
                            parent["runId"],
                            lambda _message: None,
                        )
                run = coordinator.store.get("new-exam", parent["runId"])

                self.assertEqual(run["status"], "failed")
                self.assertFalse(run["receiptValidated"])
                self.assertEqual(len(writer_calls), 1)
                self.assertEqual(len(run["childRunIds"]), 1)
                self.assertEqual(
                    [
                        stage["status"]
                        for question in run["questionExecutions"]
                        for stage in question["stages"]
                    ],
                    ["blocked", "blocked"],
                )
                artifact_sync.assert_not_called()
                self.assertIsNone(run.get("artifactSync"))
                self.assertEqual(synchronizer.merge_calls, [])
                self.assertEqual(synchronizer.calls, [])

    def test_bounded_pipeline_commits_ready_question_while_sibling_is_preparing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
            )
            first_id = "new-exam-2026-q1"
            second_id = "new-exam-2026-q2"
            slow_started = threading.Event()
            second_writer_started = threading.Event()
            slow_finished = threading.Event()
            lock = threading.Lock()
            active_preparations = 0
            max_preparations = 0
            active_writers = 0
            max_writers = 0
            writer_order = []
            writer_overlapped_slow_prepare = []

            def prepare_item(
                qualification,
                run_id,
                _phase_prompt,
                target,
                stage_id,
                _emit,
            ):
                nonlocal active_preparations, max_preparations
                question_id = str(target["id"])
                with lock:
                    active_preparations += 1
                    max_preparations = max(max_preparations, active_preparations)
                try:
                    if question_id == first_id:
                        slow_started.set()
                        second_writer_started.wait(timeout=2)
                        slow_finished.set()
                    else:
                        self.assertTrue(slow_started.wait(timeout=1))
                    self._mark_question_prepared(
                        coordinator,
                        qualification,
                        run_id,
                        target,
                        stage_id,
                    )
                    return True
                finally:
                    with lock:
                        active_preparations -= 1

            def commit_child(qualification, child_run_id, *_args, **_kwargs):
                nonlocal active_writers, max_writers
                child = coordinator.store.get(qualification, child_run_id)
                question_id = str(child["progressTargets"][0]["id"])
                with lock:
                    active_writers += 1
                    max_writers = max(max_writers, active_writers)
                try:
                    writer_order.append(question_id)
                    if question_id == second_id:
                        writer_overlapped_slow_prepare.append(
                            not slow_finished.is_set()
                        )
                        second_writer_started.set()
                    self._mark_child_succeeded(
                        coordinator,
                        qualification,
                        child_run_id,
                    )
                finally:
                    with lock:
                        active_writers -= 1

            coordinator._prepare_question_item = prepare_item
            coordinator._run_human = commit_child
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(writer_order, [second_id, first_id])
        self.assertEqual(writer_overlapped_slow_prepare, [True])
        self.assertLessEqual(max_preparations, 2)
        self.assertEqual(max_preparations, 2)
        self.assertEqual(max_writers, 1)

    def test_prepare_failure_blocks_only_that_question_and_commits_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
            )
            failed_id = "new-exam-2026-q1"
            succeeded_id = "new-exam-2026-q2"
            writer_ids = []

            def prepare_item(
                qualification,
                run_id,
                _phase_prompt,
                target,
                stage_id,
                _emit,
            ):
                question_id = str(target["id"])
                if question_id == failed_id:
                    coordinator.store.update_question_stage(
                        qualification,
                        run_id,
                        question_id,
                        stage_id,
                        status="blocked",
                        error="prepare failed",
                        finishedAt="now",
                        block_dependents=True,
                    )
                    return False
                self._mark_question_prepared(
                    coordinator,
                    qualification,
                    run_id,
                    target,
                    stage_id,
                )
                return True

            def commit_child(qualification, child_run_id, *_args, **_kwargs):
                child = coordinator.store.get(qualification, child_run_id)
                writer_ids.append(str(child["progressTargets"][0]["id"]))
                self._mark_child_succeeded(
                    coordinator,
                    qualification,
                    child_run_id,
                )

            coordinator._prepare_question_item = prepare_item
            coordinator._run_human = commit_child
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", parent["runId"])
            statuses = {
                question["questionId"]: question["stages"][0]["status"]
                for question in run["questionExecutions"]
            }

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(writer_ids, [succeeded_id])
        self.assertEqual(statuses[failed_id], "blocked")
        self.assertEqual(statuses[succeeded_id], "validated")

    def test_prepared_without_proposal_reference_never_reaches_writer(self):
        for missing_field in ("preparationPath", "preparationHash"):
            with (
                self.subTest(missing_field=missing_field),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                )
                missing_id = "new-exam-2026-q1"
                valid_id = "new-exam-2026-q2"
                writer_ids = []

                def prepare_item(
                    qualification,
                    run_id,
                    _phase_prompt,
                    target,
                    stage_id,
                    _emit,
                ):
                    self._mark_question_prepared(
                        coordinator,
                        qualification,
                        run_id,
                        target,
                        stage_id,
                    )
                    if str(target["id"]) == missing_id:
                        coordinator.store.update_question_stage(
                            qualification,
                            run_id,
                            missing_id,
                            stage_id,
                            status="prepared",
                            **{missing_field: None},
                        )
                    return True

                def commit_child(qualification, child_run_id, *_args, **_kwargs):
                    child = coordinator.store.get(qualification, child_run_id)
                    writer_ids.append(str(child["progressTargets"][0]["id"]))
                    self._mark_child_succeeded(
                        coordinator,
                        qualification,
                        child_run_id,
                    )

                coordinator._prepare_question_item = prepare_item
                coordinator._run_human = commit_child
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
                run = coordinator.store.get("new-exam", parent["runId"])
                stages = {
                    question["questionId"]: question["stages"][0]
                    for question in run["questionExecutions"]
                }

                self.assertEqual(result["queueStatus"], "partial")
                self.assertEqual(writer_ids, [valid_id])
                self.assertEqual(stages[missing_id]["status"], "blocked")
                self.assertIn("path/hash", stages[missing_id]["error"])
                self.assertEqual(stages[valid_id]["status"], "validated")

    def test_prepare_prompt_contains_deterministic_single_question_file_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            prepare_prompts = [
                prompt
                for prompt, kwargs in app_server.calls
                if kwargs["work_type"] == "maintenance_prepare_question_type"
            ]

        self.assertEqual(len(prepare_prompts), 2)
        for prompt in prepare_prompts:
            question_id = next(
                line.split("`")[1]
                for line in prompt.splitlines()
                if line.startswith("- 問題ID: `")
            )
            suffix = question_id.rsplit("q", 1)[1]
            scoped_section = prompt.split(
                "## 決定済みの一問file scope",
                1,
            )[1].split("# 親範囲の整備prompt", 1)[0]
            self.assertIn(
                f"00_source/question_2026_{suffix}.json",
                scoped_section,
            )
            self.assertIn("sourceRecordRefの`#`以降を0始まり", scoped_section)
            self.assertIn("repo全体を探索して別file又は別recordへfallbackしない", scoped_section)

    def test_dependency_blocked_item_counts_in_later_phase_and_skips_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    SourceOnlyInventory(),
                    ["question_type", "question_intent"],
                    app_server=app_server,
                )
            )
            question = parent["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                question["questionId"],
                "question_type",
                status="blocked",
                error="前工程で安全に停止した。",
                block_dependents=True,
            )

            with patch(
                "tools.question_review_console.qualification_runs."
                "sync_after_patch_update"
            ) as artifact_sync:
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            run = coordinator.store.get("new-exam", parent["runId"])
            later_phase = run["phaseExecutions"][1]

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(later_phase["status"], "partial")
        self.assertEqual(later_phase["blockedCount"], 1)
        self.assertEqual(later_phase["validatedCount"], 0)
        self.assertEqual(run["validatedWorkItemCount"], 0)
        self.assertEqual(run["artifactSync"]["status"], "not_required")
        artifact_sync.assert_not_called()
        self.assertEqual(app_server.calls, [])
        self.assertEqual(synchronizer.merge_calls, [])
        self.assertEqual(synchronizer.calls, [])

    def test_resume_remerges_failed_dependency_before_any_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []
            app_server = FlowAppServer(events=events)
            coordinator, synchronizer, resumed = (
                self._resume_after_failed_phase_merge(
                    root,
                    app_server=app_server,
                    merge_statuses={"2025": "failed", "2026": "blocked"},
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            def refresh_group(qualification, list_group_id, emit):
                synchronizer.merge_calls.append((qualification, list_group_id))
                events.append(f"resume-merge:{list_group_id}")
                emit(f"{list_group_id}: resume merge succeeded")
                return {
                    "listGroupId": list_group_id,
                    "status": "succeeded",
                    "message": "resume merge succeeded",
                }

            synchronizer.refresh_merged_views = refresh_group
            initial_dependencies = resumed["resumeMergeDependencies"]
            result = coordinator._run_maintenance_flow(
                "new-exam",
                resumed["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", resumed["runId"])

        first_session = next(
            index
            for index, event in enumerate(events)
            if event.startswith("session:")
        )
        self.assertEqual(
            {
                (value["listGroupId"], value["afterStageId"], value["status"])
                for value in initial_dependencies
            },
            {
                ("2025", "question_type", "pending"),
                ("2026", "question_type", "pending"),
            },
        )
        self.assertTrue(
            all(
                events.index(f"resume-merge:{list_group_id}") < first_session
                for list_group_id in ("2025", "2026")
            )
        )
        self.assertEqual(app_server.writer_count, 2)
        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(
            {value["status"] for value in run["resumeMergeDependencies"]},
            {"succeeded"},
        )

    def test_restart_after_validated_save_remerges_before_resumed_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type", "question_intent"],
                app_server=FlowAppServer(),
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            coordinator._run_human = (
                lambda qualification, child_run_id, *_args, **_kwargs: (
                    self._mark_child_succeeded(
                        coordinator,
                        qualification,
                        child_run_id,
                    )
                )
            )
            original_update_stage = coordinator.store.update_question_stage
            crashed = False

            def crash_after_validated(*args, **changes):
                nonlocal crashed
                updated = original_update_stage(*args, **changes)
                if (
                    not crashed
                    and str(args[1]) == str(parent["runId"])
                    and str(args[3]) == "question_type"
                    and changes.get("status") == "validated"
                ):
                    crashed = True
                    raise SystemExit("simulated process stop before phase merge")
                return updated

            coordinator.store.update_question_stage = crash_after_validated
            with self.assertRaisesRegex(SystemExit, "before phase merge"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            saved = coordinator.store.get("new-exam", parent["runId"])
            saved_stage = saved["questionExecutions"][0]["stages"][0]
            saved_receipt = saved["workVersionReceipt"]

            restarted_store = QualificationRunStore(root)
            previous = restarted_store.get("new-exam", parent["runId"])
            events = []
            app_server = FlowAppServer(events=events)
            synchronizer = FakeSynchronizer()

            def refresh_group(qualification, list_group_id, emit):
                synchronizer.merge_calls.append((qualification, list_group_id))
                events.append(f"remerge:{list_group_id}")
                emit(f"{list_group_id}: remerge succeeded")
                return {
                    "listGroupId": list_group_id,
                    "status": "succeeded",
                    "message": "remerge succeeded",
                }

            synchronizer.refresh_merged_views = refresh_group
            resumed_coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, SourceOnlyInventory()),
                synchronizer,
                DeferredJobs(),
                "secret",
                store=restarted_store,
                app_server=app_server,
            )
            resumed_coordinator._repository_file_fingerprints = lambda *_args: {}
            preview = resumed_coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type", "question_intent"],
                list_group_ids=["2026"],
                resumed_from=previous["runId"],
            )
            resumed = resumed_coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
                resumed_from=previous["runId"],
            )["run"]
            initial_dependencies = resumed["resumeMergeDependencies"]
            inherited_receipt = resumed["workVersionReceipt"]
            resumed_coordinator._run_maintenance_flow(
                "new-exam",
                resumed["runId"],
                lambda _message: None,
            )
            completed = resumed_coordinator.store.get(
                "new-exam", resumed["runId"]
            )

        self.assertTrue(crashed)
        self.assertEqual(saved_stage["status"], "validated")
        self.assertEqual(saved["confirmedGroupIds"], ["2026"])
        self.assertEqual(saved_receipt["recordedCount"], 1)
        self.assertEqual(len(saved_receipt["items"]), 1)
        self.assertEqual(inherited_receipt, saved_receipt)
        self.assertEqual(
            [
                (
                    value["listGroupId"],
                    value["afterStageId"],
                    value["status"],
                )
                for value in initial_dependencies
            ],
            [("2026", "question_type", "pending")],
        )
        writer_event = "session:maintenance_question_intent"
        self.assertLess(events.index("remerge:2026"), events.index(writer_event))
        self.assertEqual(app_server.writer_count, 1)
        self.assertGreaterEqual(completed["workVersionReceipt"]["recordedCount"], 1)

    def test_resume_keeps_merge_pending_while_preceding_stage_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, previous = (
                self._start_deferred_flow(
                    root,
                    SourceOnlyInventory(),
                    ["question_type", "question_intent"],
                )
            )
            question = previous["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question["questionId"],
                "question_type",
                status="blocked",
                error="前工程自体を再実行する。",
                block_dependents=True,
            )
            phase_executions = copy.deepcopy(previous["phaseExecutions"])
            phase_executions[0]["artifactSync"] = {
                "status": "failed",
                "groups": [
                    {"listGroupId": "2026", "status": "failed"},
                ],
            }
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="succeeded",
                queueStatus="partial",
                phaseExecutions=phase_executions,
            )
            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type", "question_intent"],
                list_group_ids=["2026"],
                resumed_from=previous["runId"],
            )
            resumed = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
                resumed_from=previous["runId"],
            )["run"]

        self.assertEqual(
            [phase["id"] for phase in resumed["phaseExecutions"]],
            ["question_type", "question_intent"],
        )
        self.assertEqual(
            [
                (
                    value["listGroupId"],
                    value["afterStageId"],
                    value["status"],
                )
                for value in resumed["resumeMergeDependencies"]
            ],
            [("2026", "question_type", "pending")],
        )

    def test_failed_partial_retry_remerges_before_validated_sibling_downstream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, previous = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type", "question_intent"],
                    app_server=FlowAppServer(),
                )
            )
            validated_id = "new-exam-2026-q1"
            retry_id = "new-exam-2026-q2"
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                validated_id,
                "question_type",
                status="validated",
                error=None,
            )
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                validated_id,
                "question_intent",
                status="blocked",
                error="前工程後のmerge待ちです。",
            )
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                retry_id,
                "question_type",
                status="blocked",
                error="この問題の前工程を再試行します。",
                block_dependents=True,
            )
            phase_executions = copy.deepcopy(previous["phaseExecutions"])
            phase_executions[0].update(
                status="partial",
                artifactSync={
                    "status": "failed",
                    "groups": [
                        {"listGroupId": "2026", "status": "failed"},
                    ],
                },
            )
            phase_executions[1]["status"] = "partial"
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="succeeded",
                queueStatus="partial",
                phaseExecutions=phase_executions,
            )
            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type", "question_intent"],
                list_group_ids=["2026"],
                resumed_from=previous["runId"],
            )
            resumed = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
                resumed_from=previous["runId"],
            )["run"]
            events = []

            def run_child(qualification, child_run_id, *_args, **_kwargs):
                child = coordinator.store.get(qualification, child_run_id)
                question_id = str(child["progressTargets"][0]["id"])
                phase_id = str(child["flowPhaseId"])
                events.append(f"writer:{phase_id}:{question_id}")
                if question_id == retry_id and phase_id == "question_type":
                    coordinator.store.update(
                        qualification,
                        child_run_id,
                        status="failed",
                        receiptValidated=False,
                        deltaUnknown=False,
                        rollback={
                            "status": "succeeded",
                            "deltaUnknown": False,
                            "remainingChangedFiles": [],
                        },
                        result={
                            "status": "failed",
                            "summary": "再試行に失敗した。",
                            "commands": [],
                            "changedFiles": [],
                        },
                        error="再試行に失敗した。",
                    )
                    raise RuntimeError("再試行に失敗した。")
                self._mark_child_succeeded(
                    coordinator,
                    qualification,
                    child_run_id,
                )

            def refresh_group(qualification, list_group_id, emit):
                synchronizer.merge_calls.append((qualification, list_group_id))
                events.append(f"remerge:{list_group_id}")
                emit(f"{list_group_id}: remerge succeeded")
                return {
                    "listGroupId": list_group_id,
                    "status": "succeeded",
                    "message": "remerge succeeded",
                }

            coordinator._run_human = run_child
            synchronizer.refresh_merged_views = refresh_group
            result = coordinator._run_maintenance_flow(
                "new-exam",
                resumed["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", resumed["runId"])

        downstream_writer = f"writer:question_intent:{validated_id}"
        self.assertEqual(result["queueStatus"], "partial")
        self.assertLess(events.index("remerge:2026"), events.index(downstream_writer))
        self.assertEqual(
            synchronizer.merge_calls,
            [("new-exam", "2026")],
        )
        self.assertEqual(
            completed["resumeMergeDependencies"][0]["status"],
            "succeeded",
        )

    def test_resume_merge_exception_blocks_only_its_group_and_excludes_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, synchronizer, resumed = (
                self._resume_after_failed_phase_merge(
                    root,
                    app_server=app_server,
                    merge_statuses={"2025": "failed", "2026": "failed"},
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            synchronizer.local_ready = False

            def refresh_group(qualification, list_group_id, emit):
                synchronizer.merge_calls.append((qualification, list_group_id))
                if list_group_id == "2025":
                    raise RuntimeError("2025 merge crashed")
                emit(f"{list_group_id}: resume merge succeeded")
                return {
                    "listGroupId": list_group_id,
                    "status": "current",
                    "message": "resume merge current",
                }

            synchronizer.refresh_merged_views = refresh_group
            result = coordinator._run_maintenance_flow(
                "new-exam",
                resumed["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", resumed["runId"])
            questions = {
                question["listGroupId"]: question
                for question in run["questionExecutions"]
            }
            artifact_groups = {
                group["listGroupId"]: group["status"]
                for group in run["artifactSync"]["groups"]
            }
            writer_prompts = [
                prompt
                for prompt, kwargs in app_server.calls
                if not kwargs["work_type"].startswith("maintenance_prepare_")
            ]

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(app_server.writer_count, 1)
        self.assertFalse(
            any("new-exam-2025-q1" in prompt for prompt in writer_prompts)
        )
        self.assertTrue(
            any("new-exam-2026-q1" in prompt for prompt in writer_prompts)
        )
        self.assertEqual(
            [stage["status"] for stage in questions["2025"]["stages"]],
            ["blocked"],
        )
        self.assertEqual(
            [stage["status"] for stage in questions["2026"]["stages"]],
            ["validated"],
        )
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])
        self.assertEqual(artifact_groups["2025"], "blocked")
        self.assertEqual(artifact_groups["2026"], "succeeded")
        self.assertEqual(
            {
                value["listGroupId"]: value["status"]
                for value in run["resumeMergeDependencies"]
            },
            {"2025": "failed", "2026": "current"},
        )

    def test_queue_block_helpers_preserve_not_applicable_stage(self):
        cases = (
            ("_block_remaining_queue", "question_type", ["not_applicable", "blocked"]),
            ("_block_group_queue", "question_type", ["not_applicable", "blocked"]),
            ("_block_remaining_queue", "question_intent", ["blocked", "not_applicable"]),
            ("_block_group_queue", "question_intent", ["blocked", "not_applicable"]),
        )
        for helper_name, terminal_stage_id, expected_statuses in cases:
            with (
                self.subTest(
                    helper=helper_name,
                    terminal_stage_id=terminal_stage_id,
                ),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                coordinator, _sync, _app_server, parent = (
                    self._start_deferred_flow(
                        root,
                        SourceOnlyInventory(),
                        ["question_type", "question_intent"],
                    )
                )
                question = parent["questionExecutions"][0]
                coordinator.store.update_question_stage(
                    "new-exam",
                    parent["runId"],
                    question["questionId"],
                    terminal_stage_id,
                    status="not_applicable",
                    error=None,
                )
                helper = getattr(coordinator, helper_name)
                if helper_name == "_block_group_queue":
                    helper("new-exam", parent["runId"], "2026", "停止理由")
                else:
                    helper("new-exam", parent["runId"], "停止理由")
                updated = coordinator.store.get("new-exam", parent["runId"])

            self.assertEqual(
                [
                    stage["status"]
                    for stage in updated["questionExecutions"][0]["stages"]
                ],
                expected_statuses,
            )

    def test_phase_start_refreshes_queued_input_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                )
            )
            phase = parent["phaseExecutions"][0]
            phase_plan, _phase_prompt = coordinator._flow_phase_plan_prompt(
                parent,
                phase,
            )
            target = dict(phase_plan["progressTargets"][0])
            question_id = str(target["id"])
            stage_id = str(phase_plan["stageId"])
            before = coordinator._queue_stage(
                coordinator.store.get("new-exam", parent["runId"]),
                question_id,
                stage_id,
            )
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                question_id,
                stage_id,
                status="queued",
                preparationPath="stale-proposal.json",
                preparationHash="stale-hash",
            )
            target["stateHash"] = "current-state-hash"
            phase_plan["policyFingerprints"] = {
                **dict(phase_plan.get("policyFingerprints") or {}),
                stage_id: "current-policy-fingerprint",
            }

            coordinator._refresh_queued_stage_inputs(
                "new-exam",
                parent["runId"],
                phase_plan,
                [target],
                stage_id,
            )
            after = coordinator._queue_stage(
                coordinator.store.get("new-exam", parent["runId"]),
                question_id,
                stage_id,
            )
            expected = input_fingerprint(
                target,
                stage_id,
                "current-policy-fingerprint",
            )

        self.assertNotEqual(before["inputFingerprint"], expected)
        self.assertEqual(after["status"], "queued")
        self.assertEqual(after["inputFingerprint"], expected)
        self.assertIsNone(after["preparationPath"])
        self.assertIsNone(after["preparationHash"])

    def test_partial_resume_rejects_retry_unsafe_parent_or_child(self):
        for unsafe_source in ("parent", "child"):
            for operation in ("preview", "start"):
                with (
                    self.subTest(source=unsafe_source, operation=operation),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    coordinator, _sync, _app_server, parent = (
                        self._start_deferred_flow(
                            root,
                            TwoQuestionSourceInventory(),
                            ["question_type"],
                        )
                    )
                    parent = self._mark_parent_partial(coordinator, parent)
                    if unsafe_source == "parent":
                        coordinator.store.update(
                            "new-exam",
                            parent["runId"],
                            retrySafe=False,
                            retryUnsafeReason="親runの再開安全性を確認できません。",
                        )
                    else:
                        child = self._attach_unsafe_child(coordinator, parent)

                    arguments = {
                        "stage_ids": ["question_type"],
                        "list_group_ids": ["2026"],
                        "resumed_from": parent["runId"],
                    }
                    with self.assertRaisesRegex(
                        QualificationRunError,
                        "再開",
                    ):
                        if operation == "preview":
                            coordinator.preview(
                                "new-exam",
                                "question_type",
                                "outdated",
                                **arguments,
                            )
                        else:
                            coordinator.start(
                                "new-exam",
                                "question_type",
                                "outdated",
                                "stale-preview-token",
                                **arguments,
                            )
                    rejected = coordinator.store.get(
                        "new-exam", parent["runId"]
                    )

                self.assertEqual(rejected["queueStatus"], "partial")
                self.assertFalse(rejected["retrySafe"])
                if unsafe_source == "child":
                    self.assertEqual(
                        rejected["unsafeChildRunId"],
                        child["runId"],
                    )

    def test_store_restart_propagates_unsafe_child_to_parent_retry_safety(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
            )
            parent = self._mark_parent_partial(coordinator, parent)
            child = self._attach_unsafe_child(coordinator, parent)

            restarted_store = QualificationRunStore(root)
            recovered = restarted_store.get("new-exam", parent["runId"])

        self.assertEqual(recovered["queueStatus"], "partial")
        self.assertFalse(recovered["retrySafe"])
        self.assertEqual(recovered["unsafeChildRunId"], child["runId"])
        self.assertIn("再開できません", recovered["retryUnsafeReason"])

    def test_store_restart_keeps_unstarted_bound_child_retry_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                )
            )
            phase_plan, _phase_prompt = coordinator._flow_phase_plan_prompt(
                parent,
                parent["phaseExecutions"][0],
            )
            target = phase_plan["progressTargets"][0]
            question_id = str(target["id"])
            child_plan = specialize_question_plan(phase_plan, question_id)
            child_plan.update(
                parentRunId=parent["runId"],
                flowPhaseId="question_type",
                phaseIndex=1,
                workType="maintenance_question_type",
            )
            child = coordinator.store.create(
                child_plan,
                status="queued",
                prompt="writerはまだ開始していない。",
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                childRunIds=[child["runId"]],
            )
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                status="committing",
                childRunIds=[child["runId"]],
                error=None,
            )

            restarted_store = QualificationRunStore(root)
            recovered = restarted_store.get("new-exam", parent["runId"])
            statuses = {
                question["questionId"]: question["stages"][0]["status"]
                for question in recovered["questionExecutions"]
            }
            resumed_coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, TwoQuestionSourceInventory()),
                synchronizer,
                DeferredJobs(),
                "secret",
                store=restarted_store,
                app_server=FlowAppServer(),
            )
            preview = resumed_coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                resumed_from=recovered["runId"],
            )

        self.assertTrue(recovered["retrySafe"])
        self.assertIsNone(recovered["unsafeChildRunId"])
        self.assertEqual(statuses[question_id], "blocked")
        self.assertEqual(
            statuses["new-exam-2026-q2"],
            "queued",
        )
        self.assertTrue(preview["canStart"])
        self.assertEqual(preview["targetCount"], 2)

    def test_read_only_preparation_retries_only_transient_app_server_error(self):
        class PreparationAppServer:
            configured = True
            provider = "Codex App Server"

            def __init__(self, outcomes):
                self.outcomes = list(outcomes)
                self.calls = []

            def assert_subscription_access(self, *, force=True):
                return {"allowed": True, "planType": "pro"}

            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                outcome = self.outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        def result(*, summary="read-only proposal", changed_files=()):
            return AppServerTurnResult(
                thread_id="thread-prepare",
                session_id="session-prepare",
                turn_id="turn-prepare",
                final_message=summary,
                model="gpt-test",
                service_tier=None,
                changed_files=tuple(changed_files),
            )

        cases = {
            "transient_app_server_error": {
                "outcomes": [
                    CodexAppServerError("stdio送信に失敗しました。"),
                    result(),
                ],
                "prepared": True,
                "calls": 2,
                "attempts": 2,
            },
            "changed_files": {
                "outcomes": [result(changed_files=("unexpected.json",))],
                "prepared": False,
                "calls": 1,
                "attempts": 1,
            },
            "proposal_integrity": {
                "outcomes": [result(summary="")],
                "prepared": False,
                "calls": 1,
                "attempts": 1,
            },
            "ordinary_runtime_error": {
                "outcomes": [RuntimeError("ordinary preparation failure")],
                "prepared": False,
                "calls": 1,
                "attempts": 1,
            },
        }
        for case_name, expected in cases.items():
            with (
                self.subTest(case=case_name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                app_server = PreparationAppServer(expected["outcomes"])
                coordinator, _sync, _app_server, parent = (
                    self._start_deferred_flow(
                        root,
                        TwoQuestionSourceInventory(),
                        ["question_type"],
                        app_server=app_server,
                    )
                )
                phase_plan, phase_prompt = coordinator._flow_phase_plan_prompt(
                    parent,
                    parent["phaseExecutions"][0],
                )
                target = phase_plan["progressTargets"][0]
                emitted = []

                prepared = coordinator._prepare_question_item(
                    "new-exam",
                    parent["runId"],
                    phase_prompt,
                    target,
                    "question_type",
                    emitted.append,
                )
                stage = coordinator._queue_stage(
                    coordinator.store.get("new-exam", parent["runId"]),
                    str(target["id"]),
                    "question_type",
                )

                self.assertEqual(prepared, expected["prepared"])
                self.assertEqual(len(app_server.calls), expected["calls"])
                self.assertEqual(stage["attempts"], expected["attempts"])
                self.assertEqual(
                    stage["status"],
                    "prepared" if expected["prepared"] else "blocked",
                )
                retry_messages = [
                    message for message in emitted if "再試行" in message
                ]
                self.assertEqual(
                    len(retry_messages),
                    1 if case_name == "transient_app_server_error" else 0,
                )

    def test_replanned_out_of_scope_items_become_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer()
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                    app_server=app_server,
                )
            )
            original_phase_plan = coordinator._flow_phase_plan_prompt

            def empty_replanned_phase(parent_run, phase):
                phase_plan, phase_prompt = original_phase_plan(parent_run, phase)
                phase_plan = copy.deepcopy(phase_plan)
                phase_plan.update(
                    targetCount=0,
                    workItemCount=0,
                    progressTargets=[],
                    policyTargets={"question_type": []},
                )
                return phase_plan, phase_prompt

            coordinator._flow_phase_plan_prompt = empty_replanned_phase
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", parent["runId"])
            stages = [
                stage
                for question in run["questionExecutions"]
                for stage in question["stages"]
            ]

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual([stage["status"] for stage in stages], [
            "not_applicable",
            "not_applicable",
        ])
        self.assertEqual(
            run["questionExecutionSummary"]["pendingWorkItemCount"],
            0,
        )
        self.assertEqual(run["phaseExecutions"][0]["status"], "skipped")
        self.assertEqual(run["phaseExecutions"][0]["notApplicableCount"], 2)
        self.assertEqual(run["artifactSync"]["status"], "not_required")
        self.assertEqual(app_server.calls, [])
        self.assertEqual(synchronizer.merge_calls, [])
        self.assertEqual(synchronizer.calls, [])

    def test_multi_group_merge_and_sync_isolate_blocked_group(self):
        scenarios = ("preblocked", "merge_failed")
        for scenario in scenarios:
            with (
                self.subTest(scenario=scenario),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                app_server = FlowAppServer()
                coordinator, synchronizer, _app_server, parent = (
                    self._start_deferred_flow(
                        root,
                        MultiGroupSourceInventory(),
                        ["question_type", "question_intent"],
                        app_server=app_server,
                        group_ids=["2025", "2026"],
                    )
                )
                coordinator._repository_file_fingerprints = lambda *_args: {}
                synchronizer.local_ready = False
                if scenario == "preblocked":
                    blocked_question = next(
                        question
                        for question in parent["questionExecutions"]
                        if question["listGroupId"] == "2025"
                    )
                    coordinator.store.update_question_stage(
                        "new-exam",
                        parent["runId"],
                        blocked_question["questionId"],
                        "question_type",
                        status="blocked",
                        error="2025は事前条件で保留した。",
                        block_dependents=True,
                    )
                else:
                    def refresh_group(qualification, list_group_id, emit):
                        synchronizer.merge_calls.append(
                            (qualification, list_group_id)
                        )
                        status = (
                            "failed" if list_group_id == "2025" else "succeeded"
                        )
                        emit(f"{list_group_id}: merge {status}")
                        return {
                            "listGroupId": list_group_id,
                            "status": status,
                            "message": f"merge {status}",
                        }

                    synchronizer.refresh_merged_views = refresh_group

                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
                run = coordinator.store.get("new-exam", parent["runId"])
                questions = {
                    question["listGroupId"]: question
                    for question in run["questionExecutions"]
                }
                artifact_groups = {
                    group["listGroupId"]: group["status"]
                    for group in run["artifactSync"]["groups"]
                }

                self.assertEqual(result["queueStatus"], "partial")
                self.assertEqual(
                    synchronizer.calls,
                    [("new-exam", "2026", True)],
                )
                if scenario == "preblocked":
                    self.assertEqual(
                        synchronizer.merge_calls,
                        [("new-exam", "2026")],
                    )
                    self.assertEqual(artifact_groups, {"2026": "succeeded"})
                    self.assertEqual(
                        [
                            stage["status"]
                            for stage in questions["2025"]["stages"]
                        ],
                        ["blocked", "blocked"],
                    )
                else:
                    self.assertEqual(
                        set(synchronizer.merge_calls),
                        {("new-exam", "2025"), ("new-exam", "2026")},
                    )
                    self.assertEqual(
                        [
                            stage["status"]
                            for stage in questions["2025"]["stages"]
                        ],
                        ["validated", "blocked"],
                    )
                    self.assertEqual(
                        [
                            stage["status"]
                            for stage in questions["2026"]["stages"]
                        ],
                        ["validated", "validated"],
                    )
                    self.assertEqual(artifact_groups["2025"], "blocked")
                    self.assertEqual(artifact_groups["2026"], "succeeded")

    def test_coordinator_technical_log_proxies_to_store(self):
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
                prompt="work",
            )
            coordinator.store.append_technical_log(
                "sample",
                run["runId"],
                {"message": "proxy event"},
            )
            with patch.object(
                coordinator.store,
                "technical_log",
                wraps=coordinator.store.technical_log,
            ) as technical_log:
                result = coordinator.technical_log("sample", run["runId"])

        technical_log.assert_called_once_with("sample", run["runId"])
        self.assertEqual(result["runId"], run["runId"])
        self.assertEqual(result["entries"][0]["message"], "proxy event")


if __name__ == "__main__":
    unittest.main()  # noqa: F405
