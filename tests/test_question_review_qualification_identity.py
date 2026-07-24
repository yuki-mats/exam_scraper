from tests.qualification_run_test_support import *  # noqa: F403


class QualificationIdentityContractTests(QualificationRunTestSupport):

    def test_qualification_law_audit_preserves_trusted_sources_and_record_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
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
                    "targetRecordBindings": [
                        {
                            "uiQuestionId": "ui-q1",
                            "reviewQuestionId": "q1",
                            "sourceQuestionKey": "sample:2026:q1",
                            "sourceRecordRef": "q1.json#0",
                            "aliases": ["q1"],
                        },
                        {
                            "uiQuestionId": "ui-q2",
                            "reviewQuestionId": "q2",
                            "sourceQuestionKey": "sample:2026:q2",
                            "sourceRecordRef": "q2.json#0",
                            "aliases": ["q2"],
                        },
                    ],
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
        self.assertEqual(run["parallelWorkerLimit"], 1)
        self.assertEqual(run["targetRecordAliases"], ["q1", "q2"])
        self.assertEqual(run["targetCount"], 2)
        self.assertEqual(run["policyVersions"], {"law_audit": "4.0"})
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

    def test_supplied_binding_prefers_source_ref_over_shared_two_field_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            source_files = [
                f"output/sample/questions_json/2026/00_source/q{number}.json"
                for number in (1, 2)
            ]
            alias_groups = [
                ["shared-review-id", "sample:2026:shared", f"q{number}.json#0"]
                for number in (1, 2)
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
                    "reviewId": "review-shared",
                    "prompt": "law audit",
                    "requestKind": "qualification_law_audit",
                    "investigationScope": "qualification",
                    "issueTypes": ["law_audit_metadata_incomplete"],
                    "targetSourceFiles": source_files,
                    "targetRecordAliasGroups": alias_groups,
                    "targetRecordBindings": [
                        {
                            "uiQuestionId": f"ui-q{number}",
                            "reviewQuestionId": "shared-review-id",
                            "sourceQuestionKey": "sample:2026:shared",
                            "sourceRecordRef": f"q{number}.json#0",
                            "aliases": aliases,
                        }
                        for number, aliases in zip((1, 2), alias_groups)
                    ],
                    "targetSourceRecordScopes": {
                        source: [aliases]
                        for source, aliases in zip(source_files, alias_groups)
                    },
                },
                work_type="maintenance",
            )
            run = coordinator.store.get(
                "sample", started["run"]["runId"]
            )

        self.assertEqual(
            [
                binding["sourceRecordRef"]
                for binding in run["targetRecordBindings"]
            ],
            ["q1.json#0", "q2.json#0"],
        )
        self.assertEqual(
            run["policyTargets"]["law_audit"],
            ["ui-q1", "ui-q2"],
        )

    def test_current_question_law_hold_scopes_every_allowed_record_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
                    "sourceQuestionKey": "sample:2026:q1",
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
                    "prompt": "law hold review",
                    "investigationScope": "current_question",
                    "issueTypes": ["law_hold"],
                    "fields": ["lawReferences", "lawRevisionFacts"],
                },
                work_type="maintenance",
            )
            run = coordinator.store.get(
                "sample", started["run"]["runId"]
            )

        expected_record_files = {
            path
            for path in [*run["allowedPatchFiles"], *run["allowedWriteFiles"]]
            if Path(path).suffix in {".json", ".jsonl"}
        }
        self.assertEqual(set(run["targetRecordScopes"]), expected_record_files)
        self.assertTrue(
            any(
                "_lawRevision_needs_5_5_high_review" in path
                for path in run["targetRecordScopes"]
            )
        )
        self.assertTrue(
            any("21_explanationText_added" in path for path in run["allowedPatchFiles"])
        )

    def test_post_fix_law_fields_preserve_law_audit_contract_and_failed_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            question = {
                "id": "question-1",
                "qualification": "sample",
                "listGroupId": "2026",
                "stateHash": "state-1",
                "originalQuestionId": "original-1",
                "sourceQuestionKey": "sample:2026:q1",
                "paths": {
                    "source": (
                        "output/sample/questions_json/2026/"
                        "00_source/question_2026_1.json"
                    ),
                    "patches": [],
                },
            }
            failed = coordinator.start_review(
                question,
                {
                    "reviewId": "review-failed",
                    "prompt": "law hold review",
                    "investigationScope": "current_question",
                    "issueTypes": ["law_hold"],
                    "fields": ["lawReferences", "lawRevisionFacts"],
                },
                work_type="maintenance",
            )["run"]
            failed_path = next(
                path
                for path in failed["allowedWriteFiles"]
                if path.endswith("_law_revision_audit.jsonl")
            )
            coordinator.store.update(
                "sample",
                failed["runId"],
                status="failed",
                result={
                    "status": "failed",
                    "changedFiles": [failed_path],
                    "resolvedFailedDeltaPaths": [],
                },
            )

            retried = coordinator.start_review(
                question,
                {
                    "reviewId": "review-retry",
                    "prompt": "post-fix law review",
                    "investigationScope": "current_question",
                    "issueTypes": ["post_fix_review"],
                    "fields": ["lawReferences", "lawRevisionFacts"],
                },
                work_type="maintenance",
            )["run"]

        self.assertEqual(retried["policyVersions"], {"law_audit": "4.0"})
        self.assertEqual(retried["allowedWriteAreas"], ["review"])
        self.assertIn(failed_path, retried["allowedWriteFiles"])
        self.assertIn(failed_path, retried["resolvableFailedDeltaPaths"])
        self.assertTrue(
            any(
                "_lawRevision_needs_5_5_high_review" in path
                for path in retried["allowedPatchFiles"]
            )
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

    def test_category_setup_cannot_complete_without_valid_category(self):
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
            run = {
                "qualification": "new-exam",
                "stageId": "category_setup",
                "stageIds": ["category_setup"],
                "targetGroupIds": ["2026"],
                "policyVersions": {},
            }

            with self.assertRaisesRegex(
                QualificationRunError,
                "category.json",
            ):
                coordinator._record_work_versions(run)

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

    def test_qualification_plan_exposes_only_resolvable_failed_delta_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            planned = (
                "output/sample/questions_json/2026/"
                "21_explanationText_added/patch.json"
            )
            unplanned = (
                "output/sample/questions_json/2026/"
                "18_law_context_prepared/law.json"
            )
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "failed",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "policyVersions": {"explanation": "1.0"},
                        "targetGroupIds": ["2026"],
                        "allowedPatchDirs": [
                            "18_law_context_prepared",
                            "21_explanationText_added",
                        ],
                        "allowedWriteAreas": [],
                        "allowedPatchFiles": [planned, unplanned],
                        "allowedWriteFiles": [],
                        "targetRecordScopes": {
                            planned: [["q1"]],
                            unplanned: [["q1"]],
                        },
                        "result": {"changedFiles": [planned, unplanned]},
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
            )
            plan = {
                "qualification": "sample",
                "workType": "maintenance",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "policyVersions": {"explanation": "1.0"},
                "targetGroupIds": ["2026"],
                "allowedPatchDirs": ["21_explanationText_added"],
                "allowedWriteAreas": [],
                "allowedPatchFiles": [planned],
                "allowedWriteFiles": [],
                "targetRecordScopes": {planned: [["q1"]]},
            }
            plan["resolvableFailedDeltaPaths"] = (
                coordinator._resolvable_for_plan(
                    "sample",
                    ["2026"],
                    plan,
                )
            )

            self.assertEqual(plan["resolvableFailedDeltaPaths"], [planned])
            run = {
                **plan,
                "result": {
                    "changedFiles": [],
                    "resolvedFailedDeltaPaths": [],
                },
            }
            coordinator._validate_changed_files(
                "sample", "run-1", run, (), ()
            )

    def test_write_transaction_prefers_exact_files_to_whole_patch_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_root = (
                root
                / "output/sample/questions_json/2026/"
                "21_explanationText_added"
            )
            agent_output = (
                root
                / "output/question_review_console/workflow_runs/sample/"
                "run-1/agent_output"
            )
            patch_root.mkdir(parents=True)
            agent_output.mkdir(parents=True)
            exact = patch_root / "target.json"
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            selected = coordinator._maintenance_transaction_roots(
                {
                    "allowedPatchFiles": [
                        exact.relative_to(root).as_posix()
                    ],
                    "allowedWriteFiles": [],
                },
                (patch_root, agent_output),
            )

        self.assertIn(exact.resolve(), selected)
        self.assertIn(agent_output.resolve(), selected)
        self.assertNotIn(patch_root.resolve(), selected)

    def test_successful_run_records_failed_delta_resolution_server_side(self):
        class FailedDeltaWorkflow(FakeWorkflow):
            def plan(self, qualification, stage_id, mode="remaining"):
                plan = super().plan(qualification, stage_id, mode)
                source = (
                    "output/sample/questions_json/2026/00_source/"
                    "question_2026_1.json"
                )
                plan.update(
                    {
                        "sourceFiles": [source],
                        "outputFiles": [
                            "output/sample/questions_json/2026/"
                            "21_explanationText_added/"
                            "question_2026_1_merged_explanationText_added.json"
                        ],
                        "targetQuestionKeys": ["q1"],
                        "targetRecordAliasGroups": [["q1"]],
                        "targetSourceRecordScopes": {source: [["q1"]]},
                    }
                )
                return plan

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FailedDeltaWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=SuccessfulAppServer(),
            )
            resolver = coordinator._plan(
                "sample", "law_audit", "remaining", None
            )
            planned = next(
                path
                for path in resolver["allowedPatchFiles"]
                if "/21_explanationText_added/" in path
            )
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample/"
                "0000-failed-run/manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "failed",
                        "workType": "maintenance",
                        "stageId": "law_audit",
                        "stageIds": ["law_audit"],
                        "targetGroupIds": ["2026"],
                        **{
                            field: resolver[field]
                            for field in (
                                "allowedPatchDirs",
                                "allowedWriteAreas",
                                "allowedPatchFiles",
                                "allowedWriteFiles",
                                "targetRecordScopes",
                            )
                        },
                        "result": {
                            "status": "failed",
                            "changedFiles": [planned],
                            "resolvedFailedDeltaPaths": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            preview = coordinator.preview(
                "sample", "law_audit", "remaining"
            )
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            start_resolvable = started["run"]["resolvableFailedDeltaPaths"]
            job = self._wait_for_job(
                jobs,
                started["job"]["jobId"],
                timeout=2,
            )
            run = coordinator.store.refresh(
                "sample", started["run"]["runId"]
            )
            unresolved = unresolved_failed_delta_paths(root, "sample")

        self.assertEqual(job["status"], "succeeded", job)
        self.assertIn(planned, start_resolvable)
        self.assertEqual(
            run["result"]["resolvedFailedDeltaPaths"], [planned]
        )
        self.assertEqual(unresolved, ())


if __name__ == "__main__":
    unittest.main()  # noqa: F405
