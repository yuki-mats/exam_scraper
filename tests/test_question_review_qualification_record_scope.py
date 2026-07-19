from tests.qualification_run_test_support import *  # noqa: F403


class QualificationRecordScopeTests(QualificationRunTestSupport):
    def _validate_record_scope_change(
        self,
        relative,
        before,
        after,
        *,
        plan_updates,
        source_payloads=None,
        stage_id="law_audit",
    ):
        def write_payload(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            text = payload if isinstance(payload, str) else json.dumps(payload)
            path.write_text(text, encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / relative
            for source_relative, payload in (source_payloads or {}).items():
                write_payload(root / source_relative, payload)
            if before is not None:
                write_payload(path, before)

            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", stage_id, "remaining")
            plan.update(plan_updates)
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (path.parent, (root / run["resultReceiptPath"]).parent),
            )
            write_payload(path, after)
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

    def _validate_multi_row_law_sidecar(
        self,
        non_target_before,
        non_target_after,
    ):
        relative = Path(
            "output/sample/review/law_revision_audit/"
            "2026_law_revision_audit.jsonl"
        )
        source_relative = Path(
            "output/sample/questions_json/2026/00_source/q1.json"
        )
        aliases = [
            "ui-hash-q1",
            "source-review-q1",
            "sample:2026:q1",
            "q1.json#0",
        ]

        def jsonl(*rows):
            return "".join(json.dumps(row) + "\n" for row in rows)

        self._validate_record_scope_change(
            relative,
            jsonl(
                {
                    "schemaVersion": "law-revision-audit/v1",
                    "reviewQuestionId": "ui-hash-q1",
                },
                non_target_before,
            ),
            jsonl(
                {
                    "schemaVersion": "law-revision-audit/v2",
                    "reviewQuestionId": "ui-hash-q1",
                    "sourceQuestionKey": "sample:2026:q1",
                    "sourceRecordRef": "q1.json#0",
                },
                non_target_after,
            ),
            plan_updates={
                "sourceFiles": [source_relative.as_posix()],
                "targetRecordAliasGroups": [aliases],
                "targetRecordBindings": [
                    {
                        "uiQuestionId": "ui-hash-q1",
                        "reviewQuestionId": "source-review-q1",
                        "sourceQuestionKey": "sample:2026:q1",
                        "sourceRecordRef": "q1.json#0",
                        "aliases": aliases,
                    }
                ],
                "allowedPatchDirs": [],
                "allowedPatchFiles": [],
                "allowedWriteFiles": [relative.as_posix()],
                "targetRecordScopes": {relative.as_posix(): [aliases]},
            },
            source_payloads={
                source_relative: {
                    "question_bodies": [
                        {
                            "originalQuestionId": "source-review-q1",
                            "sourceQuestionKey": "sample:2026:q1",
                        }
                    ]
                }
            },
        )

    def test_record_scope_rejects_a_different_question_in_aggregate_json(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/aggregate.json"
        )
        before = {
            "question_bodies": [
                {"original_question_id": "q1", "value": 1},
                {"original_question_id": "q2", "value": 1},
            ]
        }
        after = json.loads(json.dumps(before))
        after["question_bodies"][1]["value"] = 2

        with self.assertRaisesRegex(QualificationRunError, "対象問題以外"):
            self._validate_record_scope_change(
                relative,
                before,
                after,
                plan_updates={
                    "stageIds": ["law_audit"],
                    "targetRecordAliases": ["q1"],
                    "allowedPatchDirs": ["21_explanationText_added"],
                    "allowedPatchFiles": [relative.as_posix()],
                    "targetRecordScopes": {relative.as_posix(): [["q1"]]},
                },
            )

    def test_record_scope_allows_only_the_target_record_to_change(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/aggregate.json"
        )
        before = {
            "question_bodies": [
                {"original_question_id": "q1", "value": 1},
                {"original_question_id": "q2", "value": 1},
            ]
        }
        after = json.loads(json.dumps(before))
        after["question_bodies"][0]["value"] = 2

        self._validate_record_scope_change(
            relative,
            before,
            after,
            plan_updates={
                "stageIds": ["law_audit"],
                "targetRecordAliases": ["q1"],
                "allowedPatchDirs": ["21_explanationText_added"],
                "allowedPatchFiles": [relative.as_posix()],
                "targetRecordScopes": {relative.as_posix(): [["q1"]]},
            },
        )

    def test_record_scope_rejects_target_record_deletion(self):
        relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/aggregate.json"
        )
        before = {
            "question_bodies": [
                {"originalQuestionId": "q1", "value": 1},
                {"originalQuestionId": "q2", "value": 1},
            ]
        }
        after = {
            "question_bodies": [{"originalQuestionId": "q2", "value": 1}]
        }

        with self.assertRaisesRegex(QualificationRunError, "record削除"):
            self._validate_record_scope_change(
                relative,
                before,
                after,
                plan_updates={
                    "targetRecordAliasGroups": [["q1"]],
                    "allowedPatchDirs": ["21_explanationText_added"],
                    "allowedPatchFiles": [relative.as_posix()],
                    "targetRecordScopes": {relative.as_posix(): [["q1"]]},
                },
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
                        "original_question_id": "q1",
                        "public_question_id": None,
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

    def test_record_scope_uses_source_record_ref_for_shared_two_field_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group = Path("output/sample/questions_json/2026")
            source_relatives = [
                group / "00_source" / f"question_2026_{number}.json"
                for number in (1, 2)
            ]
            for number, relative in enumerate(source_relatives, 1):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "question_bodies": [
                                {
                                    "originalQuestionId": "shared-review-id",
                                    "sourceQuestionKey": "sample:2026:shared",
                                    "questionBodyText": f"問題文{number}",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
            patch_relative = (
                group
                / "21_explanationText_added"
                / "question_2026_2_explanationText_added.json"
            )
            patch = root / patch_relative
            patch.parent.mkdir(parents=True)
            non_target_record = {
                "originalQuestionId": "shared-review-id",
                "sourceQuestionKey": "sample:2026:shared",
                "sourceRecordRef": "question_2026_1.json#0",
                "explanationText": ["別問題は変更しない"],
            }
            patch_record = {
                "originalQuestionId": "shared-review-id",
                "sourceQuestionKey": "sample:2026:shared",
                "public_question_id": None,
                "explanationText": ["変更前"],
            }
            patch.write_text(
                json.dumps(
                    {"question_bodies": [non_target_record, patch_record]}
                ),
                encoding="utf-8",
            )
            alias_groups = [
                [
                    "shared-review-id",
                    "sample:2026:shared",
                    f"question_2026_{number}.json#0",
                ]
                for number in (1, 2)
            ]
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan.update(
                {
                    "sourceFiles": [path.as_posix() for path in source_relatives],
                    "targetRecordAliasGroups": alias_groups,
                    "targetRecordBindings": [
                        {
                            "uiQuestionId": f"ui-{number}",
                            "reviewQuestionId": "shared-review-id",
                            "sourceQuestionKey": "sample:2026:shared",
                            "sourceRecordRef": f"question_2026_{number}.json#0",
                            "aliases": aliases,
                        }
                        for number, aliases in zip((1, 2), alias_groups)
                    ],
                    "allowedPatchDirs": ["21_explanationText_added"],
                    "allowedPatchFiles": [patch_relative.as_posix()],
                    "targetRecordScopes": {
                        patch_relative.as_posix(): [alias_groups[1]]
                    },
                }
            )
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (patch.parent, (root / run["resultReceiptPath"]).parent),
            )
            patch_record["explanationText"] = ["変更後"]
            patch_record["sourceRecordRef"] = "question_2026_2.json#0"
            patch.write_text(
                json.dumps(
                    {"question_bodies": [non_target_record, patch_record]}
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

            coordinator._validate_record_scope(
                "sample",
                run["runId"],
                store.get("sample", run["runId"]),
                {patch_relative},
            )

            non_target_record["explanationText"] = ["別問題を誤変更"]
            patch.write_text(
                json.dumps(
                    {"question_bodies": [non_target_record, patch_record]}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "対象問題以外のrecord変更",
            ):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {patch_relative},
                )

    def test_record_scope_rejects_ambiguous_unbound_legacy_rows(self):
        patch_relative = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/q1_explanationText_added.json"
        )
        source_relative = Path(
            "output/sample/questions_json/2026/00_source/q1.json"
        )
        aliases = ["ui-q1", "review-q1", "sample:2026:q1", "q1.json#0"]
        legacy_rows = [
            {
                "originalQuestionId": "review-q1",
                "sourceQuestionKey": "sample:2026:q1",
                "public_question_id": None,
                "explanationText": [label],
            }
            for label in ("候補A", "候補B")
        ]
        after_rows = copy.deepcopy(legacy_rows)
        after_rows[0].update(
            sourceRecordRef="q1.json#0",
            explanationText=["変更後"],
        )

        with self.assertRaisesRegex(
            QualificationRunError,
            "ID fieldが空又は不正",
        ):
            self._validate_record_scope_change(
                patch_relative,
                {"question_bodies": legacy_rows},
                {"question_bodies": after_rows},
                plan_updates={
                    "sourceFiles": [source_relative.as_posix()],
                    "targetRecordAliasGroups": [aliases],
                    "targetRecordBindings": [
                        {
                            "uiQuestionId": "ui-q1",
                            "reviewQuestionId": "review-q1",
                            "sourceQuestionKey": "sample:2026:q1",
                            "sourceRecordRef": "q1.json#0",
                            "aliases": aliases,
                        }
                    ],
                    "allowedPatchDirs": ["21_explanationText_added"],
                    "allowedPatchFiles": [patch_relative.as_posix()],
                    "targetRecordScopes": {
                        patch_relative.as_posix(): [aliases]
                    },
                },
                source_payloads={
                    source_relative: {
                        "question_bodies": [
                            {
                                "originalQuestionId": "review-q1",
                                "sourceQuestionKey": "sample:2026:q1",
                            }
                        ]
                    }
                },
            )

    def test_existing_patch_is_blocked_only_when_source_binding_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/shared_explanationText_added.json"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            bindings = [
                {
                    "uiQuestionId": f"ui-{number}",
                    "reviewQuestionId": "shared-review-id",
                    "sourceQuestionKey": "sample:2026:shared",
                    "sourceRecordRef": f"question_2026_1.json#{number - 1}",
                    "aliases": [
                        "shared-review-id",
                        "sample:2026:shared",
                        f"question_2026_1.json#{number - 1}",
                        f"https://example.test/q{number}",
                    ],
                }
                for number in (1, 2)
            ]
            scopes = {relative.as_posix(): [value["aliases"] for value in bindings]}
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            path.write_text(
                json.dumps(
                    [
                        {
                            "originalQuestionId": "shared-review-id",
                            "sourceQuestionKey": "sample:2026:shared",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "source recordへ一意に対応できません",
            ):
                coordinator._reject_ambiguous_existing_patch_rows(
                    {relative.as_posix()},
                    scopes,
                    bindings,
                )

            path.write_text(
                json.dumps(
                    [
                        {
                            "originalQuestionId": "shared-review-id",
                            "sourceQuestionKey": "sample:2026:shared",
                            "question_url": "https://example.test/q1",
                        },
                        {
                            "originalQuestionId": "shared-review-id",
                            "sourceQuestionKey": "sample:2026:shared",
                            "sourceRecordRef": "question_2026_1.json#1",
                        },
                    ]
                ),
                encoding="utf-8",
            )

            coordinator._reject_ambiguous_existing_patch_rows(
                {relative.as_posix()},
                scopes,
                bindings,
            )

    def test_law_sidecar_allows_only_exact_v1_to_v2_identity_migration(self):
        def validate(before_schema: str) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source_relative = Path(
                    "output/sample/questions_json/2026/00_source/q1.json"
                )
                sidecar_relative = Path(
                    "output/sample/review/law_revision_audit/"
                    "2026_law_revision_audit.jsonl"
                )
                source = root / source_relative
                sidecar = root / sidecar_relative
                source.parent.mkdir(parents=True)
                sidecar.parent.mkdir(parents=True)
                source.write_text(
                    json.dumps(
                        {
                            "question_bodies": [
                                {
                                    "originalQuestionId": "source-review-q1",
                                    "sourceQuestionKey": "sample:2026:q1",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                sidecar.write_text(
                    json.dumps(
                        {
                            "schemaVersion": before_schema,
                            "reviewQuestionId": "ui-hash-q1",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                store = QualificationRunStore(root)
                plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
                aliases = [
                    "ui-hash-q1",
                    "source-review-q1",
                    "sample:2026:q1",
                    "q1.json#0",
                ]
                plan.update(
                    {
                        "sourceFiles": [source_relative.as_posix()],
                        "targetRecordAliasGroups": [aliases],
                        "targetRecordBindings": [
                            {
                                "uiQuestionId": "ui-hash-q1",
                                "reviewQuestionId": "source-review-q1",
                                "sourceQuestionKey": "sample:2026:q1",
                                "sourceRecordRef": "q1.json#0",
                                "aliases": aliases,
                            }
                        ],
                        "allowedPatchDirs": [],
                        "allowedPatchFiles": [],
                        "allowedWriteFiles": [sidecar_relative.as_posix()],
                        "targetRecordScopes": {
                            sidecar_relative.as_posix(): [aliases]
                        },
                    }
                )
                run = store.create(plan, status="running", prompt="work")
                store.write_baseline(
                    "sample",
                    run["runId"],
                    (sidecar.parent, (root / run["resultReceiptPath"]).parent),
                )
                sidecar.write_text(
                    json.dumps(
                        {
                            "schemaVersion": "law-revision-audit/v2",
                            # v2へ移行しても既存のreview IDは変更しない。
                            "reviewQuestionId": "ui-hash-q1",
                            "sourceQuestionKey": "sample:2026:q1",
                            "sourceRecordRef": "q1.json#0",
                        }
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
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {sidecar_relative},
                )

        validate("law-revision-audit/v1")
        with self.assertRaisesRegex(
            QualificationRunError,
            "既存ID fieldの変更",
        ):
            validate("law-revision-audit/v2")

    def test_law_sidecar_ignores_unchanged_non_target_v1_rows(self):
        non_target = {
            "schemaVersion": "law-revision-audit/v1",
            "reviewQuestionId": "ui-hash-q2",
        }
        self._validate_multi_row_law_sidecar(non_target, non_target)

    def test_law_sidecar_still_rejects_non_target_v1_changes(self):
        non_target = {
            "schemaVersion": "law-revision-audit/v1",
            "reviewQuestionId": "ui-hash-q2",
        }
        with self.assertRaisesRegex(QualificationRunError, "対象問題以外"):
            self._validate_multi_row_law_sidecar(
                non_target,
                {**non_target, "sourceSummary": "changed"},
            )

    def test_record_scope_is_file_specific_across_year_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_relative = Path(
                "output/sample/review/scoped/2025.jsonl"
            )
            second_relative = Path(
                "output/sample/review/scoped/2026.jsonl"
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


if __name__ == "__main__":
    unittest.main()  # noqa: F405
