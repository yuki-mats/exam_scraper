from tests.qualification_run_test_support import *  # noqa: F403


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


if __name__ == "__main__":
    unittest.main()  # noqa: F405
