from types import SimpleNamespace
from dataclasses import replace

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
    _source_binding_accepts_identity,
)


class SourceBindingAliasTests(unittest.TestCase):
    def test_existing_review_id_alias_keeps_stable_source_binding(self):
        binding = {
            "sourceQuestionKey": "sample:2026:q1",
            "reviewQuestionId": "firestore:q1-a,q1-b",
            "sourceRecordRef": "source.json#1",
            "aliases": ["legacy-ui-id"],
        }

        self.assertTrue(
            _source_binding_accepts_identity(
                binding,
                {
                    "sourceQuestionKey": "sample:2026:q1",
                    "reviewQuestionId": "legacy-ui-id",
                    "sourceRecordRef": "source.json#1",
                },
            )
        )
        self.assertFalse(
            _source_binding_accepts_identity(
                binding,
                {
                    "sourceQuestionKey": "sample:2026:q1",
                    "reviewQuestionId": "other-id",
                    "sourceRecordRef": "source.json#1",
                },
            )
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

    def test_parent_progress_does_not_double_count_validated_then_blocked_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            targets = [
                {
                    "id": question_id,
                    "questionId": question_id,
                    "questionKey": question_id,
                    "sourceQuestionKey": f"source-{question_id}",
                    "reviewQuestionId": f"review-{question_id}",
                    "sourceRecordRef": f"source.json#{index}",
                    "listGroupId": "2026",
                }
                for index, question_id in enumerate(("q1", "q2"), start=1)
            ]
            parent_plan = {
                **FakeWorkflow().plan("sample", "question_type", "remaining"),
                "kind": "orchestration",
                "workType": "maintenance_flow",
                "targetCount": 2,
                "workItemCount": 2,
                "progressTargets": targets,
                "policyTargets": {"question_type": ["q1", "q2"]},
                "questionExecutions": [
                    {
                        **target,
                        "status": "blocked",
                        "stages": [
                            {
                                "stageId": "question_type",
                                "status": "blocked",
                            }
                        ],
                    }
                    for target in targets
                ],
            }
            parent = store.create(parent_plan, status="running")
            child_plan = {
                **FakeWorkflow().plan("sample", "question_type", "remaining"),
                "parentRunId": parent["runId"],
                "progressTargets": [targets[0]],
                "policyTargets": {"question_type": ["q1"]},
            }
            child = store.create(child_plan, status="running", prompt="work")
            (root / child["progressReceiptPath"]).write_text(
                "\n".join(
                    json.dumps(event)
                    for event in (
                        {"event": "question_started", "questionId": "q1"},
                        {
                            "event": "stage_completed",
                            "questionId": "q1",
                            "stageId": "question_type",
                        },
                        {"event": "question_completed", "questionId": "q1"},
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            store.update(
                "sample",
                child["runId"],
                status="succeeded",
                receiptValidated=True,
            )
            store.update(
                "sample",
                parent["runId"],
                status="interrupted",
                childRunIds=[child["runId"]],
                questionExecutionSummary={
                    "validatedQuestionCount": 0,
                    "blockedQuestionCount": 2,
                    "completedWorkItemCount": 0,
                    "blockedWorkItemCount": 2,
                    "pendingWorkItemCount": 0,
                },
            )

            progress = store.combined_progress("sample", parent["runId"])

        self.assertEqual(progress["processedQuestionCount"], 2)
        self.assertEqual(progress["targetQuestionCount"], 2)
        self.assertLessEqual(
            progress["processedQuestionCount"],
            progress["targetQuestionCount"],
        )

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

    def test_uncommitted_external_change_is_not_attributed_to_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = FakeWorkflow().plan("sample", "law_audit")
            plan["sandbox"] = "workspace-write"
            run = coordinator.store.create(
                plan, status="running", prompt="整備する。"
            )
            outside = "scripts/unrelated_work.py"
            coordinator.store.update(
                "sample",
                run["runId"],
                result={
                    "status": "succeeded",
                    "commands": [],
                    "changedFiles": [outside],
                },
            )

            attribution = coordinator._validate_changed_files(
                "sample",
                run["runId"],
                coordinator.store.get("sample", run["runId"]),
                (),
                (outside,),
            )

        self.assertEqual(attribution["changedFiles"], [])
        self.assertEqual(
            attribution["externalConcurrentChangedFiles"],
            [outside],
        )
        self.assertEqual(
            attribution["ignoredReceiptChangedFiles"],
            [outside],
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

    def test_succeeded_partial_resume_does_not_requeue_validated_policy_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _sync, _app_server, parent = self._start_deferred_flow(
                Path(directory),
                TwoQuestionSourceInventory(),
                ["question_type"],
            )
            first, second = parent["questionExecutions"]
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                first["questionId"],
                "question_type",
                status="validated",
                policyFingerprint="previous-policy",
            )
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                second["questionId"],
                "question_type",
                status="blocked",
                error="この問題だけ再実行する。",
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
            resumed = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
                resumed_from=parent["runId"],
            )["run"]

        self.assertEqual(preview["targetCount"], 1)
        self.assertEqual(preview["workItemCount"], 1)
        self.assertEqual(
            [question["questionId"] for question in resumed["questionExecutions"]],
            [second["questionId"]],
        )

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
            external = ["docs/goals/question-maintenance/state.yaml"]
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

    def test_recent_hides_failed_delta_reconciliation_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            visible = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="succeeded",
                prompt="visible",
            )
            receipt = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="succeeded",
                prompt="receipt",
            )
            coordinator.store.update(
                "sample",
                receipt["runId"],
                schemaVersion="failed-delta-reconciliation/v1",
            )

            recent = coordinator.recent("sample")

        self.assertEqual(
            [run["runId"] for run in recent["runs"]],
            [visible["runId"]],
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
                    "maintenance_question_type_candidate",
                    "maintenance_category_setup",
                ],
            )
            self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])


    @staticmethod
    def _write_counted_sources(root, count):
        source_dir = (
            root
            / "output/new-exam/questions_json/2026/00_source"
        )
        source_dir.mkdir(parents=True, exist_ok=True)
        for number in range(1, count + 1):
            question_id = f"new-exam-2026-q{number}"
            (source_dir / f"question_2026_{number}.json").write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "original_question_id": question_id,
                                "sourceQuestionKey": f"new-exam:2026:q{number}",
                                "reviewQuestionId": question_id,
                                "sourceRecordRef": f"question_2026_{number}.json#0",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def test_five_questions_use_one_model_turn_without_read_only_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(5),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 5)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(app_server.batch_calls, [tuple(
            f"new-exam-2026-q{number}" for number in range(1, 6)
        )])
        self.assertEqual(len(completed["childRunIds"]), 1)
        self.assertEqual(completed["validatedQuestionCount"], 5)
        self.assertEqual(completed["modelBatchSize"], 5)
        self.assertEqual(completed["modelWorkerLimit"], 1)

    def test_failed_question_retries_after_normal_queue_is_drained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer(
                failed_question_id="new-exam-2026-q1"
            )
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(6),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 6)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(
            app_server.batch_calls,
            [
                tuple(f"new-exam-2026-q{number}" for number in range(1, 7)),
                ("new-exam-2026-q1",),
                ("new-exam-2026-q1",),
            ],
        )
        self.assertEqual(completed["validatedQuestionCount"], 5)
        self.assertEqual(completed["blockedQuestionCount"], 1)
        self.assertEqual(
            [kwargs["model"] for _question_id, _prompt, kwargs in app_server.calls],
            ["gpt-5.5", "gpt-5.6-sol", "gpt-5.6-sol"],
        )
        failed_stage = completed["questionExecutions"][0]["stages"][0]
        self.assertEqual(
            [attempt["requestedModel"] for attempt in failed_stage["validationAttempts"]],
            ["gpt-5.5", "gpt-5.6-sol", "gpt-5.6-sol"],
        )
        self.assertTrue(
            all(
                attempt["requestedReasoningEffort"] == "high"
                and attempt["reasoningEffort"] == "high"
                for attempt in failed_stage["validationAttempts"]
            )
        )
        successful_stage = completed["questionExecutions"][1]["stages"][0]
        self.assertEqual(len(successful_stage["validationAttempts"]), 1)
        self.assertEqual(
            successful_stage["validationAttempts"][0]["requestedModel"],
            "gpt-5.5",
        )

    def test_resumed_fresh_and_failed_questions_use_separate_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 2)
            failed_question_id = parent["questionExecutions"][0]["questionId"]
            fresh_question_id = parent["questionExecutions"][1]["questionId"]
            coordinator.store.update_question_stage(
                "new-exam",
                parent["runId"],
                failed_question_id,
                "question_type",
                validationAttempts=[
                    {
                        "attempt": 1,
                        "status": "failed",
                        "feedback": {"reason": "機械検査に失敗"},
                    }
                ],
            )

            coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

        models_by_batch = {
            batch: kwargs["model"]
            for batch, (_question_id, _prompt, kwargs) in zip(
                app_server.batch_calls,
                app_server.calls,
                strict=True,
            )
        }
        self.assertEqual(
            models_by_batch[(fresh_question_id,)],
            "gpt-5.5",
        )
        self.assertEqual(
            models_by_batch[(failed_question_id,)],
            "gpt-5.6-sol",
        )

    def test_blocked_candidate_stops_only_that_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer(
                failed_question_id="new-exam-2026-q2"
            )
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 2)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(completed["validatedQuestionCount"], 1)
        self.assertEqual(completed["blockedQuestionCount"], 1)
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["validated", "blocked"],
        )

    def test_ambiguous_target_is_blocked_before_model_without_stopping_sibling(self):
        def assert_resolvable(_root, _path, *, binding, aliases):
            del aliases
            if "question_2026_2.json" in binding.source_record_ref:
                raise ValueError("対象レコードが複数あります")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                TwoQuestionSourceInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 2)

            with patch(
                "tools.question_review_console.qualification_runs."
                "assert_target_resolvable",
                side_effect=assert_resolvable,
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(result["queueStatus"], "partial")
        self.assertEqual(app_server.batch_calls, [("new-exam-2026-q1",)])
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["validated", "blocked"],
        )

    def test_small_ten_question_input_uses_one_token_aware_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            app_server.writer_delay = 0.1
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(10),
                ["question_type"],
                app_server=app_server,
                question_concurrency=10,
            )
            self._write_counted_sources(root, 10)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(app_server.max_active_writers, 1)
        self.assertEqual(len(app_server.batch_calls), 1)
        self.assertEqual(len(app_server.batch_calls[0]), 10)

    def test_one_provider_timeout_is_retried_after_other_questions(self):
        class TimeoutOnceAppServer(PerQuestionQueueAppServer):
            def __init__(self):
                super().__init__()
                self.timed_out = False

            def run_turn(self, prompt, **kwargs):
                if not self.timed_out:
                    self.timed_out = True
                    question_ids = self._question_ids(prompt)
                    with self._lock:
                        self.batch_calls.append(tuple(question_ids))
                    kwargs["on_thread_started"]("thread-timeout", "session-timeout")
                    kwargs["on_turn_started"]("thread-timeout", "turn-timeout")
                    raise CodexAppServerError("turnが時間切れになりました。")
                return super().run_turn(prompt, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = TimeoutOnceAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                CountedSourceInventory(6),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 6)

            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(
            app_server.batch_calls,
            [
                tuple(f"new-exam-2026-q{number}" for number in range(1, 7)),
                tuple(f"new-exam-2026-q{number}" for number in range(1, 7)),
            ],
        )

    def test_server_rebases_validated_candidate_into_canonical_patch(self):
        class ServerCandidateAppServer(PerQuestionQueueAppServer):
            def run_turn(self, prompt, **kwargs):
                work_type = kwargs["work_type"]
                if work_type == "maintenance_question_type_candidate":
                    question_id = str(self._candidate_questions(prompt)[0]["questionId"])
                    stage_id = "question_type"
                    patch_relative = (
                        "output/new-exam/questions_json/2026/10_questionType_fixed/"
                        "question_2026_1_questionType_fixed.json"
                    )
                    self.changed_files_by_work_item[(question_id, stage_id)] = [
                        patch_relative
                    ]
                result = super().run_turn(prompt, **kwargs)
                if work_type != "maintenance_question_type_candidate":
                    return result
                payload = json.loads(result.final_message)
                payload["questionResults"][0]["updates"][0]["setFields"] = [
                    {"field": "questionType", "valueJson": '"single_choice"'}
                ]
                return replace(
                    result,
                    final_message=json.dumps(payload, ensure_ascii=False),
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = ServerCandidateAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            source_path = (
                root
                / "output/new-exam/questions_json/2026/00_source/"
                "question_2026_1.json"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "original_question_id": "new-exam-2026-q1",
                                "sourceQuestionKey": "new-exam:2026:q1",
                                "reviewQuestionId": "new-exam-2026-q1",
                                "sourceRecordRef": "question_2026_1.json#0",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                lambda _message: None,
            )
            completed = coordinator.store.get("new-exam", parent["runId"])
            child = coordinator.store.get(
                "new-exam",
                completed["childRunIds"][0],
            )
            patch_path = root / child["result"]["changedFiles"][0]
            records = json.loads(patch_path.read_text(encoding="utf-8"))
            workspace_exists = (
                root
                / "output/question_review_console/workflow_runs/new-exam"
                / child["runId"]
                / "candidate_workspaces"
            ).exists()

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertTrue(child["receiptValidated"])
        self.assertEqual(records[0]["questionType"], "single_choice")
        self.assertFalse(workspace_exists)

    def test_checkpoint_write_failure_rolls_back_before_question_retry(self):
        patch_relative = (
            "output/new-exam/questions_json/2026/10_questionType_fixed/"
            "question_2026_1_questionType_fixed.json"
        )
        question_id = "new-exam-2026-q1"
        app_server = PerQuestionQueueAppServer(
            changed_files_by_work_item={
                (question_id, "question_type"): [patch_relative]
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            source_path = (
                root
                / "output/new-exam/questions_json/2026/00_source/"
                "question_2026_1.json"
            )
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {
                                "original_question_id": question_id,
                                "sourceQuestionKey": "new-exam:2026:q1",
                                "reviewQuestionId": question_id,
                                "sourceRecordRef": "question_2026_1.json#0",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_update = coordinator.store.update
            failed_checkpoints = 0

            def fail_first_success_checkpoint(qualification, run_id, **changes):
                nonlocal failed_checkpoints
                results = changes.get("batchQuestionResults") or []
                if (
                    failed_checkpoints == 0
                    and changes.get("executionPhase")
                    == "server_candidate_checkpoint"
                    and results
                    and results[-1].get("status") == "succeeded"
                ):
                    failed_checkpoints += 1
                    raise OSError("checkpoint unavailable")
                return original_update(qualification, run_id, **changes)

            with patch.object(
                coordinator.store,
                "update",
                side_effect=fail_first_success_checkpoint,
            ):
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            children = [
                coordinator.store.get("new-exam", child_id)
                for child_id in completed["childRunIds"]
            ]
            patch_records = json.loads(
                (root / patch_relative).read_text(encoding="utf-8")
            )

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(failed_checkpoints, 1)
        self.assertEqual(len(app_server.batch_calls), 2)
        self.assertEqual(
            [
                value["status"]
                for child in children
                for value in child["batchQuestionResults"]
            ],
            ["failed", "succeeded"],
        )
        self.assertEqual(children[0]["result"]["changedFiles"], [])
        self.assertEqual(patch_records[0]["questionType"], "true_false")

    def test_commit_validation_failure_rolls_back_once_and_retries_question(self):
        patch_relative = (
            "output/new-exam/questions_json/2026/10_questionType_fixed/"
            "question_2026_1_questionType_fixed.json"
        )
        question_id = "new-exam-2026-q1"
        app_server = PerQuestionQueueAppServer(
            changed_files_by_work_item={
                (question_id, "question_type"): [patch_relative]
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            self._write_counted_sources(root, 1)
            original_validate = coordinator._validate_record_scope
            original_rollback = coordinator.store.rollback_baseline
            validation_calls = 0

            def fail_first_validation(*args, **kwargs):
                nonlocal validation_calls
                validation_calls += 1
                if validation_calls == 1:
                    raise OSError("record scope unavailable")
                return original_validate(*args, **kwargs)

            with patch.object(
                coordinator,
                "_validate_record_scope",
                side_effect=fail_first_validation,
            ), patch.object(
                coordinator.store,
                "rollback_baseline",
                wraps=original_rollback,
            ) as rollback:
                result = coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            children = [
                coordinator.store.get("new-exam", child_id)
                for child_id in completed["childRunIds"]
            ]

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(validation_calls, 2)
        self.assertEqual(rollback.call_count, 1)
        self.assertEqual(len(app_server.batch_calls), 2)
        self.assertEqual(
            [
                value["summary"]
                for child in children
                for value in child["batchQuestionResults"]
            ],
            ["record scope unavailable", f"{question_id}の整備候補を作成した。"],
        )
        self.assertTrue(all(child["retrySafe"] for child in children))

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

            coordinator._repository_file_fingerprints = lambda *_args: {}
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


    def test_provider_gate_retries_batch_then_waits_without_blocking_questions(self):
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
            with self.assertRaisesRegex(QuestionQueuePaused, "回復後に再開"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])
            report_saved = (root / completed["improvementReportPath"]).is_file()

        self.assertEqual(len(app_server.calls), 2)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(completed["queueStatus"], "partial")
        self.assertTrue(completed["retrySafe"])
        self.assertEqual(completed["blockedQuestionCount"], 0)
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["queued", "queued"],
        )
        self.assertEqual(completed["pauseKind"], "external_provider")
        self.assertTrue(report_saved)

    def test_provider_retry_skips_preblocked_question(self):
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

            with self.assertRaisesRegex(QuestionQueuePaused, "回復後に再開"):
                coordinator._run_maintenance_flow(
                    "new-exam",
                    parent["runId"],
                    lambda _message: None,
                )
            completed = coordinator.store.get("new-exam", parent["runId"])

        self.assertEqual(len(app_server.calls), 2)
        self.assertEqual(completed["status"], "interrupted")
        self.assertEqual(
            [
                question["stages"][0]["status"]
                for question in completed["questionExecutions"]
            ],
            ["blocked", "queued", "queued"],
        )


    def test_batch_without_receipt_blocks_each_question_after_deferred_retries(self):
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
        self.assertEqual(len(app_server.calls), 3)
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


    def test_batch_prompt_contains_deterministic_question_identity_and_projection(self):
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
            batch_prompts = [
                prompt
                for prompt, kwargs in app_server.calls
                if kwargs["work_type"] == "maintenance_question_type_candidate"
            ]

        self.assertEqual(len(batch_prompts), 1)
        prompt = batch_prompts[0]
        self.assertIn('"questionId":"new-exam-2026-q1"', prompt)
        self.assertIn('"questionId":"new-exam-2026-q2"', prompt)
        self.assertIn('"sourceRecordRef":"question_2026_1.json#0"', prompt)
        self.assertIn('"sourceRecordRef":"question_2026_2.json#0"', prompt)
        self.assertEqual(prompt.count('"currentRecord":'), 2)
        self.assertIn("file、shell、progress、receipt、git、外部状態は変更しない", prompt)

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
        writer_event = "session:maintenance_question_intent_candidate"
        self.assertIn(writer_event, events)
        self.assertEqual(app_server.writer_count, 1)
        self.assertGreaterEqual(completed["workVersionReceipt"]["recordedCount"], 1)

    def test_retry_safe_failed_queue_can_resume_blocked_question(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _sync, _app_server, previous = (
                self._start_deferred_flow(
                    Path(directory),
                    SourceOnlyInventory(),
                    ["question_type"],
                    app_server=FlowAppServer(),
                )
            )
            question_id = previous["questionExecutions"][0]["questionId"]
            coordinator.store.update_question_stage(
                "new-exam",
                previous["runId"],
                question_id,
                "question_type",
                status="blocked",
                error="安全停止後にこの問題だけ再開する。",
            )
            previous = coordinator.store.update(
                "new-exam",
                previous["runId"],
                status="failed",
                queueStatus="failed",
                retrySafe=True,
            )

            preview = coordinator.preview(
                "new-exam",
                "question_type",
                "outdated",
                list_group_ids=["2026"],
                resumed_from=previous["runId"],
            )

        self.assertEqual(preview["targetCount"], 1)
        self.assertEqual(preview["workItemCount"], 1)


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


    def test_batch_change_detection_is_scoped_to_its_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=FlowAppServer(),
            )
            child_plan = FakeWorkflow().plan(
                "sample",
                "question_type",
                "remaining",
            )
            child_plan.update(
                parallelStrategy="structured_candidate_batch",
                progressTargets=[{"id": "q1"}, {"id": "q2"}],
            )
            child = coordinator.store.create(child_plan, status="succeeded")
            coordinator.store.update(
                "sample",
                child["runId"],
                receiptValidated=True,
                deltaUnknown=False,
                workVersionReceipt={"recordedCount": 1},
                result={
                    "status": "succeeded",
                    "summary": "batch完了",
                    "commands": [{"command": "check", "status": "pass"}],
                    "changedFiles": ["patch.json"],
                },
                batchQuestionResults=[
                    {
                        "questionId": "q1",
                        "status": "succeeded",
                        "changedFiles": ["patch.json"],
                    },
                    {
                        "questionId": "q2",
                        "status": "succeeded",
                        "changedFiles": [],
                    },
                ],
            )
            stage = {"childRunIds": [child["runId"]]}

            self.assertTrue(
                coordinator._validated_queue_stage_changed(
                    "sample", stage, "q1"
                )
            )
            self.assertFalse(
                coordinator._validated_queue_stage_changed(
                    "sample", stage, "q2"
                )
            )

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


    def test_writer_reprepares_only_current_question_when_policy_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = PerQuestionQueueAppServer()
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                root,
                SourceOnlyInventory(),
                ["question_type"],
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            original_policy_check = coordinator._phase_plan_policy_is_current
            policy_check_count = 0

            def policy_is_current(*args, **kwargs):
                nonlocal policy_check_count
                policy_check_count += 1
                if policy_check_count == 2:
                    return False
                return original_policy_check(*args, **kwargs)

            coordinator._phase_plan_policy_is_current = policy_is_current
            messages = []
            result = coordinator._run_maintenance_flow(
                "new-exam",
                parent["runId"],
                messages.append,
            )
            run = coordinator.store.get("new-exam", parent["runId"])
            stage = run["questionExecutions"][0]["stages"][0]
            work_types = [
                kwargs["work_type"]
                for _question, _prompt, kwargs in app_server.calls
            ]

        self.assertEqual(result["queueStatus"], "succeeded")
        self.assertEqual(stage["status"], "validated")
        self.assertEqual(stage["policyRefreshCount"], 1)
        self.assertEqual(
            work_types,
            [
                "maintenance_question_type_candidate",
                "maintenance_question_type_candidate",
            ],
        )
        self.assertTrue(
            any("この問題だけを自動再準備します" in value for value in messages)
        )


    def test_policy_refresh_limit_blocks_only_current_question(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _sync, _server, parent = self._start_deferred_flow(
                Path(directory),
                SourceOnlyInventory(),
                ["question_type"],
            )
            question_id = parent["questionExecutions"][0]["questionId"]
            messages = []

            first = coordinator._requeue_policy_changed_question(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                messages.append,
            )
            second = coordinator._requeue_policy_changed_question(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                messages.append,
            )
            third = coordinator._requeue_policy_changed_question(
                "new-exam",
                parent["runId"],
                question_id,
                "question_type",
                messages.append,
            )
            stage = coordinator._queue_stage(
                coordinator.store.get("new-exam", parent["runId"]),
                question_id,
                "question_type",
            )

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertFalse(third)
        self.assertEqual(stage["status"], "blocked")
        self.assertEqual(stage["policyRefreshCount"], 2)
        self.assertTrue(any("他の問題は続行します" in value for value in messages))


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

            self.assertEqual(len(inventory.projected_calls), 4)
        self.assertEqual(len(set(projected_paths)), 2)
        self.assertTrue(
            all('"currentRecord":' in prompt for _question, prompt, _ in app_server.calls)
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
