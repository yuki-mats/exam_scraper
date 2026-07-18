from types import SimpleNamespace

from tests.qualification_run_test_support import *  # noqa: F403
from tools.question_review_console.codex_app_server import (
    CodexAppServerError,
    SubscriptionGateError,
)
from tools.question_review_console.question_work_queue import (
    input_fingerprint,
    specialize_question_plan,
)
from tools.question_review_console.qualification_runs import (
    QuestionItemError,
    QuestionQueuePaused,
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
                    "correct_choice": ["ui-q1"],
                    "explanation": ["ui-q1", "ui-q2"],
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

    def test_progress_rejects_alias_policy_contract(self):
        manifest = {
            "runId": "stored-run",
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

    def test_progress_rejects_unique_alias_policy_target(self):
        manifest = {
            "runId": "stored-run",
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
                    "questionKey": "source-key",
                    "aliases": ["source-key"],
                    "listGroupId": "2026",
                }
            ],
            "policyTargets": {"explanation": ["source-key"]},
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

        self.assertEqual(progress["invalidEventCount"], 2)
        self.assertEqual(progress["processedQuestionCount"], 1)
        self.assertEqual(progress["validatedQuestionCount"], 0)

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

    def test_combined_progress_uses_parent_queue_position(self):
        questions = [
            {"questionId": "q2", "targetIndex": 1},
            {"questionId": "q1", "targetIndex": 1},
        ]
        executions = [
            {"questionId": "q1", "displayOrder": 1},
            {"questionId": "q2", "displayOrder": 1},
        ]

        QualificationRunStore._order_parent_questions(questions, executions)

        self.assertEqual(
            [(question["questionId"], question["targetIndex"]) for question in questions],
            [("q1", 1), ("q2", 2)],
        )

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

    def test_external_concurrent_change_is_not_attributed_to_writer(self):
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
            external = [
                ".git/HEAD",
                "docs/goals/question-maintenance/state.yaml",
            ]
            coordinator.store.update(
                "sample",
                run["runId"],
                result={
                    "status": "succeeded",
                    "commands": [],
                    "changedFiles": external,
                },
            )
            current = coordinator.store.get("sample", run["runId"])

            attribution = coordinator._validate_changed_files(
                "sample",
                run["runId"],
                current,
                (),
                tuple(external),
            )

        self.assertEqual(attribution["changedFiles"], [])
        self.assertEqual(
            attribution["externalConcurrentChangedFiles"],
            external,
        )
        self.assertEqual(
            attribution["ignoredReceiptChangedFiles"],
            external,
        )

    def test_app_server_scope_violation_is_not_treated_as_external_change(self):
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
            outside = "docs/unsafe.md"
            coordinator.store.update(
                "sample",
                run["runId"],
                result={
                    "status": "succeeded",
                    "commands": [],
                    "changedFiles": [outside],
                },
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "App Serverが整備責務外",
            ):
                coordinator._validate_changed_files(
                    "sample",
                    run["runId"],
                    coordinator.store.get("sample", run["runId"]),
                    (outside,),
                    (outside,),
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
    def _mark_child_failed_safely(
        coordinator,
        qualification,
        child_run_id,
        *,
        summary="patch contract failed",
    ):
        coordinator.store.update(
            qualification,
            child_run_id,
            status="failed",
            receiptValidated=False,
            receiptError="server patch validation rejected the receipt",
            deltaUnknown=False,
            rollback={
                "status": "succeeded",
                "deltaUnknown": False,
                "remainingChangedFiles": [],
            },
            result={
                "status": "failed",
                "summary": summary,
                "commands": [
                    {"command": "python check_patch.py", "status": "fail"}
                ],
                "changedFiles": [],
            },
            error=summary,
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
        projection = coordinator._write_projected_question_input(
            qualification,
            run_id,
            target,
            str(stage["workItemKey"]),
        )
        coordinator.store.update_question_stage(
            qualification,
            run_id,
            question_id,
            stage_id,
            status="prepared",
            preparationPath=proposal["path"],
            preparationHash=proposal["hash"],
            projectedInputPath=projection["path"],
            projectedInputHash=projection["hash"],
            error=None,
        )

    def _prepare_all_questions(self, coordinator):
        def prepare(
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

        return prepare

    def _start_deferred_flow(
        self,
        root,
        inventory,
        stage_ids,
        app_server=None,
        group_ids=None,
        question_concurrency=5,
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
            question_concurrency=question_concurrency,
        )
        started = coordinator.start(
            "new-exam",
            preview["stageId"],
            "outdated",
            preview["previewToken"],
            stage_ids=preview["stageIds"],
            list_group_ids=preview["scopeListGroupIds"],
            question_concurrency=preview["questionConcurrency"],
        )
        self.assertEqual(started["run"]["workType"], "maintenance_flow")
        return coordinator, synchronizer, app_server, started["run"]

    @staticmethod
    def _write_valid_category(root):
        category_path = (
            root / "output" / "new-exam" / "category" / "category.json"
        )
        category_path.parent.mkdir(parents=True, exist_ok=True)
        category_path.write_text(
            json.dumps(
                {
                    "folders": [{"folderId": "f1"}],
                    "questionSets": [{"questionSetId": "s1", "folderId": "f1"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_invalid_queue_contract_fails_before_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, synchronizer, app_server, parent = (
                self._start_deferred_flow(
                    Path(directory),
                    SourceOnlyInventory(),
                    ["question_type"],
                )
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                queueOrder=None,
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "一問queue契約が不正",
            ):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            run = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["queueStatus"], "failed")
        self.assertIn("一問queue契約が不正", run["error"])
        self.assertEqual(app_server.calls, [])
        self.assertEqual(synchronizer.calls, [])

    def test_resume_accepts_succeeded_partial_state(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                Path(directory),
                SourceOnlyInventory(),
                ["question_type"],
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                status="succeeded",
                queueStatus="partial",
            )

            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
                resumed_from=parent["runId"],
            )

        self.assertEqual(preview["targetCount"], 1)
        self.assertEqual(preview["workItemCount"], 1)

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
            status="failed",
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
            "scope_violation_notification": {
                "deltaUnknown": False,
                "rollback": {
                    "status": "succeeded",
                    "deltaUnknown": False,
                    "remainingChangedFiles": [],
                },
                "writeAttributionVerified": True,
                "unsafeNotifiedChangedFiles": ["docs/unsafe.md"],
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
                self.assertEqual(synchronizer.calls, [])

    def test_external_concurrent_change_blocks_only_its_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed_question_id = "new-exam-2026-q1"
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                )
            )
            coordinator._prepare_question_item = self._prepare_all_questions(
                coordinator
            )
            writer_question_ids = []

            def run_child(qualification, child_run_id, *_args, **_kwargs):
                child = coordinator.store.get(qualification, child_run_id)
                target = (child.get("progressTargets") or [{}])[0]
                question_id = str(target.get("id") or "")
                writer_question_ids.append(question_id)
                if question_id != failed_question_id:
                    self._mark_child_succeeded(
                        coordinator,
                        qualification,
                        child_run_id,
                    )
                    return
                summary = "同時刻のGoalBuddy更新をreceiptへ含めた。"
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
                    writeAttributionVerified=True,
                    unsafeNotifiedChangedFiles=[],
                    externalConcurrentChangedFiles=[
                        ".git/HEAD",
                        "docs/goals/question-maintenance/state.yaml",
                    ],
                    result={
                        "status": "failed",
                        "summary": summary,
                        "commands": [],
                        "changedFiles": [
                            ".git/HEAD",
                            "docs/goals/question-maintenance/state.yaml",
                        ],
                    },
                    error=summary,
                )
                raise RuntimeError(summary)

            coordinator._run_human = run_child
            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", parent["runId"])

        questions = {
            question["questionId"]: question
            for question in run["questionExecutions"]
        }
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["queueStatus"], "partial")
        self.assertEqual(run["blockedQuestionCount"], 1)
        self.assertEqual(run["validatedQuestionCount"], 1)
        self.assertEqual(
            questions[failed_question_id]["stages"][0]["status"],
            "blocked",
        )
        self.assertEqual(
            questions["new-exam-2026-q2"]["stages"][0]["status"],
            "validated",
        )
        self.assertEqual(
            writer_question_ids,
            [failed_question_id] * 3 + ["new-exam-2026-q2"],
        )
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

    def test_recent_reclassifies_legacy_external_only_child_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
            )
            parent = self._mark_parent_partial(coordinator, parent)
            child_plan = FakeWorkflow().plan(
                "new-exam",
                "question_type",
                "outdated",
            )
            child_plan.update(
                parentRunId=parent["runId"],
                flowPhaseId="question_type",
                sandbox="workspace-write",
            )
            child = coordinator.store.create(
                child_plan,
                status="failed",
                prompt="legacy child",
            )
            external = [
                ".git/HEAD",
                "docs/goals/question-maintenance/state.yaml",
            ]
            coordinator.store.update(
                "new-exam",
                child["runId"],
                startedAt="started",
                deltaUnknown=False,
                rollback={
                    "status": "succeeded",
                    "deltaUnknown": False,
                    "remainingChangedFiles": [],
                },
                result={
                    "status": "failed",
                    "summary": "scope外の並行変更を誤検出した。",
                    "commands": [],
                    "changedFiles": external,
                },
                error="scope外の並行変更を誤検出した。",
            )
            coordinator.store.append_technical_log(
                "new-exam",
                child["runId"],
                {
                    "changedPaths": [
                        child["progressReceiptPath"],
                        child["resultReceiptPath"],
                    ]
                },
            )
            coordinator.store.update(
                "new-exam",
                parent["runId"],
                retrySafe=False,
                retryUnsafeReason="rollbackを確認できない。",
                unsafeChildRunId=child["runId"],
                childRunIds=[child["runId"]],
            )

            recent = coordinator.recent("new-exam")
            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
                resumed_from=parent["runId"],
            )
            recovered_parent = coordinator.store.get(
                "new-exam",
                parent["runId"],
            )
            recovered_child = coordinator.store.get(
                "new-exam",
                child["runId"],
            )

        self.assertTrue(recent["runs"][0]["retrySafe"])
        self.assertEqual(preview["targetCount"], 1)
        self.assertTrue(recovered_parent["retrySafe"])
        self.assertIsNone(recovered_parent["unsafeChildRunId"])
        self.assertTrue(recovered_child["writeAttributionVerified"])
        self.assertEqual(recovered_child["result"]["changedFiles"], [])
        self.assertEqual(
            recovered_child["externalConcurrentChangedFiles"],
            external,
        )

    def test_unsafe_category_setup_stops_dependent_queue_and_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    LawSourceInventory(),
                    ["category_setup", "question_set"],
                )
            )
            writer_calls = []

            def fail_unsafe_scope(
                qualification,
                child_run_id,
                *_args,
                **_kwargs,
            ):
                writer_calls.append(child_run_id)
                coordinator.store.update(
                    qualification,
                    child_run_id,
                    status="failed",
                    receiptValidated=False,
                    deltaUnknown=True,
                    rollback={
                        "status": "failed",
                        "deltaUnknown": True,
                        "remainingChangedFiles": [],
                    },
                    result={
                        "status": "failed",
                        "summary": "category setup failed",
                        "commands": [],
                        "changedFiles": [],
                    },
                    error="category setup failed",
                )
                raise RuntimeError("category setup failed")

            coordinator._run_human = fail_unsafe_scope
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
            self.assertFalse(run["retrySafe"])
            self.assertTrue((root / run["improvementReportPath"]).is_file())
            self.assertEqual(run["unsafeChildRunId"], writer_calls[0])
            self.assertEqual(len(writer_calls), 1)
            self.assertTrue(
                all(
                    stage["status"] == "blocked"
                    for question in run["questionExecutions"]
                    for stage in question["stages"]
                )
            )
            artifact_sync.assert_not_called()
            self.assertEqual(synchronizer.calls, [])

    def test_category_setup_provider_gate_pauses_parent_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    LawSourceInventory(),
                    ["category_setup", "question_set"],
                )
            )
            writer_ids = []

            def gated_scope(qualification, child_run_id, *_args, **_kwargs):
                writer_ids.append(child_run_id)
                self._mark_child_failed_safely(
                    coordinator,
                    qualification,
                    child_run_id,
                    summary="利用上限を確認できません。",
                )
                try:
                    raise SubscriptionGateError("利用上限を確認できません。")
                except SubscriptionGateError as cause:
                    raise QualificationRunError("scope writerを開始できません。") from cause

            coordinator._run_human = gated_scope
            with patch(
                "tools.question_review_console.qualification_runs."
                "sync_after_patch_update"
            ) as artifact_sync:
                with self.assertRaisesRegex(QuestionQueuePaused, "利用上限"):
                    coordinator._run_maintenance_flow(
                        "new-exam",
                        parent["runId"],
                        lambda _message: None,
                    )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(len(writer_ids), 1)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(completed["queueStatus"], "partial")
        self.assertEqual(completed["pauseKind"], "external_provider")
        self.assertTrue(completed["retrySafe"])
        self.assertTrue(
            all(
                stage["status"] == "blocked"
                for question in completed["questionExecutions"]
                for stage in question["stages"]
            )
        )
        artifact_sync.assert_not_called()
        self.assertEqual(synchronizer.calls, [])

    def test_safe_category_setup_failure_blocks_only_dependent_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer(fail_on_writer=2)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    LawSourceInventory(),
                    ["question_type", "category_setup", "question_set"],
                    app_server=app_server,
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

            run = coordinator.store.get("new-exam", parent["runId"])
            stages = {
                stage["stageId"]: stage["status"]
                for question in run["questionExecutions"]
                for stage in question["stages"]
            }
            phase_statuses = {
                phase["id"]: phase["status"]
                for phase in run["phaseExecutions"]
            }
            self.assertEqual(result["queueStatus"], "partial")
            self.assertEqual(run["status"], "succeeded")
            self.assertTrue(run["retrySafe"])
            self.assertEqual(stages["question_type"], "validated")
            self.assertEqual(stages["question_set"], "blocked")
            self.assertEqual(phase_statuses["question_type"], "succeeded")
            self.assertEqual(phase_statuses["category_setup"], "failed")
            self.assertEqual(phase_statuses["question_set"], "partial")
            self.assertEqual(
                [kwargs["work_type"] for _prompt, kwargs in app_server.calls],
                [
                    "maintenance_prepare_question_type",
                    "maintenance_question_type",
                    "maintenance_category_setup",
                ],
            )
            self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

    def test_pipeline_warms_up_then_prefetches_without_reordering_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
            )
            first_id = "new-exam-2026-q1"
            second_id = "new-exam-2026-q2"
            writer_started = threading.Event()
            second_prepared = threading.Event()
            lock = threading.Lock()
            active_preparations = 0
            max_preparations = 0
            active_writers = 0
            max_writers = 0
            writer_order = []

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
                    if question_id == second_id:
                        self.assertTrue(writer_started.wait(timeout=2))
                    self._mark_question_prepared(
                        coordinator,
                        qualification,
                        run_id,
                        target,
                        stage_id,
                    )
                    if question_id == second_id:
                        second_prepared.set()
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
                    if question_id == first_id:
                        writer_started.set()
                        self.assertTrue(second_prepared.wait(timeout=2))
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
        self.assertEqual(writer_order, [first_id, second_id])
        self.assertLessEqual(max_preparations, 2)
        self.assertEqual(max_preparations, 1)
        self.assertEqual(max_writers, 1)

    def test_prefetch_window_uses_five_read_only_workers_after_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(6),
                ["question_type"],
            )
            probe_id = parent["questionExecutions"][0]["questionId"]
            concurrent = threading.Barrier(5)
            lock = threading.Lock()
            active = 0
            max_active = 0

            def prepare_item(
                qualification,
                run_id,
                _phase_prompt,
                target,
                stage_id,
                _emit,
            ):
                nonlocal active, max_active
                question_id = str(target["id"])
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    if question_id != probe_id:
                        concurrent.wait(timeout=2)
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
                        active -= 1

            def commit_child(qualification, child_run_id, *_args, **_kwargs):
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

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(parent["questionConcurrency"], 5)
        self.assertEqual(parent["parallelWorkerLimit"], 5)
        self.assertEqual(max_active, 5)

    def test_prefetches_first_pending_stage_for_each_question_in_parallel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(6),
                ["question_type", "question_intent"],
            )
            probe_id = parent["questionExecutions"][0]["questionId"]
            for question in parent["questionExecutions"][1:]:
                coordinator.store.update_question_stage(
                    "new-exam",
                    parent["runId"],
                    question["questionId"],
                    "question_type",
                    status="validated",
                )

            concurrent = threading.Barrier(5)
            lock = threading.Lock()
            active = 0
            max_active = 0

            def prepare_item(
                qualification,
                run_id,
                _phase_prompt,
                target,
                stage_id,
                _emit,
            ):
                nonlocal active, max_active
                question_id = str(target["id"])
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                try:
                    if question_id != probe_id and stage_id == "question_intent":
                        concurrent.wait(timeout=2)
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
                        active -= 1

            def commit_child(qualification, child_run_id, *_args, **_kwargs):
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

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(max_active, 5)

    def test_question_concurrency_can_be_raised_to_ten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, CountedSourceInventory(11)),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=FlowAppServer(),
            )
            preview_five = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                question_concurrency=5,
            )
            preview_ten = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                question_concurrency=10,
            )
            started = coordinator.start(
                "new-exam",
                "question_type",
                "outdated",
                preview_five["previewToken"],
                stage_ids=["question_type"],
                list_group_ids=["2026"],
                question_concurrency=10,
            )
            parent = started["run"]

        self.assertEqual(preview_five["previewToken"], preview_ten["previewToken"])
        self.assertEqual(parent["questionConcurrency"], 10)
        self.assertEqual(parent["parallelWorkerLimit"], 10)

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

    def test_safe_validation_feedback_retries_with_new_children_until_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
            )
            prompts = []
            child_ids = []

            def commit_child(
                qualification,
                child_run_id,
                prompt,
                *_args,
                **_kwargs,
            ):
                child_ids.append(child_run_id)
                prompts.append(prompt)
                if len(child_ids) < 3:
                    self._mark_child_failed_safely(
                        coordinator,
                        qualification,
                        child_run_id,
                        summary=f"attempt {len(child_ids)} failed",
                    )
                    raise RuntimeError("machine validation failed")
                self._mark_child_succeeded(
                    coordinator,
                    qualification,
                    child_run_id,
                )

            coordinator._prepare_question_item = self._prepare_all_questions(
                coordinator
            )
            coordinator._run_human = commit_child
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])
            stage = completed["questionExecutions"][0]["stages"][0]
            improvement_report = json.loads(
                (root / completed["improvementReportPath"]).read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(stage["status"], "validated")
        self.assertEqual(stage["childRunIds"], child_ids)
        self.assertEqual(completed["childRunIds"], child_ids)
        self.assertEqual(len(set(child_ids)), 3)
        self.assertEqual(
            [attempt["status"] for attempt in stage["validationAttempts"]],
            ["failed", "failed", "validated"],
        )
        self.assertEqual(
            [
                attempt["feedback"]["status"]
                for attempt in stage["validationAttempts"]
            ],
            ["retryable", "retryable", "accepted"],
        )
        self.assertEqual(
            [
                attempt["feedback"]["childRunId"]
                for attempt in stage["validationAttempts"][:2]
            ],
            child_ids[:2],
        )
        first_feedback = stage["validationAttempts"][0]["feedback"]
        self.assertEqual(first_feedback["reason"], "attempt 1 failed")
        self.assertEqual(first_feedback["resultSummary"], "attempt 1 failed")
        self.assertIn("server patch validation", first_feedback["receiptError"])
        self.assertEqual(
            first_feedback["failedChecks"][0]["command"],
            "python check_patch.py",
        )
        self.assertNotIn("検査フィードバック", prompts[0])
        self.assertIn('"attempt": 1', prompts[1])
        self.assertIn("python check_patch.py", prompts[1])
        self.assertIn('"attempt": 1', prompts[2])
        self.assertIn('"attempt": 2', prompts[2])
        self.assertEqual(improvement_report["attemptCount"], 3)
        self.assertEqual(improvement_report["distinctQuestionCount"], 1)

    def test_improvement_report_failure_warns_without_rejecting_validated_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    SourceOnlyInventory(),
                    ["question_type"],
                )
            )

            def commit_child(qualification, child_run_id, *_args, **_kwargs):
                self._mark_child_succeeded(
                    coordinator,
                    qualification,
                    child_run_id,
                )

            coordinator._prepare_question_item = self._prepare_all_questions(
                coordinator
            )
            coordinator._run_human = commit_child
            with patch(
                "tools.question_review_console.qualification_runs."
                "write_improvement_report",
                side_effect=OSError("report storage unavailable"),
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(completed["status"], "succeeded")
        self.assertTrue(completed["receiptValidated"])
        self.assertEqual(completed["queueStatus"], "succeeded")
        self.assertIsNone(completed["improvementReportPath"])
        self.assertIn(
            "report storage unavailable",
            completed["improvementReportWarning"],
        )
        self.assertTrue(result["warning"])
        self.assertIn("改善候補reportを保存できませんでした", result["message"])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

    def test_three_safe_rejections_block_only_that_question_and_continue_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type", "question_intent"],
            )
            failed_id = "new-exam-2026-q1"
            sibling_id = "new-exam-2026-q2"
            writer_calls = []

            def commit_child(qualification, child_run_id, *_args, **_kwargs):
                child = coordinator.store.get(qualification, child_run_id)
                question_id = str(child["progressTargets"][0]["id"])
                stage_id = str(child["flowPhaseId"])
                writer_calls.append((question_id, stage_id, child_run_id))
                if question_id == failed_id:
                    self._mark_child_failed_safely(
                        coordinator,
                        qualification,
                        child_run_id,
                    )
                    raise RuntimeError("machine validation failed")
                self._mark_child_succeeded(
                    coordinator,
                    qualification,
                    child_run_id,
                )

            coordinator._prepare_question_item = self._prepare_all_questions(
                coordinator
            )
            coordinator._run_human = commit_child
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])
            executions = {
                question["questionId"]: question
                for question in completed["questionExecutions"]
            }

        failed_stages = executions[failed_id]["stages"]
        sibling_stages = executions[sibling_id]["stages"]
        self.assertEqual(result["queueStatus"], "partial")
        self.assertTrue(completed["retrySafe"])
        self.assertEqual(
            [stage["status"] for stage in failed_stages],
            ["blocked", "blocked"],
        )
        self.assertEqual(len(failed_stages[0]["validationAttempts"]), 3)
        self.assertTrue(
            all(
                attempt["feedback"]["status"] == "retryable"
                for attempt in failed_stages[0]["validationAttempts"]
            )
        )
        self.assertEqual(
            [question_id for question_id, _stage_id, _child_id in writer_calls[:3]],
            [failed_id, failed_id, failed_id],
        )
        self.assertTrue(
            any(question_id == sibling_id for question_id, *_rest in writer_calls)
        )
        self.assertEqual(sibling_stages[0]["status"], "validated")

    def test_non_retryable_validation_feedback_blocks_question_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                )
            )
            writer_ids = []
            failed_id = "new-exam-2026-q1"

            def commit_child(qualification, child_run_id, *_args, **_kwargs):
                writer_ids.append(child_run_id)
                child = coordinator.store.get(qualification, child_run_id)
                question_id = str(child["progressTargets"][0]["id"])
                if question_id == failed_id:
                    self._mark_child_failed_safely(
                        coordinator,
                        qualification,
                        child_run_id,
                        summary="00_source不変条件に違反しました。",
                    )
                    raise RuntimeError("source immutability violation")
                self._mark_child_succeeded(
                    coordinator,
                    qualification,
                    child_run_id,
                )

            coordinator._prepare_question_item = self._prepare_all_questions(
                coordinator
            )
            coordinator._run_human = commit_child
            with patch(
                "tools.question_review_console.qualification_runs."
                "sync_after_patch_update",
                return_value={"status": "current", "groupId": "2026"},
            ) as artifact_sync:
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            improvement_report_saved = (
                root / completed["improvementReportPath"]
            ).is_file()

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(len(writer_ids), 2)
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["queueStatus"], "partial")
        self.assertTrue(completed["retrySafe"])
        self.assertIsNone(completed["unsafeChildRunId"])
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["blocked", "validated"],
        )
        first_attempt = completed["questionExecutions"][0]["stages"][0][
            "validationAttempts"
        ][0]
        self.assertEqual(first_attempt["feedback"]["status"], "blocked")
        self.assertEqual(first_attempt["status"], "blocked")
        self.assertTrue(improvement_report_saved)
        artifact_sync.assert_called_once()
        self.assertEqual(synchronizer.calls, [])

    def test_writer_subscription_gate_pauses_once_and_remains_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
            )
            writer_ids = []

            def gated_writer(qualification, child_run_id, *_args, **_kwargs):
                writer_ids.append(child_run_id)
                self._mark_child_failed_safely(
                    coordinator,
                    qualification,
                    child_run_id,
                    summary="利用上限を確認できません。",
                )
                try:
                    raise SubscriptionGateError("利用上限を確認できません。")
                except SubscriptionGateError as cause:
                    raise QualificationRunError("writerを開始できません。") from cause

            coordinator._prepare_question_item = self._prepare_all_questions(
                coordinator
            )
            coordinator._run_human = gated_writer
            with self.assertRaisesRegex(QuestionQueuePaused, "利用上限"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            retry_preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
                resumed_from=parent["runId"],
            )

        stage = completed["questionExecutions"][0]["stages"][0]
        self.assertEqual(len(writer_ids), 1)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(completed["queueStatus"], "partial")
        self.assertTrue(completed["retrySafe"])
        self.assertEqual(stage["status"], "blocked")
        self.assertEqual(stage["validationAttempts"][0]["status"], "interrupted")
        self.assertIsNone(stage["validationAttempts"][0]["feedback"])
        self.assertEqual(retry_preview["targetCount"], 1)

    def test_writer_pause_is_persisted_before_slow_prefetch_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(3),
                ["question_type"],
            )
            probe_id = parent["questionExecutions"][0]["questionId"]
            prefetch_started = [threading.Event(), threading.Event()]
            prefetch_lock = threading.Lock()
            prefetch_count = 0
            release_prefetch = threading.Event()
            outcome = []

            def prepare_item(
                qualification,
                run_id,
                _phase_prompt,
                target,
                stage_id,
                _emit,
            ):
                nonlocal prefetch_count
                question_id = str(target["id"])
                if question_id != probe_id:
                    with prefetch_lock:
                        index = prefetch_count
                        prefetch_count += 1
                    prefetch_started[index].set()
                    release_prefetch.wait(timeout=5)
                self._mark_question_prepared(
                    coordinator,
                    qualification,
                    run_id,
                    target,
                    stage_id,
                )
                return True

            def gated_writer(qualification, child_run_id, *_args, **_kwargs):
                self._mark_child_failed_safely(
                    coordinator,
                    qualification,
                    child_run_id,
                    summary="利用上限を確認できません。",
                )
                try:
                    raise SubscriptionGateError("利用上限を確認できません。")
                except SubscriptionGateError as cause:
                    raise QualificationRunError("writerを開始できません。") from cause

            def run_flow():
                try:
                    coordinator._run_maintenance_flow(
                        "new-exam",
                        parent["runId"],
                        lambda _message: None,
                    )
                except QuestionQueuePaused as exc:
                    outcome.append(exc)

            coordinator._prepare_question_item = prepare_item
            coordinator._run_human = gated_writer
            runner = threading.Thread(target=run_flow)
            runner.start()
            self.assertTrue(prefetch_started[0].wait(timeout=2))
            self.assertTrue(prefetch_started[1].wait(timeout=2))
            deadline = time.monotonic() + 2
            paused = coordinator.store.get("new-exam", parent["runId"])
            while paused["status"] != "interrupted" and time.monotonic() < deadline:
                time.sleep(0.01)
                paused = coordinator.store.get("new-exam", parent["runId"])
            self.assertEqual(paused["status"], "interrupted")
            self.assertEqual(paused["pauseKind"], "external_provider")
            self.assertTrue(runner.is_alive())
            release_prefetch.set()
            runner.join(timeout=5)

        self.assertFalse(runner.is_alive())
        self.assertEqual(len(outcome), 1)

    def test_preparation_subscription_gate_stops_before_other_questions(self):
        class PreparationGateAppServer(FlowAppServer):
            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                raise SubscriptionGateError("利用上限に達しています。")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PreparationGateAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            with self.assertRaisesRegex(QuestionQueuePaused, "利用上限"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            report_saved = (root / completed["improvementReportPath"]).is_file()

        self.assertEqual(len(app_server.calls), 1)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(completed["queueStatus"], "partial")
        self.assertTrue(completed["retrySafe"])
        self.assertEqual(completed["blockedQuestionCount"], 1)
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["blocked", "queued"],
        )
        self.assertEqual(completed["pauseKind"], "external_provider")
        self.assertTrue(report_saved)

    def test_provider_probe_skips_preblocked_question_before_prefetch(self):
        class PreparationGateAppServer(FlowAppServer):
            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                raise SubscriptionGateError("利用上限に達しています。")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PreparationGateAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(3),
                ["question_type"],
                app_server=app_server,
            )
            first_question = parent["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                first_question["questionId"],
                "question_type",
                status="blocked",
                error="事前保留",
                block_dependents=True,
            )

            with self.assertRaisesRegex(QuestionQueuePaused, "利用上限"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(len(app_server.calls), 1)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["blocked", "blocked", "queued"],
        )

    def test_prefetch_provider_pause_is_not_overwritten_by_partial_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
            )
            first_id = parent["questionExecutions"][0]["questionId"]
            pause_started = threading.Event()

            def prepare_item(
                qualification,
                run_id,
                _phase_prompt,
                target,
                stage_id,
                _emit,
            ):
                if str(target["id"]) != first_id:
                    pause_started.set()
                    raise QuestionQueuePaused(
                        "provider停止",
                        pause_kind="external_provider",
                    )
                self._mark_question_prepared(
                    coordinator,
                    qualification,
                    run_id,
                    target,
                    stage_id,
                )
                return True

            def commit_child(qualification, child_run_id, *_args, **_kwargs):
                self.assertTrue(pause_started.wait(timeout=2))
                self._mark_child_succeeded(
                    coordinator,
                    qualification,
                    child_run_id,
                )

            coordinator._prepare_question_item = prepare_item
            coordinator._run_human = commit_child
            with self.assertRaisesRegex(QuestionQueuePaused, "provider停止"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(completed["pauseKind"], "external_provider")
        self.assertFalse(completed["receiptValidated"])

    def test_read_only_preparation_write_notice_blocks_each_question_independently(self):
        class ReadOnlyViolationAppServer(FlowAppServer):
            def run_turn(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                return AppServerTurnResult(
                    thread_id="thread-read-only-violation",
                    session_id="session-read-only-violation",
                    turn_id="turn-read-only-violation",
                    final_message="proposal",
                    model="gpt-test",
                    service_tier=None,
                    changed_files=("unexpected.json",),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = ReadOnlyViolationAppServer()
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(len(app_server.calls), 2)
        self.assertEqual(completed["status"], "succeeded")
        self.assertIsNone(completed["pauseKind"])
        self.assertTrue(completed["retrySafe"])
        self.assertTrue(completed["receiptValidated"])
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["blocked", "blocked"],
        )

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
        self.assertEqual(synchronizer.calls, [])

    def test_restart_after_validated_save_resumes_from_logical_projection(self):
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
                    raise SystemExit("simulated process stop after validated save")
                return updated

            coordinator.store.update_question_stage = crash_after_validated
            with self.assertRaisesRegex(SystemExit, "after validated save"):
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
        writer_event = "session:maintenance_question_intent"
        self.assertIn(writer_event, events)
        self.assertEqual(app_server.writer_count, 1)
        self.assertGreaterEqual(completed["workVersionReceipt"]["recordedCount"], 1)

    def test_failed_partial_retry_uses_logical_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _app_server, previous = (
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
                error="この問題の後続工程を再試行します。",
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
            phase_executions[0]["status"] = "partial"
            phase_executions[1]["status"] = "partial"
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="failed",
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

            coordinator._run_human = run_child
            result = coordinator._run_maintenance_flow(
                "new-exam",
                resumed["runId"],
                lambda _message: None,
            )

        downstream_writer = f"writer:question_intent:{validated_id}"
        self.assertEqual(result["queueStatus"], "partial")
        self.assertIn(downstream_writer, events)

    def test_queue_block_preserves_not_applicable_stage(self):
        cases = (
            ("question_type", ["not_applicable", "blocked"]),
            ("question_intent", ["blocked", "not_applicable"]),
        )
        for terminal_stage_id, expected_statuses in cases:
            with (
                self.subTest(
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
                coordinator._block_remaining_queue(
                    "new-exam", parent["runId"], "停止理由"
                )
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

    def test_read_only_preparation_pauses_once_on_app_server_unavailability(self):
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
                "outcomes": [CodexAppServerError("stdio送信に失敗しました。")],
                "paused": True,
            },
            "model_capacity": {
                "outcomes": [
                    CodexAppServerError(
                        "Selected model is at capacity. Please try a different model."
                    ),
                ],
                "paused": True,
            },
            "subscription_gate": {
                "outcomes": [SubscriptionGateError("利用上限を確認できません。")],
                "paused": True,
            },
            "success": {
                "outcomes": [result()],
                "prepared": True,
            },
            "changed_files": {
                "outcomes": [result(changed_files=("unexpected.json",))],
                "prepared": False,
            },
            "proposal_integrity": {
                "outcomes": [result(summary="")],
                "prepared": False,
            },
            "ordinary_runtime_error": {
                "outcomes": [RuntimeError("ordinary preparation failure")],
                "prepared": False,
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

                if expected.get("paused"):
                    with self.assertRaises(QuestionQueuePaused):
                        coordinator._prepare_question_item(
                            "new-exam",
                            parent["runId"],
                            phase_prompt,
                            target,
                            "question_type",
                            emitted.append,
                        )
                    prepared = False
                else:
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

                self.assertEqual(prepared, expected.get("prepared", False))
                self.assertEqual(len(app_server.calls), 1)
                self.assertEqual(stage["attempts"], 1)
                self.assertEqual(
                    stage["status"],
                    "prepared" if expected.get("prepared") else "blocked",
                )
                self.assertFalse(any("再試行" in message for message in emitted))

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
        self.assertEqual(synchronizer.calls, [])

    def test_multi_group_final_sync_excludes_preblocked_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    MultiGroupSourceInventory(),
                    ["question_type", "question_intent"],
                    app_server=FlowAppServer(),
                    group_ids=["2025", "2026"],
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
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

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])
        self.assertEqual(
            [stage["status"] for stage in questions["2025"]["stages"]],
            ["blocked", "blocked"],
        )
        self.assertEqual(
            {
                group["listGroupId"]: group["status"]
                for group in run["artifactSync"]["groups"]
            },
            {"2026": "succeeded"},
        )

    def test_next_question_scope_error_does_not_stop_current_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type"],
                    app_server=app_server,
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            original_stage_spec = coordinator._question_major_stage_spec

            def stage_spec(*args, **kwargs):
                question_id = str(args[3])
                if question_id == "new-exam-2026-q2":
                    raise QuestionItemError("q2のrecord scopeを解決できません。")
                return original_stage_spec(*args, **kwargs)

            coordinator._question_major_stage_spec = stage_spec

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

            run = coordinator.store.get("new-exam", parent["runId"])
            states = {
                question["questionId"]: question["stages"][0]["status"]
                for question in run["questionExecutions"]
            }
            self.assertEqual(result["queueStatus"], "partial")
            self.assertEqual(states["new-exam-2026-q1"], "validated")
            self.assertEqual(states["new-exam-2026-q2"], "blocked")
            self.assertEqual(
                app_server.successful_writes,
                [("new-exam-2026-q1", "question_type")],
            )
            self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

    def test_later_stage_rechecks_only_question_changed_by_prior_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    TwoQuestionSourceInventory(),
                    ["question_type", "question_intent"],
                    app_server=app_server,
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            coordinator._validated_queue_stage_changed = lambda *_args: True
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                "new-exam-2026-q2",
                "question_type",
                status="blocked",
                error="fixtureで対象外",
                block_dependents=True,
            )
            original_phase_plan = coordinator._flow_phase_plan_prompt
            intent_plan_calls = 0

            def phase_plan(parent_run, phase):
                nonlocal intent_plan_calls
                plan, prompt = original_phase_plan(parent_run, phase)
                if phase["id"] != "question_intent":
                    return plan, prompt
                intent_plan_calls += 1
                if intent_plan_calls > 1:
                    return plan, prompt
                plan = copy.deepcopy(plan)
                plan.update(
                    targetCount=0,
                    workItemCount=0,
                    progressTargets=[],
                    policyTargets={"question_intent": []},
                )
                return plan, prompt

            coordinator._flow_phase_plan_prompt = phase_plan

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

            run = coordinator.store.get("new-exam", parent["runId"])
            q1 = next(
                question
                for question in run["questionExecutions"]
                if question["questionId"] == "new-exam-2026-q1"
            )
            self.assertEqual(result["queueStatus"], "partial")
            self.assertEqual(
                [stage["status"] for stage in q1["stages"]],
                ["validated", "validated"],
            )
            self.assertEqual(intent_plan_calls, 1)
            self.assertEqual(
                app_server.successful_writes,
                [
                    ("new-exam-2026-q1", "question_type"),
                    ("new-exam-2026-q1", "question_intent"),
                ],
            )
            self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

    def test_only_linked_changed_receipt_activates_general_placeholder(self):
        for receipt_case in ("empty", "missing_child_link", "missing_changed_files"):
            with (
                self.subTest(receipt_case=receipt_case),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                app_server = PerQuestionQueueAppServer()
                coordinator, _sync, _server, parent = self._start_deferred_flow(
                    root,
                    SourceOnlyInventory(),
                    ["question_type", "question_intent"],
                    app_server=app_server,
                )
                coordinator._repository_file_fingerprints = lambda *_args: {}
                original_phase_plan = coordinator._flow_phase_plan_prompt

                def phase_plan(parent_run, phase):
                    plan, prompt = original_phase_plan(parent_run, phase)
                    if phase["id"] == "question_intent":
                        plan = copy.deepcopy(plan)
                        plan.update(
                            targetCount=0,
                            workItemCount=0,
                            progressTargets=[],
                            policyTargets={"question_intent": []},
                        )
                    return plan, prompt

                coordinator._flow_phase_plan_prompt = phase_plan
                if receipt_case != "empty":
                    original_commit = coordinator._commit_question_major_item

                    def remove_receipt_link(*args, **kwargs):
                        committed = original_commit(*args, **kwargs)
                        spec = args[2]
                        if committed and spec["stageId"] == "question_type":
                            stage = coordinator._queue_stage(
                                coordinator.store.get("new-exam", parent["runId"]),
                                spec["target"]["id"],
                                "question_type",
                            )
                            if receipt_case == "missing_child_link":
                                coordinator.store.update_question_stage(
                                    "new-exam",
                                    parent["runId"],
                                    spec["target"]["id"],
                                    "question_type",
                                    childRunIds=[],
                                )
                            else:
                                child_id = stage["childRunIds"][-1]
                                child = coordinator.store.get("new-exam", child_id)
                                result = dict(child["result"])
                                result.pop("changedFiles")
                                coordinator.store.update(
                                    "new-exam", child_id, result=result
                                )
                        return committed

                    coordinator._commit_question_major_item = (
                        remove_receipt_link
                    )
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
                stages = coordinator.store.get(
                    "new-exam", parent["runId"]
                )["questionExecutions"][0]["stages"]

                self.assertEqual(
                    [stage["status"] for stage in stages],
                    ["validated", "not_applicable"],
                )
                self.assertEqual(
                    app_server.successful_writes,
                    [("new-exam-2026-q1", "question_type")],
                )

    def test_changed_question_can_make_later_law_stage_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["law_context", "law_audit"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(
            [stage["status"] for stage in run["questionExecutions"][0]["stages"]],
            ["validated", "not_applicable"],
        )
        self.assertEqual(
            app_server.successful_writes,
            [("new-exam-2026-q1", "law_context")],
        )

    def test_changed_question_can_make_placeholder_stage_applicable(self):
        class MutableLawInventory(LawSourceInventory):
            def __init__(self):
                self.law_related = False

            def group(self, qualification, list_group_id):
                group = (
                    super().group(qualification, list_group_id)
                    if self.law_related
                    else SourceOnlyInventory.group(
                        self, qualification, list_group_id
                    )
                )
                question = group["questions"][0]
                question["isLawRelated"] = self.law_related
                question["projected"] = {
                    **question["projected"],
                    "isLawRelated": self.law_related,
                }
                return group

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = MutableLawInventory()
            app_server = PerQuestionQueueAppServer()
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, inventory),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=app_server,
            )
            preview = coordinator.preview(
                "new-exam",
                "law_context",
                "remaining",
                stage_ids=["law_context", "law_audit"],
                list_group_ids=["2026"],
            )
            parent = coordinator.start(
                "new-exam",
                preview["stageId"],
                "remaining",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )["run"]
            coordinator._repository_file_fingerprints = lambda *_args: {}
            coordinator._validated_queue_stage_changed = lambda *_args: True
            original_commit = coordinator._commit_question_major_item
            writer_stages = []

            def mark_child_succeeded(qualification, child_run_id, *_args, **_kwargs):
                child = coordinator.store.get(qualification, child_run_id)
                writer_stages.append(str(child["stageId"]))
                self._mark_child_succeeded(
                    coordinator,
                    qualification,
                    child_run_id,
                )

            coordinator._run_human = mark_child_succeeded

            def commit_then_flip(*args, **kwargs):
                committed = original_commit(*args, **kwargs)
                spec = args[2]
                if committed and spec["stageId"] == "law_context":
                    inventory.law_related = True
                return committed

            coordinator._commit_question_major_item = commit_then_flip

            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            run = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(
            [stage["status"] for stage in run["questionExecutions"][0]["stages"]],
            ["validated", "validated"],
        )
        self.assertEqual(writer_stages, ["law_context", "law_audit"])

    def test_writer_reprepares_when_projection_changes_after_read_only_step(self):
        class MutableProjectionInventory(SourceOnlyInventory):
            def __init__(self):
                self.body = "準備前"

            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                group["questions"][0]["projected"] = {
                    **group["questions"][0]["projected"],
                    "questionBodyText": self.body,
                }
                return group

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = MutableProjectionInventory()
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                inventory,
                ["question_type"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            original_prepare = coordinator._prepare_question_item
            prepare_count = 0

            def prepare_then_change(*args, **kwargs):
                nonlocal prepare_count
                prepared = original_prepare(*args, **kwargs)
                prepare_count += 1
                if prepare_count == 1:
                    inventory.body = "手動patch更新後"
                return prepared

            coordinator._prepare_question_item = prepare_then_change
            messages = []
            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                messages.append,
            )
            run = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(prepare_count, 2)
        self.assertEqual(
            [kwargs["work_type"] for _question, _prompt, kwargs in app_server.calls],
            [
                "maintenance_prepare_question_type",
                "maintenance_prepare_question_type",
                "maintenance_question_type",
            ],
        )
        self.assertEqual(
            run["questionExecutions"][0]["stages"][0]["status"],
            "validated",
        )
        self.assertTrue(any("最新入力で準備し直します" in value for value in messages))

    def test_writer_skips_law_audit_when_latest_projection_is_not_law_related(self):
        class MutableLawAuditInventory(SourceOnlyInventory):
            def __init__(self):
                self.law_related = True

            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                question = group["questions"][0]
                question["isLawRelated"] = self.law_related
                question["projected"] = {
                    **question["projected"],
                    "isLawRelated": self.law_related,
                    "lawGroundedExplanationNotNeeded": not self.law_related,
                }
                return group

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = MutableLawAuditInventory()
            app_server = PerQuestionQueueAppServer()
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, inventory),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=app_server,
            )
            preview = coordinator.preview(
                "new-exam",
                "law_audit",
                "remaining",
                list_group_ids=["2026"],
            )
            parent = coordinator.start(
                "new-exam",
                preview["stageId"],
                "remaining",
                preview["previewToken"],
                list_group_ids=preview["scopeListGroupIds"],
            )["run"]
            coordinator._repository_file_fingerprints = lambda *_args: {}
            original_prepare = coordinator._prepare_question_item

            def prepare_then_reclassify(*args, **kwargs):
                prepared = original_prepare(*args, **kwargs)
                inventory.law_related = False
                return prepared

            coordinator._prepare_question_item = prepare_then_reclassify
            messages = []
            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                messages.append,
            )
            stage = coordinator.store.get(
                "new-exam", parent["runId"]
            )["questionExecutions"][0]["stages"][0]

        self.assertEqual(stage["status"], "not_applicable")
        self.assertEqual(
            [kwargs["work_type"] for _question, _prompt, kwargs in app_server.calls],
            ["maintenance_prepare_law_audit"],
        )
        self.assertTrue(any("writerを省略" in value for value in messages))

    def test_explicit_group_refresh_keeps_non_law_question_in_law_audit(self):
        self.assertTrue(
            QualificationRunCoordinator._projection_stage_applicable(
                {"mode": "group_refresh"},
                "law_audit",
                {"isLawRelated": False},
            )
        )
        self.assertFalse(
            QualificationRunCoordinator._projection_stage_applicable(
                {"mode": "remaining"},
                "law_audit",
                {"isLawRelated": False},
            )
        )

    def test_dynamic_replan_uses_promoted_phase_mode_for_law_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, NonLawSourceInventory()),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            phase_plan = coordinator._plan(
                "new-exam",
                "law_audit",
                "group_refresh",
                None,
                list_group_ids=["2026"],
            )
            parent = {**phase_plan, "mode": "outdated"}

            _plan, target = coordinator._dynamic_question_phase_plan(
                "new-exam",
                parent,
                {"id": "law_audit"},
                phase_plan,
                "new-exam-2026-q1",
            )

        self.assertIsNotNone(target)

    def test_missing_logical_projection_blocks_only_that_question(self):
        class MissingProjectionInventory(SourceOnlyInventory):
            projected_input = None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                MissingProjectionInventory(),
                ["question_type"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            stage = coordinator.store.get(
                "new-exam", parent["runId"]
            )["questionExecutions"][0]["stages"][0]

        self.assertEqual(stage["status"], "blocked")
        self.assertIn("logicalProjection", stage["error"])
        self.assertEqual(app_server.calls, [])

    def test_resume_does_not_repeat_succeeded_category_scope_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, previous = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["category_setup", "question_set"],
                app_server=app_server,
            )
            question = previous["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question["questionId"],
                "question_set",
                status="blocked",
                error="question_setだけ再開する。",
            )
            phases = copy.deepcopy(previous["phaseExecutions"])
            phases[0]["status"] = "succeeded"
            phases[1]["status"] = "partial"
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="failed",
                queueStatus="partial",
                phaseExecutions=phases,
            )
            self._write_valid_category(root)
            preview = coordinator.preview(
                "new-exam",
                "category_setup",
                "outdated",
                stage_ids=["category_setup", "question_set"],
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
            ["question_set"],
        )

    def test_fully_succeeded_scope_flow_has_nothing_to_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, previous = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["category_setup", "question_set"],
                app_server=PerQuestionQueueAppServer(),
            )
            question = previous["questionExecutions"][0]
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question["questionId"],
                "question_set",
                status="validated",
                error=None,
            )
            phases = copy.deepcopy(previous["phaseExecutions"])
            for phase in phases:
                phase["status"] = "succeeded"
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="succeeded",
                queueStatus="succeeded",
                phaseExecutions=phases,
            )
            self._write_valid_category(root)

            with self.assertRaisesRegex(
                QualificationRunError,
                "再実行が必要な問題はありません",
            ):
                coordinator.preview(
                    "new-exam",
                    "category_setup",
                    "outdated",
                    stage_ids=["category_setup", "question_set"],
                    list_group_ids=["2026"],
                    resumed_from=previous["runId"],
                )

    def test_each_writer_reads_run_local_logical_projection(self):
        class ProjectingInventory(TwoQuestionSourceInventory):
            def __init__(self):
                self.projected_calls = []

            def projected_input(
                self,
                qualification,
                list_group_id,
                source_record_ref,
            ):
                self.projected_calls.append(source_record_ref)
                return SimpleNamespace(
                    record={
                        "original_question_id": source_record_ref,
                        "questionBodyText": "現在の論理入力",
                    },
                    applied_files=("output/new-exam/current-patch.json",),
                    errors=(),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = ProjectingInventory()
            app_server = PerQuestionQueueAppServer()
            coordinator, _synchronizer, _app_server, parent = (
                self._start_deferred_flow(
                    root,
                    inventory,
                    ["question_type"],
                    app_server=app_server,
                )
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            original_record_versions = coordinator._record_work_versions

            def record_versions_without_group_scan(run):
                original_group = inventory.group

                def reject_group_scan(*_args, **_kwargs):
                    raise AssertionError(
                        "一問writerの工程版記録で年度全体を再構築しました。"
                    )

                inventory.group = reject_group_scan
                try:
                    return original_record_versions(run)
                finally:
                    inventory.group = original_group

            coordinator._record_work_versions = record_versions_without_group_scan

            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

            run = coordinator.store.get("new-exam", parent["runId"])
            projected_paths = [
                stage["projectedInputPath"]
                for question in run["questionExecutions"]
                for stage in question["stages"]
            ]
            payloads = [
                json.loads((root / path).read_text(encoding="utf-8"))
                for path in projected_paths
            ]

            self.assertEqual(len(inventory.projected_calls), 6)
        self.assertEqual(len(set(projected_paths)), 2)
        self.assertTrue(
            all("logicalProjection:" in prompt for _question, prompt, _ in app_server.calls)
        )
        self.assertTrue(
            all(
                payload["schemaVersion"] == "question-maintenance-projection/v1"
                for payload in payloads
            )
        )

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
