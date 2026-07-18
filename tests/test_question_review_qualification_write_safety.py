from tests.qualification_run_test_support import *  # noqa: F403


class QualificationWriteSafetyReceiptTests(QualificationRunTestSupport):

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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
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
        self.assertEqual(run["policyVersions"], {"explanation": "2.2"})
        self.assertEqual(run["parallelWorkerLimit"], 1)
        self.assertEqual(run["writeWorkerLimit"], 1)

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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = SuccessfulAppServer(
                (
                    "output/sample/questions_json/2026/"
                    "21_explanationText_added/patch.json",
                ),
                temporary_helper=True,
            )
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
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
            job = self._wait_for_job(
                jobs,
                started["job"]["jobId"],
                timeout=2,
            )
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertIsNone(started["prompt"])
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["workType"], "maintenance")
        self.assertEqual(run["sandbox"], "workspace-write")
        self.assertEqual(run["threadId"], "thread-maintenance-1")
        self.assertEqual(run["sessionId"], "session-maintenance-1")
        self.assertEqual(run["turnId"], "turn-maintenance-1")
        self.assertEqual(run["model"], "gpt-test")
        self.assertIsNone(run["serviceTier"])
        self.assertEqual(run["reasoningEffort"], "high")
        self.assertEqual(run["parallelStrategy"], "read_only_research")
        self.assertEqual(run["parallelWorkerLimit"], 2)
        self.assertEqual(run["writeWorkerLimit"], 1)
        self.assertEqual(run["researchStatus"], "succeeded")
        self.assertEqual(run["researchThreadId"], "thread-research-1")
        self.assertEqual(run["researchSessionId"], "session-research-1")
        self.assertEqual(run["researchTurnId"], "turn-research-1")
        self.assertEqual(run["researchModel"], "gpt-research-test")
        self.assertEqual(run["researchSubagentCount"], 2)
        self.assertEqual(
            run["researchSubagentThreadIds"], ["subagent-1", "subagent-2"]
        )
        self.assertEqual(len(app_server.calls), 2)
        research_prompt, research_kwargs = app_server.calls[0]
        writer_prompt, writer_kwargs = app_server.calls[1]
        self.assertEqual(research_kwargs["work_type"], "maintenance_research")
        self.assertEqual(research_kwargs["sandbox"], "read-only")
        self.assertNotIn("writable_roots", research_kwargs)
        self.assertIn("read-only並列調査", research_prompt)
        self.assertNotIn("画面用の問題別進捗", research_prompt)
        self.assertNotIn("完了時に検証結果を次へJSONで保存", research_prompt)
        self.assertEqual(writer_kwargs["work_type"], "maintenance")
        self.assertEqual(writer_kwargs["sandbox"], "workspace-write")
        self.assertIn("問題IDごとの調査案", writer_prompt)
        self.assertIn(str((root / ".venv/bin/python").resolve()), writer_prompt)
        self.assertIn("成功ならpass、失敗ならfail", writer_prompt)
        self.assertIn("独自の代替検証だけで成功扱いにせず", writer_prompt)
        self.assertIn("result.jsonを最後のfile操作", writer_prompt)
        self.assertEqual(app_server.kwargs["work_type"], "maintenance")
        self.assertEqual(synchronizer.calls, [("sample", "2026", True)])
        self.assertEqual(run["artifactSync"]["status"], "succeeded")
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

    def test_success_receipt_can_finish_writer_before_turn_final_answer(self):
        job, run = self._run_receipt_completion(mutate_after_probe=False)

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["turnCompletionMode"], "receipt_interrupted")

    def test_success_receipt_survives_concurrent_manifest_refresh(self):
        job, run = self._run_receipt_completion(
            mutate_after_probe=False,
            clobber_manifest_after_probe=True,
        )

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["turnCompletionMode"], "receipt_interrupted")

    def test_change_after_success_receipt_is_not_accepted(self):
        job, run = self._run_receipt_completion(mutate_after_probe=True)

        self.assertEqual(job["status"], "failed")
        self.assertIn("成功receiptの保存後にfile変更", job["error"])
        self.assertEqual(run["status"], "failed")

    def test_failed_receipt_keeps_summary_and_first_failed_command(self):
        receipt = {
            "status": "failed",
            "summary": "工程別検証は成功したが、全体検証に失敗した。",
            "commands": [
                {"command": "python scoped_check.py", "status": "pass"},
                {"command": "python quality_gate.py", "status": "fail"},
                {"command": "python later_check.py", "status": "fail"},
            ],
            "changedFiles": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=SuccessfulAppServer(receipt=receipt),
            )
            snapshots = iter([{}, {}])
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            job = self._wait_for_job(
                jobs,
                started["job"]["jobId"],
                timeout=2,
            )
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertEqual(run["status"], "failed")
        self.assertIsNone(run["receiptError"])
        self.assertEqual(run["result"]["summary"], receipt["summary"])
        self.assertEqual(run["result"]["commands"], receipt["commands"])
        self.assertIn(receipt["summary"], run["error"])
        self.assertIn("python quality_gate.py", run["error"])
        self.assertNotIn("python later_check.py", run["error"])
        self.assertNotIn("有効な成功receipt", run["error"])

    def test_human_run_succeeds_with_warning_when_automatic_sync_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed_file = (
                "output/sample/questions_json/2026/"
                "21_explanationText_added/patch.json"
            )
            app_server = SuccessfulAppServer((changed_file,))
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            synchronizer.can_sync = False
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            snapshots = iter(
                [
                    {},
                    {Path(changed_file): "sha256:after"},
                ]
            )
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            job = self._wait_for_job(
                jobs,
                started["job"]["jobId"],
                timeout=2,
            )
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "succeeded")
        self.assertTrue(job["result"]["warning"])
        self.assertEqual(job["result"]["artifactSync"]["status"], "blocked")
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["artifactSync"]["status"], "blocked")
        self.assertEqual(synchronizer.calls, [])

    def test_change_notifications_allow_only_turn_workspace_or_repository(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as turn_directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            turn_workspace = Path(turn_directory)
            outside = Path(outside_directory)
            repository_file = root / "output/sample/questions_json/2026/patch.json"

            persistent = coordinator._repository_change_notifications(
                (
                    str(turn_workspace / "helper.py"),
                    "relative-helper.py",
                    str(repository_file),
                ),
                transient_root=turn_workspace,
            )

            self.assertEqual(
                persistent,
                ("output/sample/questions_json/2026/patch.json",),
            )
            with self.assertRaisesRegex(
                QualificationRunError, "repository外のfile変更"
            ):
                coordinator._repository_change_notifications(
                    (str(outside / "file.json"),),
                    transient_root=turn_workspace,
                )
            (turn_workspace / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                QualificationRunError, "repository外のfile変更"
            ):
                coordinator._repository_change_notifications(
                    ("escape/file.json",),
                    transient_root=turn_workspace,
                )

    def test_path_fingerprint_ignores_ctime_only_cloud_hydration(self):
        class StubPath:
            def __init__(self, *, size=10, mtime_ns=100, ctime_ns=200):
                self._stat = type(
                    "StubStat",
                    (),
                    {
                        "st_mode": 0o100644,
                        "st_size": size,
                        "st_mtime_ns": mtime_ns,
                        "st_ctime_ns": ctime_ns,
                    },
                )()

            def lstat(self):
                return self._stat

            def is_symlink(self):
                return False

        original = QualificationRunCoordinator._path_fingerprint(
            StubPath(ctime_ns=200)
        )
        hydrated = QualificationRunCoordinator._path_fingerprint(
            StubPath(ctime_ns=300)
        )
        modified = QualificationRunCoordinator._path_fingerprint(
            StubPath(mtime_ns=101, ctime_ns=300)
        )

        self.assertEqual(original, hydrated)
        self.assertNotEqual(original, modified)

    def test_version_recording_failure_never_marks_the_receipt_as_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=SuccessfulAppServer(),
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            def fail_version_recording(_run):
                raise QualificationRunError("version recording failed")

            coordinator._record_work_versions = fail_version_recording
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            job = self._wait_for_job(
                jobs,
                started["job"]["jobId"],
                timeout=2,
            )
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertEqual(run["status"], "failed")
        self.assertFalse(run["receiptValidated"])
        self.assertIsNone(run["workVersionReceipt"])
        self.assertIsNotNone(run["finishedAt"])

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

    def test_success_receipt_cannot_choose_failed_delta_resolution(self):
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

            with self.assertRaisesRegex(
                QualificationRunError, "serverが確定"
            ):
                coordinator._validate_changed_files(
                    "sample", "run-1", run, (), ()
                )

    def test_failed_turn_still_records_the_actual_repository_diff(self):
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
            job = self._wait_for_job(
                jobs,
                started["job"]["jobId"],
                timeout=2,
            )
            run = coordinator.store.refresh("sample", started["run"]["runId"])
            baseline = json.loads(
                (root / run["baselinePath"]).read_text(encoding="utf-8")
            )

        self.assertEqual(job["status"], "failed")
        self.assertIn("turn crashed", job["error"])
        self.assertIn(str(changed_path), job["error"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["result"]["changedFiles"], [])
        self.assertEqual(run["rollback"]["status"], "succeeded")
        self.assertEqual(run["threadId"], "thread-failed-1")
        self.assertEqual(run["turnId"], "turn-failed-1")
        self.assertIn(
            "output/question_review_console/sample/2026/work_versions.json",
            baseline["writeTransaction"]["roots"],
        )

    def test_failed_turn_excludes_unnotified_external_and_progress_files(self):
        unsafe_path = Path("tools/question_review_console/unsafe.json")
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
            snapshot_count = 0

            def snapshots(qualification, run_id):
                nonlocal snapshot_count
                snapshot_count += 1
                if snapshot_count == 1:
                    return {}
                progress_path = Path(
                    "output",
                    "question_review_console",
                    "workflow_runs",
                    qualification,
                    run_id,
                    "agent_output",
                    "progress.jsonl",
                )
                return {
                    progress_path: "sha256:progress",
                    unsafe_path: "sha256:unsafe",
                }

            coordinator._repository_file_fingerprints = snapshots
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            job = self._wait_for_job(
                jobs,
                started["job"]["jobId"],
                timeout=2,
            )
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertIn("turn crashed", job["error"])
        self.assertEqual(run["result"]["changedFiles"], [])
        self.assertEqual(
            run["externalConcurrentChangedFiles"],
            [str(unsafe_path)],
        )


if __name__ == "__main__":
    unittest.main()  # noqa: F405
