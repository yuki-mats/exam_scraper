from tests.qualification_run_test_support import *  # noqa: F403


class QualificationLawVersionTests(QualificationRunTestSupport):

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
                "policyVersions": {
                    "question_type": policy["policyVersion"],
                    "question_intent": workflow.versioned_policies("new-exam")[
                        "question_intent"
                    ]["policyVersion"],
                },
                "policyFingerprints": {
                    "question_type": policy["policyFingerprint"],
                    "question_intent": workflow.versioned_policies("new-exam")[
                        "question_intent"
                    ]["policyFingerprint"],
                },
                "policyTargets": {
                    "question_type": ["new-exam-2026-q1"],
                    "question_intent": [],
                },
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

    def test_version_recording_uses_exact_binding_not_shared_legacy_alias(self):
        class SharedIdentityInventory(SourceOnlyInventory):
            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                first = group["questions"][0]
                first.update(
                    {
                        "id": "ui-q1",
                        "reviewKey": "review-key-q1",
                        "originalQuestionId": "shared-review-id",
                        "sourceQuestionKey": "new-exam:2026:shared",
                        "sourceRecordRef": "question_2026_1.json#0",
                    }
                )
                second = json.loads(json.dumps(first))
                second.update(
                    {
                        "id": "ui-q2",
                        "reviewKey": "review-key-q2",
                        "sourceRecordRef": "question_2026_2.json#0",
                    }
                )
                group["questions"] = [first, second]
                return group

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = SharedIdentityInventory()
            workflow = QualificationWorkflow(root, inventory)
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            policy = workflow.versioned_policies("new-exam")["question_type"]
            run = {
                "runId": "run-exact-q2",
                "qualification": "new-exam",
                "targetGroupIds": ["2026"],
                "policyVersions": {
                    "question_type": policy["policyVersion"]
                },
                "policyFingerprints": {
                    "question_type": policy["policyFingerprint"]
                },
                "policyTargets": {"question_type": ["ui-q2"]},
                "targetRecordBindings": [
                    {
                        "uiQuestionId": "ui-q2",
                        "reviewQuestionId": "shared-review-id",
                        "sourceQuestionKey": "new-exam:2026:shared",
                        "sourceRecordRef": "question_2026_2.json#0",
                        "aliases": [
                            "shared-review-id",
                            "new-exam:2026:shared",
                        ],
                    }
                ],
            }

            receipt = coordinator._record_work_versions(run)
            questions = inventory.group("new-exam", "2026")["questions"]
            statuses = [
                workflow.work_versions.status_for(question, [policy])["status"]
                for question in questions
            ]

        self.assertEqual(receipt["recordedCount"], 1)
        self.assertEqual(statuses, ["unrecorded", "current"])

    def test_version_recording_rejects_ambiguous_legacy_alias(self):
        class SharedAliasInventory(SourceOnlyInventory):
            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                first = group["questions"][0]
                first.update(
                    {
                        "id": "ui-q1",
                        "reviewKey": "review-key-q1",
                        "sourceQuestionKey": "shared-source-key",
                    }
                )
                second = json.loads(json.dumps(first))
                second.update(
                    {
                        "id": "ui-q2",
                        "reviewKey": "review-key-q2",
                        "sourceRecordRef": "question_2026_2.json#0",
                    }
                )
                group["questions"] = [first, second]
                return group

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = SharedAliasInventory()
            workflow = QualificationWorkflow(root, inventory)
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            policy = workflow.versioned_policies("new-exam")["question_type"]

            with self.assertRaisesRegex(
                QualificationRunError,
                "一意に解決できません",
            ):
                coordinator._record_work_versions(
                    {
                        "runId": "run-legacy-shared",
                        "qualification": "new-exam",
                        "targetGroupIds": ["2026"],
                        "policyVersions": {
                            "question_type": policy["policyVersion"]
                        },
                        "policyFingerprints": {
                            "question_type": policy["policyFingerprint"]
                        },
                        "policyTargets": {
                            "question_type": ["shared-source-key"]
                        },
                    }
                )

    def test_explanation_version_recording_rejects_old_legal_style(self):
        with self.assertRaisesRegex(
            QualificationRunError, "03 解説の日本語品質検証"
        ):
            QualificationRunCoordinator._validate_explanation_quality(
                [
                    {
                        "originalQuestionId": "q1",
                        "projected": {
                            "explanationText": [
                                "正しい。ガス事業法第2条第1項は、"
                                "小売供給を定義している。"
                            ]
                        },
                    }
                ]
            )

    def test_explanation_version_recording_rejects_missing_or_opposite_prefix(self):
        for explanation in (
            "定義に一致するため正しい。",
            "間違い。定義に一致する。",
            "正しい。A",
        ):
            with self.subTest(explanation=explanation), self.assertRaisesRegex(
                QualificationRunError, "03 解説の日本語品質検証"
            ):
                QualificationRunCoordinator._validate_explanation_quality(
                    [
                        {
                            "originalQuestionId": "q1",
                            "projected": {
                                "choiceTextList": ["A"],
                                "correctChoiceText": ["正しい"],
                                "explanationText": [explanation],
                            },
                        }
                    ]
                )

    def test_law_audit_version_recording_also_rejects_bad_explanation_format(self):
        class BadLawExplanationInventory(LawSourceInventory):
            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                group["questions"][0]["projected"]["explanationText"] = [
                    "定義に一致するため正しい。"
                ]
                return group

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(
                root, BadLawExplanationInventory()
            )
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            with self.assertRaisesRegex(
                QualificationRunError, "03 解説の日本語品質検証"
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(workflow)
                )

    def test_law_audit_plan_preserves_ui_and_source_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory), LawSourceInventory()
            )

            plan = workflow.plan(
                "new-exam", "law_audit", "group_refresh",
                list_group_id="2026",
            )

        self.assertEqual(
            plan["targetRecordBindings"],
            [
                {
                    "uiQuestionId": "new-exam-2026-q1",
                    "reviewQuestionId": "new-exam-2026-q1",
                    "sourceQuestionKey": "new-exam:2026:q1",
                    "sourceRecordRef": "question_2026_1.json#0",
                    "aliases": plan["targetRecordBindings"][0]["aliases"],
                }
            ],
        )

    def test_law_audit_plan_rejects_missing_source_identity(self):
        class MissingSourceIdentityInventory(LawSourceInventory):
            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                group["questions"][0]["sourceQuestionKey"] = ""
                return group

        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory), MissingSourceIdentityInventory()
            )

            with self.assertRaisesRegex(ValueError, "source由来"):
                workflow.plan(
                    "new-exam", "law_audit", "group_refresh",
                    list_group_id="2026",
                )

    def test_law_audit_plan_rejects_duplicate_source_identity_pair(self):
        class DuplicateSourceIdentityInventory(LawSourceInventory):
            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                duplicate = json.loads(json.dumps(group["questions"][0]))
                duplicate["id"] = "different-ui-id"
                duplicate["reviewKey"] = "different-review-key"
                group["questions"].append(duplicate)
                return group

        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(
                Path(directory), DuplicateSourceIdentityInventory()
            )

            with self.assertRaisesRegex(ValueError, "組が重複"):
                workflow.plan(
                    "new-exam", "law_audit", "group_refresh",
                    list_group_id="2026",
                )

    def test_law_audit_plan_blocks_group_identity_issue_without_hiding_group(self):
        class IdentityBlockedInventory(LawSourceInventory):
            def group(self, qualification, list_group_id):
                group = super().group(qualification, list_group_id)
                group["identityBlockers"] = [
                    {
                        "code": "source_identity_pair_duplicate",
                        "message": "source identity pair duplicate",
                    }
                ]
                return group

        inventory = IdentityBlockedInventory()
        visible_group = inventory.group("new-exam", "2026")
        self.assertEqual(len(visible_group["questions"]), 1)

        with tempfile.TemporaryDirectory() as directory:
            workflow = QualificationWorkflow(Path(directory), inventory)
            with self.assertRaisesRegex(ValueError, "source identity"):
                workflow.plan(
                    "new-exam", "law_audit", "group_refresh",
                    list_group_id="2026",
                )

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

    def test_law_audit_quality_accepts_explicitly_non_law_question(self):
        question = {
            "id": "non-law-question",
            "questionLabel": "問1",
            "isLawRelated": False,
            "issueCodes": [],
            "projected": {"isLawRelated": False},
        }

        QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_quality_rejects_unpublished_law_evidence(self):
        question = {
            "id": "law-question",
            "questionLabel": "問2",
            "isLawRelated": True,
            "issueCodes": [],
            "projected": {
                "isLawRelated": True,
                "lawRevisionFacts": [{"auditStatus": "same_as_current"}],
                "lawReferences": [
                    {"lawTitle": "ガス事業法", "article": "第2条"}
                ],
                "explanationText": ["正しい。定義に該当する。"],
                "suggestedQuestions": ["この内容はどうなっていますか？"],
                "suggestedQuestionDetails": [
                    {"answer": "対象となる事業を定めたものです。"}
                ],
            },
        }

        with self.assertRaisesRegex(
            QualificationRunError,
            "concrete law evidence anchor",
        ):
            QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_quality_accepts_published_law_evidence(self):
        question = {
            "id": "law-question",
            "questionLabel": "問2",
            "isLawRelated": True,
            "issueCodes": [],
            "projected": {
                "isLawRelated": True,
                "correctChoiceText": ["正しい"],
                "lawRevisionFacts": [
                    {
                        "auditStatus": "same_as_current",
                        "current": {"correctChoiceText": "正しい"},
                        "evidenceSummary": {"verdict": "correct"},
                    }
                ],
                "lawReferences": [
                    {"lawTitle": "ガス事業法", "article": "第2条"}
                ],
                "explanationText": [
                    "正しい。ガス事業法第2条の定義に該当する。"
                ],
                "suggestedQuestions": [
                    "現行法のガス事業法第2条は何を定義していますか？"
                ],
                "suggestedQuestionDetails": [
                    {"answer": "ガス事業法第2条が対象事業を定義しています。"}
                ],
            },
        }

        QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_quality_uses_projected_metadata_before_artifact_sync(self):
        question = LawSourceInventory().group("new-exam", "2026")["questions"][0]
        question["issueCodes"] = [
            "law_audit_metadata_incomplete",
            "law_audit_verdict_mismatch",
            "law_hold",
            "law_basis_missing",
        ]

        QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_quality_does_not_require_law_verdicts_for_non_law_question(self):
        question = NonLawSourceInventory().group("new-exam", "2026")[
            "questions"
        ][0]
        question["projected"].update(
            {
                "correctChoiceText": ["正しい"],
                "lawRevisionFacts": {
                    "auditStatus": "not_law_related",
                    "reviewState": "secondary_verified",
                },
            }
        )

        QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_version_rejects_sidecar_classification_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, NonLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": True,
                        "auditStatus": "hold",
                        "reviewState": "needs_secondary_review",
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "sidecar整合.*isLawRelated",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(workflow)
                )
            question = workflow.inventory.group("new-exam", "2026")[
                "questions"
            ][0]
            status = workflow.work_versions.status_for(
                question,
                [workflow.versioned_policies("new-exam")["law_audit"]],
            )

        self.assertEqual(status["stages"][0]["status"], "unrecorded")

    def test_law_audit_version_records_matching_non_law_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, NonLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )

            receipt = coordinator._record_work_versions(
                self._law_audit_policy_run(workflow)
            )
            question = workflow.inventory.group("new-exam", "2026")[
                "questions"
            ][0]
            status = workflow.work_versions.status_for(
                question,
                [workflow.versioned_policies("new-exam")["law_audit"]],
            )

        self.assertEqual(receipt["recordedCount"], 1)
        self.assertEqual(status["stages"][0]["status"], "current")

    def test_law_audit_version_rejects_missing_required_sidecar_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, NonLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "auditMethodVersion": "",
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "auditMethodVersion",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(workflow)
                )

    def test_law_sidecar_pair_join_allows_shared_source_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = NonLawSourceInventory()
            questions = inventory.group("new-exam", "2026")["questions"]
            first = questions[0]
            first["sourceQuestionKey"] = "new-exam:2026:shared"
            first["sourceRecordRef"] = "question_2026_1.json#0"
            second = json.loads(json.dumps(first))
            second.update(
                {
                    "id": "new-exam-2026-q2",
                    "reviewKey": "new-exam:2026:q2",
                    "originalQuestionId": "new-exam-2026-q2",
                    "sourceRecordRef": "question_2026_2.json#0",
                }
            )
            questions.append(second)
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": review_id,
                        "sourceQuestionKey": "new-exam:2026:shared",
                        "sourceRecordRef": (
                            "question_2026_1.json#0"
                            if review_id.endswith("q1")
                            else "question_2026_2.json#0"
                        ),
                    }
                    for review_id in (
                        "new-exam-2026-q1",
                        "new-exam-2026-q2",
                    )
                ],
            )
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, inventory),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            coordinator._validate_law_audit_sidecar_consistency(
                "new-exam",
                questions,
            )

    def test_law_sidecar_v2_join_preserves_existing_review_id_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = NonLawSourceInventory()
            questions = inventory.group("new-exam", "2026")["questions"]
            questions[0]["id"] = "legacy-ui-review-id"
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "legacy-ui-review-id",
                        "sourceQuestionKey": "new-exam:2026:q1",
                        "sourceRecordRef": "question_2026_1.json#0",
                    }
                ],
            )
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, inventory),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            coordinator._validate_law_audit_sidecar_consistency(
                "new-exam",
                questions,
            )

    def test_law_audit_version_rejects_missing_or_duplicate_sidecar_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, NonLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = self._law_audit_policy_run(workflow)

            with self.assertRaisesRegex(
                QualificationRunError,
                "監査sidecarがありません",
            ):
                coordinator._record_work_versions(run)

            row = {
                "reviewQuestionId": "new-exam-2026-q1",
                "isLawRelated": False,
                "auditStatus": "not_law_related",
                "reviewState": "secondary_verified",
            }
            self._write_law_audit_sidecar(root, "2026", [row, row])
            with self.assertRaisesRegex(
                QualificationRunError,
                "対応行が2件",
            ):
                coordinator._record_work_versions(run)

            self._write_law_audit_sidecar(
                root,
                "2026",
                [{**row, "sourceSummary": {"text": "文字列ではない"}}],
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "監査sidecar.sourceSummaryがありません",
            ):
                coordinator._record_work_versions(run)

    def test_law_audit_version_validates_nested_verified_basis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            row = {
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
            self._write_law_audit_sidecar(root, "2026", [row])

            receipt = coordinator._record_work_versions(
                self._law_audit_policy_run(workflow)
            )

        self.assertEqual(receipt["recordedCount"], 1)

    def test_law_audit_version_rejects_unverified_projected_basis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(
                root,
                UnverifiedLawSourceInventory(),
            )
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
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

            with self.assertRaisesRegex(
                QualificationRunError,
                "projected lawReferencesにverified",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(workflow)
                )

    def test_law_audit_version_rejects_different_verified_basis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
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
                                    "lawTitle": "消防法",
                                    "lawId": "323AC1000000186",
                                    "article": "3",
                                    "verificationStatus": "verified",
                                }
                            ]
                        ],
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "verified法令根拠が一致しません",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(workflow)
                )

    def test_law_audit_sidecar_rejects_unpublished_projected_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = LawSourceInventory().group("new-exam", "2026")[
                "questions"
            ][0]
            fact = question["projected"]["lawRevisionFacts"][0]
            fact["auditStatus"] = "hold"
            fact["reviewState"] = "needs_secondary_review"
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, LawSourceInventory()),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
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

            with self.assertRaisesRegex(
                QualificationRunError,
                "projected lawRevisionFactsが公開確定状態ではありません",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [question],
                )

    def test_non_law_sidecar_rejects_stale_hold_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = NonLawSourceInventory().group("new-exam", "2026")[
                "questions"
            ][0]
            question["projected"]["lawRevisionFacts"] = [
                {
                    "auditStatus": "hold",
                    "reviewState": "needs_secondary_review",
                }
            ]
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, NonLawSourceInventory()),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "非法令問題のprojected lawRevisionFacts",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [question],
                )

    def test_non_law_sidecar_rejects_flag_or_stale_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = NonLawSourceInventory().group("new-exam", "2026")[
                "questions"
            ][0]
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, NonLawSourceInventory()),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                        "lawReferences": [],
                    }
                ],
            )

            question["projected"]["lawGroundedExplanationNotNeeded"] = False
            with self.assertRaisesRegex(
                QualificationRunError,
                "lawGroundedExplanationNotNeededがtrueではありません",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [question],
                )

            question["projected"]["lawGroundedExplanationNotNeeded"] = True
            question["projected"]["lawReferences"] = [
                {
                    "lawTitle": "ガス事業法",
                    "lawId": "329AC0000000051",
                    "article": "2",
                    "verificationStatus": "verified",
                }
            ]
            with self.assertRaisesRegex(
                QualificationRunError,
                "非法令問題のprojected lawReferencesが空ではありません",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [question],
                )

    def test_law_audit_rejects_scalar_law_revision_facts(self):
        law_question = LawSourceInventory().group("new-exam", "2026")[
            "questions"
        ][0]
        law_question["projected"]["lawRevisionFacts"] = "invalid"
        with self.assertRaisesRegex(
            QualificationRunError,
            "lawRevisionFactsを確認できません",
        ):
            QualificationRunCoordinator._validate_law_audit_quality(
                [law_question]
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            non_law_question = NonLawSourceInventory().group(
                "new-exam",
                "2026",
            )["questions"][0]
            non_law_question["projected"]["lawRevisionFacts"] = "invalid"
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, NonLawSourceInventory()),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "lawRevisionFactsの型が不正",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [non_law_question],
                )

    def test_law_audit_version_is_atomic_when_one_group_sidecar_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = MultiGroupNonLawSourceInventory()
            workflow = QualificationWorkflow(root, inventory)
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2025",
                [
                    {
                        "reviewQuestionId": "new-exam-2025-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "2026_law_revision_audit.jsonl: 監査sidecarがありません",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(
                        workflow,
                        list_group_ids=["2025", "2026"],
                    )
                )
            statuses = [
                workflow.work_versions.status_for(
                    inventory.group("new-exam", group)["questions"][0],
                    [workflow.versioned_policies("new-exam")["law_audit"]],
                )["stages"][0]["status"]
                for group in ("2025", "2026")
            ]

        self.assertEqual(statuses, ["unrecorded", "unrecorded"])

    def test_law_audit_version_is_not_recorded_while_quality_warning_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, IncompleteLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = coordinator._plan(
                "new-exam", "law_audit", "outdated", None
            )
            plan["runId"] = "law-audit-run"

            with self.assertRaisesRegex(
                QualificationRunError, "03b 現行法監査の必須メタデータ"
            ):
                coordinator._record_work_versions(plan)
            status = workflow.work_versions.status_for(
                IncompleteLawSourceInventory().group("new-exam", "2026")[
                    "questions"
                ][0],
                [workflow.versioned_policies("new-exam")["law_audit"]],
            )

        self.assertEqual(status["stages"][0]["status"], "unrecorded")


if __name__ == "__main__":
    unittest.main()  # noqa: F405
