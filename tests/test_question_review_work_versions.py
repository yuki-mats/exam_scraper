import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import tools.question_review_console.work_versions as work_versions_module
from tools.question_review_console.work_versions import QuestionWorkVersionStore


def question(*, law_related=False):
    return {
        "id": "question-1",
        "reviewKey": "sample:2026:question_1:original-1",
        "qualification": "sample",
        "publicationQualificationId": "sample-public",
        "listGroupId": "2026",
        "originalQuestionId": "original-1",
        "isLawRelated": law_related,
    }


def policy(stage_id="question_type", *, fingerprint="fingerprint-1"):
    return {
        "id": stage_id,
        "code": "01" if stage_id == "question_type" else "03b",
        "label": "問題形式" if stage_id == "question_type" else "現行法監査",
        "policyVersion": "1.0",
        "policyFingerprint": fingerprint,
    }


def question_for(question_id, *, list_group_id):
    item = question()
    item.update(
        id=f"question-{question_id}",
        reviewKey=f"sample:{list_group_id}:question_{question_id}:original-{question_id}",
        listGroupId=list_group_id,
        originalQuestionId=f"original-{question_id}",
    )
    return item


class QuestionWorkVersionStoreTests(unittest.TestCase):
    def test_distinct_work_version_paths_write_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            original_atomic_write = work_versions_module.atomic_write
            rendezvous = threading.Barrier(2)
            counter_lock = threading.Lock()
            active_writes = 0
            peak_writes = 0

            def observed_atomic_write(path, content):
                nonlocal active_writes, peak_writes
                with counter_lock:
                    active_writes += 1
                    peak_writes = max(peak_writes, active_writes)
                try:
                    rendezvous.wait(timeout=3)
                    original_atomic_write(path, content)
                finally:
                    with counter_lock:
                        active_writes -= 1

            items = [
                question_for("1", list_group_id="2025"),
                question_for("2", list_group_id="2026"),
            ]
            with patch.object(
                work_versions_module,
                "atomic_write",
                side_effect=observed_atomic_write,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            store.record_stage,
                            [item],
                            policy(),
                            run_id=f"run-{index}",
                            source="validated_run",
                        )
                        for index, item in enumerate(items, start=1)
                    ]
                    receipts = [future.result(timeout=5) for future in futures]

            saved_counts = [
                len(
                    json.loads(
                        store.question_path_for(item).read_text(
                            encoding="utf-8"
                        )
                    )["questions"]
                )
                for item in items
            ]

        self.assertEqual(peak_writes, 2)
        self.assertEqual(
            [receipt["recordedCount"] for receipt in receipts],
            [1, 1],
        )
        self.assertEqual(saved_counts, [1, 1])

    def test_distinct_questions_in_same_group_write_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            original_atomic_write = work_versions_module.atomic_write
            rendezvous = threading.Barrier(2)
            counter_lock = threading.Lock()
            active_writes = 0
            peak_writes = 0

            def observed_atomic_write(path, content):
                nonlocal active_writes, peak_writes
                with counter_lock:
                    active_writes += 1
                    peak_writes = max(peak_writes, active_writes)
                try:
                    rendezvous.wait(timeout=3)
                    original_atomic_write(path, content)
                finally:
                    with counter_lock:
                        active_writes -= 1

            items = [
                question_for("1", list_group_id="2026"),
                question_for("2", list_group_id="2026"),
            ]
            with patch.object(
                work_versions_module,
                "atomic_write",
                side_effect=observed_atomic_write,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            store.record_stage,
                            [item],
                            policy(),
                            run_id=f"run-{index}",
                            source="validated_run",
                        )
                        for index, item in enumerate(items, start=1)
                    ]
                    receipts = [future.result(timeout=5) for future in futures]
            saved = store.load_group("sample", "2026")

        self.assertEqual(peak_writes, 2)
        self.assertEqual(
            [receipt["recordedCount"] for receipt in receipts],
            [1, 1],
        )
        self.assertEqual(len(saved["questions"]), 2)

    def test_same_question_path_serializes_without_lost_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            original_atomic_write = work_versions_module.atomic_write
            counter_lock = threading.Lock()
            active_writes = 0
            peak_writes = 0
            item = question_for("1", list_group_id="2026")

            def observed_atomic_write(path, content):
                nonlocal active_writes, peak_writes
                with counter_lock:
                    active_writes += 1
                    peak_writes = max(peak_writes, active_writes)
                try:
                    time.sleep(0.05)
                    original_atomic_write(path, content)
                finally:
                    with counter_lock:
                        active_writes -= 1

            with patch.object(
                work_versions_module,
                "atomic_write",
                side_effect=observed_atomic_write,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            store.record_stage,
                            [item],
                            policy(stage_id),
                            run_id=f"run-{index}",
                            source="validated_run",
                        )
                        for index, stage_id in enumerate(
                            ("question_type", "law_audit"),
                            start=1,
                        )
                    ]
                    receipts = [future.result(timeout=5) for future in futures]
            record = store.record_for(item)

        self.assertEqual(peak_writes, 1)
        self.assertEqual(
            [receipt["recordedCount"] for receipt in receipts],
            [1, 1],
        )
        self.assertEqual(
            set(record["stages"]),
            {"question_type", "law_audit"},
        )

    def test_manual_policy_is_tracked_only_after_its_patch_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            required_policy = policy()
            manual_policy = {
                **policy("originalize"),
                "automatic": False,
                "patchDir": "05_originalized",
            }
            store.record_stage(
                [item], required_policy, run_id="run-1", source="validated_run"
            )

            before_patch = store.status_for(
                item, [manual_policy, required_policy]
            )
            item["paths"] = {
                "patches": [
                    "output/sample/questions_json/independent/05_originalized/"
                    "question_originalized.json"
                ]
            }
            after_patch = store.status_for(
                item, [manual_policy, required_policy]
            )

        self.assertTrue(before_patch["allCurrent"])
        self.assertEqual(before_patch["applicableCount"], 1)
        self.assertEqual(
            [stage["id"] for stage in before_patch["stages"]],
            ["question_type"],
        )
        self.assertFalse(after_patch["allCurrent"])
        self.assertEqual(after_patch["applicableCount"], 2)
        self.assertEqual(after_patch["unrecordedStageIds"], ["originalize"])

    def test_failed_manual_patch_does_not_opt_question_into_version_tracking(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            patch_path = (
                "output/sample/questions_json/independent/05_originalized/"
                "question_originalized.json"
            )
            item = {
                **question(),
                "paths": {"patches": [patch_path]},
                "failedRunChangedPaths": [patch_path],
            }
            status = store.status_for(
                item,
                [
                    {
                        **policy("originalize"),
                        "automatic": False,
                        "patchDir": "05_originalized",
                    }
                ],
            )

        self.assertEqual(status["applicableCount"], 0)
        self.assertEqual(status["stages"], [])

    def test_store_rejects_parent_path_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            with self.assertRaisesRegex(ValueError, "invalid"):
                store.question_directory_for("..", "2026")

    def test_legacy_version_is_outdated_and_current_run_replaces_it(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            current_policy = policy()

            initial = store.status_for(item, [current_policy])
            legacy = store.record_stage(
                [item],
                current_policy,
                run_id=None,
                source="firestore_published_backfill",
                version=0,
                policy_fingerprint_override="legacy-unknown",
            )
            old = store.status_for(item, [current_policy])
            current = store.record_stage(
                [item],
                current_policy,
                run_id="run-1",
                source="validated_run",
            )
            complete = store.status_for(item, [current_policy])
            record = store.record_for(item)

        self.assertEqual(initial["status"], "unrecorded")
        self.assertEqual(legacy["recordedCount"], 1)
        self.assertEqual(old["status"], "outdated")
        self.assertEqual(old["stages"][0]["recordedVersion"], "0.0")
        self.assertEqual(current["recordedCount"], 1)
        self.assertTrue(complete["allCurrent"])
        self.assertEqual(complete["stages"][0]["runId"], "run-1")
        self.assertEqual(
            [entry["version"] for entry in record["stages"]["question_type"]["history"]],
            ["0.0"],
        )

    def test_minor_change_stays_current_and_major_change_requires_rework(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            store.record_stage(
                [item], policy(), run_id="run-1", source="validated_run"
            )

            minor = store.status_for(
                item, [{**policy(), "policyVersion": "1.1"}]
            )
            major = store.status_for(
                item, [{**policy(), "policyVersion": "2.0"}]
            )

        self.assertEqual(minor["status"], "current")
        self.assertIn("洗い替え不要", minor["stages"][0]["detail"])
        self.assertEqual(major["status"], "outdated")

    def test_migration_normalizes_legacy_group_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QuestionWorkVersionStore(root)
            item = question()
            store.record_stage(
                [item], policy(), run_id="run-1", source="validated_run"
            )
            question_path = store.question_path_for(item)
            payload = json.loads(question_path.read_text(encoding="utf-8"))
            payload["schemaVersion"] = "question-work-versions/v1"
            stage = next(iter(payload["questions"].values()))["stages"]["question_type"]
            stage["version"] = 1
            stage["history"] = [{"version": 0}]
            legacy_path = store.legacy_group_path_for("sample", "2026")
            legacy_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            question_path.unlink()

            dry_run = QuestionWorkVersionStore(root).migrate_all()
            migrated = QuestionWorkVersionStore(root).migrate_all(execute=True)
            saved = json.loads(question_path.read_text(encoding="utf-8"))

        self.assertEqual(dry_run["changedFileCount"], 2)
        self.assertEqual(migrated["stageRecordCount"], 1)
        self.assertEqual(saved["schemaVersion"], "question-work-versions/v4")
        self.assertFalse(legacy_path.exists())
        saved_stage = next(iter(saved["questions"].values()))["stages"]["question_type"]
        self.assertEqual(saved_stage["version"], "1.0")
        self.assertEqual(saved_stage["history"][0]["version"], "0.0")

    def test_migration_reconciles_legacy_ui_identity_into_canonical_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QuestionWorkVersionStore(root)
            canonical = question()
            legacy = {**canonical, "reviewKey": canonical["id"]}
            store.record_stage(
                [canonical],
                policy("question_type"),
                run_id="question-type-run",
                source="validated_run",
            )
            store.record_stage(
                [legacy],
                policy("law_audit"),
                run_id="law-audit-run",
                source="validated_run",
            )
            group = store.load_group("sample", "2026")
            group["schemaVersion"] = "question-work-versions/v3"
            legacy_path = store.legacy_group_path_for("sample", "2026")
            legacy_path.write_text(
                json.dumps(group, ensure_ascii=False),
                encoding="utf-8",
            )
            for path in store.question_directory_for(
                "sample",
                "2026",
            ).glob("*.json"):
                path.unlink()

            result = QuestionWorkVersionStore(root).migrate_all(execute=True)
            migrated_store = QuestionWorkVersionStore(root)
            record = migrated_store.record_for(canonical)
            question_files = list(
                migrated_store.question_directory_for(
                    "sample",
                    "2026",
                ).glob("*.json")
            )

        self.assertEqual(result["questionCount"], 1)
        self.assertEqual(len(question_files), 1)
        self.assertFalse(legacy_path.exists())
        self.assertEqual(
            set(record["stages"]),
            {"question_type", "law_audit"},
        )

    def test_migration_write_failure_restores_legacy_group_and_removes_shards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QuestionWorkVersionStore(root)
            items = [
                question_for("1", list_group_id="2026"),
                question_for("2", list_group_id="2026"),
            ]
            store.record_stage(
                items,
                policy(),
                run_id="run-1",
                source="validated_run",
            )
            group = store.load_group("sample", "2026")
            group["schemaVersion"] = "question-work-versions/v3"
            legacy_path = store.legacy_group_path_for("sample", "2026")
            legacy_path.write_text(
                json.dumps(group, ensure_ascii=False),
                encoding="utf-8",
            )
            original_legacy = legacy_path.read_bytes()
            for path in store.question_directory_for(
                "sample",
                "2026",
            ).glob("*.json"):
                path.unlink()
            original_atomic_write = work_versions_module.atomic_write
            write_count = 0

            def fail_second_write(path, content):
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise OSError("migration write failed")
                return original_atomic_write(path, content)

            with (
                patch.object(
                    work_versions_module,
                    "atomic_write",
                    side_effect=fail_second_write,
                ),
                self.assertRaisesRegex(OSError, "migration write failed"),
            ):
                QuestionWorkVersionStore(root).migrate_all(execute=True)
            remaining_question_files = list(
                store.question_directory_for(
                    "sample",
                    "2026",
                ).glob("*.json")
            )
            recovered_legacy = legacy_path.read_bytes()

        self.assertEqual(recovered_legacy, original_legacy)
        self.assertEqual(remaining_question_files, [])

    def test_backfill_never_overwrites_a_validated_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            current_policy = policy()
            store.record_stage(
                [item], current_policy, run_id="run-1", source="validated_run"
            )

            receipt = store.record_stage(
                [item],
                current_policy,
                run_id=None,
                source="firestore_published_backfill",
                only_missing=True,
                version=0,
                policy_fingerprint_override="legacy-unknown",
            )
            status = store.status_for(item, [current_policy])

        self.assertEqual(receipt["recordedCount"], 0)
        self.assertEqual(receipt["skippedCount"], 1)
        self.assertTrue(status["allCurrent"])
        self.assertEqual(status["stages"][0]["runId"], "run-1")

    def test_same_version_with_changed_policy_fingerprint_stays_current(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            store.record_stage(
                [item], policy(), run_id="run-1", source="validated_run"
            )

            status = store.status_for(
                item, [policy(fingerprint="fingerprint-changed")]
            )

        self.assertEqual(status["status"], "current")
        self.assertFalse(status["stages"][0]["policyFingerprintMatches"])

    def test_invalidated_run_returns_only_that_stage_to_outdated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            explanation_policy = {
                **policy("explanation"),
                "code": "03",
                "label": "解説",
                "policyVersion": "2.0",
            }
            store.record_stage(
                [item], explanation_policy, run_id="bad-run", source="validated_run"
            )

            receipt = store.invalidate_stage_run(
                "sample",
                "2026",
                stage_id="explanation",
                run_id="bad-run",
                question_ids=["question-1"],
                reason="文体規則に適合しない出力を成功扱いにしたため",
                receipt_id="invalidate-1",
                execute=True,
            )
            status = store.status_for(item, [explanation_policy])
            record = store.record_for(item)["stages"]["explanation"]

        self.assertEqual(receipt["invalidatedCount"], 1)
        self.assertEqual(status["status"], "outdated")
        self.assertEqual(status["stages"][0]["recordedVersion"], "0.0")
        self.assertEqual(record["source"], "invalidated_run")
        self.assertEqual(record["invalidatedRunId"], "bad-run")
        self.assertEqual(record["history"][-1]["version"], "2.0")

    def test_invalidation_does_not_overwrite_a_newer_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            explanation_policy = {
                **policy("explanation"),
                "code": "03",
                "label": "解説",
                "policyVersion": "2.0",
            }
            store.record_stage(
                [item],
                explanation_policy,
                run_id="bad-run",
                source="validated_run",
            )
            original_transaction_paths = (
                store.transaction_paths_for_questions
            )
            injected = False

            def inject_newer_run(questions):
                nonlocal injected
                items = list(questions)
                if not injected:
                    injected = True
                    store.record_stage(
                        [item],
                        explanation_policy,
                        run_id="newer-run",
                        source="validated_run",
                    )
                return original_transaction_paths(items)

            with patch.object(
                store,
                "transaction_paths_for_questions",
                side_effect=inject_newer_run,
            ):
                receipt = store.invalidate_stage_run(
                    "sample",
                    "2026",
                    stage_id="explanation",
                    run_id="bad-run",
                    question_ids=["question-1"],
                    reason="旧runだけを無効化するため",
                    receipt_id="invalidate-1",
                    execute=True,
                )
            record = store.record_for(item)["stages"]["explanation"]

        self.assertEqual(receipt["invalidatedCount"], 0)
        self.assertEqual(receipt["skippedQuestionIds"], ["question-1"])
        self.assertEqual(record["runId"], "newer-run")

    def test_law_audit_version_records_explicit_non_law_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            non_law = question(law_related=False)
            receipt = store.record_stage(
                [non_law],
                policy("law_audit"),
                run_id="run-1",
                source="validated_run",
            )
            status = store.status_for(non_law, [policy("law_audit")])

        self.assertEqual(receipt["recordedCount"], 1)
        self.assertEqual(status["applicableCount"], 1)
        self.assertTrue(status["allCurrent"])

    def test_corrupt_question_file_fails_closed_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            path = store.question_path_for(item)
            path.parent.mkdir(parents=True)
            path.write_text("{broken", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "読めません"):
                store.record_for(item)
            with self.assertRaisesRegex(ValueError, "読めません"):
                store.record_stage(
                    [item], policy(), run_id="run-1", source="validated_run"
                )
            unchanged = path.read_text(encoding="utf-8")

        self.assertEqual(unchanged, "{broken")

    def test_corrupt_question_file_does_not_block_another_question(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            broken = question_for("1", list_group_id="2026")
            healthy = question_for("2", list_group_id="2026")
            broken_path = store.question_path_for(broken)
            broken_path.parent.mkdir(parents=True)
            broken_path.write_text("{broken", encoding="utf-8")

            receipt = store.record_stage(
                [healthy],
                policy(),
                run_id="healthy-run",
                source="validated_run",
            )
            healthy_record = store.record_for(healthy)

        self.assertEqual(receipt["recordedCount"], 1)
        self.assertEqual(
            healthy_record["stages"]["question_type"]["runId"],
            "healthy-run",
        )

    def test_partial_update_records_only_selected_target(self):
        target_policy = {
            **policy("explanation"),
            "code": "03",
            "label": "解説",
            "policyVersion": "4.0",
            "updateTargets": [
                {
                    "id": "basic_explanation",
                    "selectionId": "explanation.basic_explanation",
                    "label": "基本解説",
                    "fields": ["explanationText"],
                },
                {
                    "id": "supplementary_questions",
                    "selectionId": "explanation.supplementary_questions",
                    "label": "補足質問と回答",
                    "fields": ["suggestedQuestionDetailsByChoice"],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            item = question()
            receipt = store.record_stage(
                [item],
                target_policy,
                run_id="supplement-run",
                source="validated_run",
                target_ids=["explanation.supplementary_questions"],
            )
            full_status = store.status_for(item, [target_policy])
            supplement_status = store.status_for(
                item,
                [
                    {
                        **target_policy,
                        "selectedUpdateTargetIds": [
                            "explanation.supplementary_questions"
                        ],
                    }
                ],
            )
            store.record_stage(
                [item],
                target_policy,
                run_id="basic-run",
                source="validated_run",
                target_ids=["explanation.basic_explanation"],
            )
            complete = store.status_for(item, [target_policy])
            saved = json.loads(
                store.question_path_for(item).read_text(encoding="utf-8")
            )

        self.assertTrue(receipt["partial"])
        self.assertEqual(
            receipt["targetIds"], ["explanation.supplementary_questions"]
        )
        self.assertEqual(full_status["status"], "unrecorded")
        target_states = {
            target["id"]: target["status"]
            for target in full_status["stages"][0]["targets"]
        }
        self.assertEqual(target_states["explanation.basic_explanation"], "unrecorded")
        self.assertEqual(
            target_states["explanation.supplementary_questions"], "current"
        )
        self.assertTrue(supplement_status["allCurrent"])
        self.assertTrue(complete["allCurrent"])
        stage = next(iter(saved["questions"].values()))["stages"]["explanation"]
        self.assertNotIn("version", stage)
        self.assertEqual(
            set(stage["targets"]),
            {
                "explanation.basic_explanation",
                "explanation.supplementary_questions",
            },
        )

    def test_record_stage_reconciles_legacy_ui_id_with_canonical_review_key(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QuestionWorkVersionStore(Path(directory))
            canonical = question()
            legacy = {**canonical, "reviewKey": canonical["id"]}
            store.record_stage(
                [canonical],
                {**policy("question_type"), "policyVersion": "2.0"},
                run_id="question-type-run",
                source="validated_run",
            )
            store.record_stage(
                [legacy],
                {**policy("law_audit"), "policyVersion": "4.0"},
                run_id="law-audit-run",
                source="validated_run",
            )

            receipt = store.record_stage(
                [canonical],
                {**policy("explanation"), "policyVersion": "4.0"},
                run_id="explanation-run",
                source="validated_run",
            )
            saved = json.loads(
                store.question_path_for(canonical).read_text(encoding="utf-8")
            )
            record = store.record_for(canonical)
            alias_path = store.question_path_for(legacy)

        self.assertEqual(receipt["reconciledCount"], 1)
        self.assertEqual(len(saved["questions"]), 1)
        self.assertFalse(alias_path.exists())
        self.assertEqual(record["reviewKey"], canonical["reviewKey"])
        self.assertEqual(
            set(record["stages"]),
            {"question_type", "law_audit", "explanation"},
        )
        self.assertEqual(record["stages"]["law_audit"]["version"], "4.0")


if __name__ == "__main__":
    unittest.main()
