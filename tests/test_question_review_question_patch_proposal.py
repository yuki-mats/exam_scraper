import hashlib
import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from scripts.common.question_identity import SourceIdentityBinding
from tools.question_review_console.question_patch_proposal import (
    IsolatedQuestionPatchWorkspace,
    QuestionPatchProposalError,
    QuestionPatchProposalStore,
)


def _process_rebase_with_validation_barrier(
    workspace,
    binding_payload,
    aliases_by_path,
    attempting,
    post_write,
    release_validation,
    results,
):
    attempting.set()
    binding = SourceIdentityBinding.from_mapping(binding_payload)
    try:
        with workspace.canonical_transaction(
            workspace.changed_paths()
        ) as transaction:
            changed = transaction.rebase(
                binding=binding,
                aliases_by_path=aliases_by_path,
            )
            post_write.set()
            if not release_validation.wait(10):
                raise RuntimeError("validation barrier timeout")
        results.put(("done", changed))
    except Exception as exc:  # noqa: BLE001
        results.put(("error", str(exc)))


def _process_rebase_then_rollback_at_validation_barrier(
    workspace,
    binding_payload,
    aliases_by_path,
    attempting,
    post_write,
    first_rollback_failed,
    release_validation,
    rollback_bytes,
    results,
):
    attempting.set()
    binding = SourceIdentityBinding.from_mapping(binding_payload)
    try:
        with workspace.canonical_transaction(
            workspace.changed_paths()
        ) as transaction:
            changed = transaction.rebase(
                binding=binding,
                aliases_by_path=aliases_by_path,
            )
            post_write.set()
            first_rollback_failed.set()
            if not release_validation.wait(10):
                raise RuntimeError("validation barrier timeout")
            (workspace.repo_root / changed[0]).write_bytes(rollback_bytes)
        results.put(("rolled_back", changed))
    except Exception as exc:  # noqa: BLE001
        results.put(("error", str(exc)))


def _process_rebase_with_terminal_rollback_failure(
    workspace,
    binding_payload,
    aliases_by_path,
    attempting,
    post_write,
    terminal_failure_confirmed,
    release_lock,
    results,
):
    attempting.set()
    binding = SourceIdentityBinding.from_mapping(binding_payload)
    try:
        with workspace.canonical_transaction(
            workspace.changed_paths()
        ) as transaction:
            changed = transaction.rebase(
                binding=binding,
                aliases_by_path=aliases_by_path,
            )
            post_write.set()
            terminal_failure_confirmed.set()
            if not release_lock.wait(10):
                raise RuntimeError("terminal rollback barrier timeout")
            expected_states = {
                path.as_posix(): (
                    {"kind": "missing"}
                    if workspace.initial_bytes[path] is None
                    else {
                        "kind": "file",
                        "sha256": hashlib.sha256(
                            workspace.initial_bytes[path]
                        ).hexdigest(),
                    }
                )
                for path in transaction.changed_paths
            }
            transaction.mark_poisoned(
                "rollback verification failed",
                expected_states,
            )
        results.put(("terminal", changed))
    except Exception as exc:  # noqa: BLE001
        results.put(("error", str(exc)))


class QuestionPatchProposalStoreTests(unittest.TestCase):
    def test_round_trips_bound_preparation_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow_root = root / "output/question_review_console/workflow_runs"
            store = QuestionPatchProposalStore(root, workflow_root)
            written = store.write(
                "sample",
                "run-1",
                work_item_key="abc123",
                question_id="q1",
                stage_id="explanation",
                input_fingerprint="input-1",
                summary="修正案です。",
                thread_id="thread-1",
                session_id="session-1",
                turn_id="turn-1",
            )

            payload = store.read(
                "sample",
                "run-1",
                work_item_key="abc123",
                expected_hash=written["hash"],
                question_id="q1",
                stage_id="explanation",
                input_fingerprint="input-1",
            )

        self.assertEqual(payload["summary"], "修正案です。")
        self.assertIn("question_preparations/abc123.json", written["path"])

    def test_rejects_receipt_reused_for_another_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QuestionPatchProposalStore(
                root,
                root / "output/question_review_console/workflow_runs",
            )
            written = store.write(
                "sample",
                "run-1",
                work_item_key="abc123",
                question_id="q1",
                stage_id="explanation",
                input_fingerprint="input-1",
                summary="修正案です。",
                thread_id="thread-1",
                session_id="session-1",
                turn_id="turn-1",
            )

            with self.assertRaisesRegex(QuestionPatchProposalError, "一致"):
                store.read(
                    "sample",
                    "run-1",
                    work_item_key="abc123",
                    expected_hash=written["hash"],
                    question_id="q2",
                    stage_id="explanation",
                    input_fingerprint="input-1",
                )

    def test_rejects_path_segments_outside_workflow_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QuestionPatchProposalStore(
                root,
                root / "output/question_review_console/workflow_runs",
            )
            for qualification, run_id, work_item_key in (
                ("..", "run-1", "abc123"),
                ("sample", "..", "abc123"),
                ("sample", "run-1", ".."),
            ):
                with self.subTest(
                    qualification=qualification,
                    run_id=run_id,
                    work_item_key=work_item_key,
                ), self.assertRaises(QuestionPatchProposalError):
                    store.write(
                        qualification,
                        run_id,
                        work_item_key=work_item_key,
                        question_id="q1",
                        stage_id="explanation",
                        input_fingerprint="input-1",
                        summary="修正案です。",
                        thread_id="thread-1",
                        session_id="session-1",
                        turn_id="turn-1",
                    )


class IsolatedQuestionPatchWorkspaceTests(unittest.TestCase):
    @staticmethod
    def _record(question: str, explanation: str) -> dict[str, str]:
        return {
            "sourceQuestionKey": f"sample:2026:{question}",
            "reviewQuestionId": f"review-{question}",
            "sourceRecordRef": f"source.json#{question.removeprefix('q')}",
            "explanationText": explanation,
        }

    def test_rebases_one_record_without_losing_sibling_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_relative = Path(
                "output/sample/questions_json/2026/21_explanationText_added/patch.json"
            )
            patch_path = root / patch_relative
            patch_path.parent.mkdir(parents=True)
            q1 = self._record("q1", "before-1")
            q2 = self._record("q2", "before-2")
            patch_path.write_text(
                json.dumps([q1, q2], ensure_ascii=False),
                encoding="utf-8",
            )
            workspace = IsolatedQuestionPatchWorkspace.create(
                root,
                root / "output/question_review_console/run/isolated_workspace",
                qualification="sample",
                mutable_paths=[patch_relative.as_posix()],
            )
            isolated_patch = workspace.root / patch_relative
            candidate = json.loads(isolated_patch.read_text(encoding="utf-8"))
            candidate[0]["explanationText"] = "candidate-1"
            isolated_patch.write_text(
                json.dumps(candidate, ensure_ascii=False),
                encoding="utf-8",
            )

            canonical = json.loads(patch_path.read_text(encoding="utf-8"))
            canonical[1]["explanationText"] = "committed-by-sibling"
            patch_path.write_text(
                json.dumps(canonical, ensure_ascii=False),
                encoding="utf-8",
            )
            binding = SourceIdentityBinding.from_mapping(q1)
            changed = workspace.rebase_into_canonical(
                workspace.changed_paths(),
                binding=binding,
                aliases_by_path={
                    patch_relative.as_posix(): [list(binding.as_tuple())]
                },
            )
            result = json.loads(patch_path.read_text(encoding="utf-8"))

        self.assertEqual(changed, [patch_relative.as_posix()])
        self.assertEqual(result[0]["explanationText"], "candidate-1")
        self.assertEqual(result[1]["explanationText"], "committed-by-sibling")

    def test_independent_workspaces_commit_distinct_records_in_reverse_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("output/sample/questions_json/group/patch.json")
            canonical = root / relative
            canonical.parent.mkdir(parents=True)
            records = [
                self._record("q1", "before-1"),
                self._record("q2", "before-2"),
            ]
            canonical.write_text(json.dumps(records), encoding="utf-8")
            workspaces = [
                IsolatedQuestionPatchWorkspace.create(
                    root,
                    root / f"output/question_review_console/run-{number}",
                    qualification="sample",
                    mutable_paths=[relative.as_posix()],
                )
                for number in (1, 2)
            ]
            for workspace, index in zip(workspaces, (0, 1)):
                candidate_path = workspace.root / relative
                candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
                candidate[index]["explanationText"] = f"candidate-{index + 1}"
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

            for workspace, record in reversed(list(zip(workspaces, records))):
                binding = SourceIdentityBinding.from_mapping(record)
                workspace.rebase_into_canonical(
                    workspace.changed_paths(),
                    binding=binding,
                    aliases_by_path={relative.as_posix(): [list(binding.as_tuple())]},
                )
            result = json.loads(canonical.read_text(encoding="utf-8"))

        self.assertEqual(
            [record["explanationText"] for record in result],
            ["candidate-1", "candidate-2"],
        )

    def test_process_transactions_serialize_through_validation_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("output/sample/questions_json/group/patch.json")
            canonical = root / relative
            canonical.parent.mkdir(parents=True)
            records = [
                self._record("q1", "before-1"),
                self._record("q2", "before-2"),
            ]
            canonical.write_text(json.dumps(records), encoding="utf-8")
            workspaces = []
            for index in range(2):
                workspace = IsolatedQuestionPatchWorkspace.create(
                    root,
                    root / f"output/question_review_console/process-{index}",
                    qualification="sample",
                    mutable_paths=[relative.as_posix()],
                )
                candidate_path = workspace.root / relative
                candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
                candidate[index]["explanationText"] = f"process-{index + 1}"
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
                workspaces.append(workspace)

            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            attempting = [context.Event(), context.Event()]
            post_write = [context.Event(), context.Event()]
            releases = [context.Event(), context.Event()]
            processes = []
            for index in range(2):
                binding = SourceIdentityBinding.from_mapping(records[index])
                process = context.Process(
                    target=_process_rebase_with_validation_barrier,
                    args=(
                        workspaces[index],
                        records[index],
                        {relative.as_posix(): [list(binding.as_tuple())]},
                        attempting[index],
                        post_write[index],
                        releases[index],
                        results,
                    ),
                )
                processes.append(process)

            processes[0].start()
            self.assertTrue(post_write[0].wait(10))
            processes[1].start()
            self.assertTrue(attempting[1].wait(10))
            self.assertFalse(post_write[1].wait(0.3))
            releases[0].set()
            self.assertTrue(post_write[1].wait(10))
            releases[1].set()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            outcomes = sorted(results.get(timeout=2) for _ in processes)
            final_records = json.loads(canonical.read_text(encoding="utf-8"))

        self.assertEqual([outcome[0] for outcome in outcomes], ["done", "done"])
        self.assertEqual(
            [record["explanationText"] for record in final_records],
            ["process-1", "process-2"],
        )

    def test_process_transactions_fail_closed_on_same_record_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("output/sample/questions_json/group/patch.json")
            canonical = root / relative
            canonical.parent.mkdir(parents=True)
            record = self._record("q1", "before")
            canonical.write_text(json.dumps([record]), encoding="utf-8")
            workspaces = []
            for index in range(2):
                workspace = IsolatedQuestionPatchWorkspace.create(
                    root,
                    root / f"output/question_review_console/conflict-{index}",
                    qualification="sample",
                    mutable_paths=[relative.as_posix()],
                )
                candidate_path = workspace.root / relative
                candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
                candidate[0]["explanationText"] = f"process-{index + 1}"
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
                workspaces.append(workspace)

            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            binding = SourceIdentityBinding.from_mapping(record)
            aliases = {relative.as_posix(): [list(binding.as_tuple())]}
            events = [
                (context.Event(), context.Event(), context.Event())
                for _ in range(2)
            ]
            processes = [
                context.Process(
                    target=_process_rebase_with_validation_barrier,
                    args=(
                        workspaces[index],
                        record,
                        aliases,
                        *events[index],
                        results,
                    ),
                )
                for index in range(2)
            ]
            processes[0].start()
            self.assertTrue(events[0][1].wait(10))
            processes[1].start()
            self.assertTrue(events[1][0].wait(10))
            self.assertFalse(events[1][1].wait(0.3))
            events[0][2].set()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=2) for _ in processes]
            final_record = json.loads(canonical.read_text(encoding="utf-8"))[0]

        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["done", "error"])
        self.assertTrue(
            any("準備後に対象recordが更新" in outcome[1] for outcome in outcomes)
        )
        self.assertEqual(final_record["explanationText"], "process-1")

    def test_process_rollback_finishes_before_another_record_can_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("output/sample/questions_json/group/patch.json")
            canonical = root / relative
            canonical.parent.mkdir(parents=True)
            records = [
                self._record("q1", "before-1"),
                self._record("q2", "before-2"),
            ]
            canonical.write_text(json.dumps(records), encoding="utf-8")
            baseline_bytes = canonical.read_bytes()
            workspaces = []
            for index in range(2):
                workspace = IsolatedQuestionPatchWorkspace.create(
                    root,
                    root / f"output/question_review_console/rollback-{index}",
                    qualification="sample",
                    mutable_paths=[relative.as_posix()],
                )
                candidate_path = workspace.root / relative
                candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
                candidate[index]["explanationText"] = f"process-{index + 1}"
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
                workspaces.append(workspace)

            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            first_events = tuple(context.Event() for _ in range(4))
            second_events = tuple(context.Event() for _ in range(3))
            bindings = [
                SourceIdentityBinding.from_mapping(record) for record in records
            ]
            first = context.Process(
                target=_process_rebase_then_rollback_at_validation_barrier,
                args=(
                    workspaces[0],
                    records[0],
                    {relative.as_posix(): [list(bindings[0].as_tuple())]},
                    *first_events,
                    baseline_bytes,
                    results,
                ),
            )
            second = context.Process(
                target=_process_rebase_with_validation_barrier,
                args=(
                    workspaces[1],
                    records[1],
                    {relative.as_posix(): [list(bindings[1].as_tuple())]},
                    *second_events,
                    results,
                ),
            )
            first.start()
            self.assertTrue(first_events[1].wait(10))
            self.assertTrue(first_events[2].wait(10))
            second.start()
            self.assertTrue(second_events[0].wait(10))
            self.assertFalse(second_events[1].wait(0.3))
            first_events[3].set()
            self.assertTrue(second_events[1].wait(10))
            second_events[2].set()
            for process in (first, second):
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            outcomes = sorted(results.get(timeout=2)[0] for _ in range(2))
            final_records = json.loads(canonical.read_text(encoding="utf-8"))

        self.assertEqual(outcomes, ["done", "rolled_back"])
        self.assertEqual(
            [record["explanationText"] for record in final_records],
            ["before-1", "process-2"],
        )

    def test_process_terminal_rollback_failure_has_no_late_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("output/sample/questions_json/group/patch.json")
            canonical = root / relative
            canonical.parent.mkdir(parents=True)
            records = [
                self._record("q1", "before-1"),
                self._record("q2", "committed-before"),
            ]
            canonical.write_text(json.dumps(records), encoding="utf-8")
            workspaces = []
            for index in range(2):
                workspace = IsolatedQuestionPatchWorkspace.create(
                    root,
                    root / f"output/question_review_console/terminal-{index}",
                    qualification="sample",
                    mutable_paths=[relative.as_posix()],
                )
                candidate_path = workspace.root / relative
                candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
                candidate[index]["explanationText"] = f"process-{index + 1}"
                candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
                workspaces.append(workspace)

            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            first_events = tuple(context.Event() for _ in range(4))
            second_events = tuple(context.Event() for _ in range(3))
            bindings = [
                SourceIdentityBinding.from_mapping(record) for record in records
            ]
            first = context.Process(
                target=_process_rebase_with_terminal_rollback_failure,
                args=(
                    workspaces[0],
                    records[0],
                    {relative.as_posix(): [list(bindings[0].as_tuple())]},
                    *first_events,
                    results,
                ),
            )
            second = context.Process(
                target=_process_rebase_with_validation_barrier,
                args=(
                    workspaces[1],
                    records[1],
                    {relative.as_posix(): [list(bindings[1].as_tuple())]},
                    *second_events,
                    results,
                ),
            )
            first.start()
            self.assertTrue(first_events[1].wait(10))
            self.assertTrue(first_events[2].wait(10))
            second.start()
            self.assertTrue(second_events[0].wait(10))
            self.assertFalse(second_events[1].wait(0.3))
            first_events[3].set()
            for process in (first, second):
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=2) for _ in range(2)]
            final_records = json.loads(canonical.read_text(encoding="utf-8"))

        self.assertEqual(sorted(outcome[0] for outcome in outcomes), ["error", "terminal"])
        self.assertTrue(
            any("rollback未確認" in outcome[1] for outcome in outcomes)
        )
        self.assertEqual(
            [record["explanationText"] for record in final_records],
            ["process-1", "committed-before"],
        )

    def test_poison_clears_only_after_verified_baseline_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git_metadata = root / "git-metadata"
            git_metadata.mkdir()
            (root / ".git").write_text("gitdir: git-metadata\n", encoding="utf-8")
            relative = Path("output/sample/questions_json/group/patch.json")
            canonical = root / relative
            canonical.parent.mkdir(parents=True)
            record = self._record("q1", "before")
            canonical.write_text(json.dumps([record]), encoding="utf-8")
            baseline_bytes = canonical.read_bytes()
            workspace = IsolatedQuestionPatchWorkspace.create(
                root,
                root / "output/question_review_console/poison-repair",
                qualification="sample",
                mutable_paths=[relative.as_posix()],
            )
            candidate_path = workspace.root / relative
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            candidate[0]["explanationText"] = "candidate"
            candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
            binding = SourceIdentityBinding.from_mapping(record)
            aliases = {relative.as_posix(): [list(binding.as_tuple())]}
            expected_states = {
                relative.as_posix(): {
                    "kind": "file",
                    "sha256": hashlib.sha256(baseline_bytes).hexdigest(),
                }
            }
            with workspace.canonical_transaction(
                workspace.changed_paths()
            ) as transaction:
                transaction.rebase(binding=binding, aliases_by_path=aliases)
                transaction.mark_poisoned(
                    "rollback verification failed",
                    expected_states,
                )
            self.assertEqual(
                len(list((git_metadata / "question-patch-locks").glob("*.poison.json"))),
                1,
            )

            with self.assertRaisesRegex(
                QuestionPatchProposalError,
                "rollback未確認",
            ):
                with workspace.canonical_transaction(workspace.changed_paths()):
                    pass
            with self.assertRaisesRegex(
                QuestionPatchProposalError,
                "baseline復旧",
            ):
                workspace.clear_poison_after_verified_repair(
                    workspace.changed_paths()
                )

            canonical.write_bytes(baseline_bytes)
            workspace.clear_poison_after_verified_repair(
                workspace.changed_paths()
            )
            self.assertFalse(
                list((git_metadata / "question-patch-locks").glob("*.poison.json"))
            )
            with workspace.canonical_transaction(workspace.changed_paths()):
                pass

    def test_rejects_same_record_changed_after_workspace_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_relative = Path(
                "output/sample/questions_json/2026/21_explanationText_added/patch.json"
            )
            patch_path = root / patch_relative
            patch_path.parent.mkdir(parents=True)
            q1 = self._record("q1", "before")
            patch_path.write_text(json.dumps([q1]), encoding="utf-8")
            workspace = IsolatedQuestionPatchWorkspace.create(
                root,
                root / "output/question_review_console/run/isolated_workspace",
                qualification="sample",
                mutable_paths=[patch_relative.as_posix()],
            )
            isolated_patch = workspace.root / patch_relative
            candidate = json.loads(isolated_patch.read_text(encoding="utf-8"))
            candidate[0]["explanationText"] = "candidate"
            isolated_patch.write_text(json.dumps(candidate), encoding="utf-8")
            canonical = [self._record("q1", "manual-update")]
            patch_path.write_text(json.dumps(canonical), encoding="utf-8")
            binding = SourceIdentityBinding.from_mapping(q1)

            with self.assertRaisesRegex(
                QuestionPatchProposalError,
                "準備後に対象recordが更新",
            ):
                workspace.rebase_into_canonical(
                    workspace.changed_paths(),
                    binding=binding,
                    aliases_by_path={
                        patch_relative.as_posix(): [list(binding.as_tuple())]
                    },
                )

    def test_server_adds_complete_identity_to_existing_legacy_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_relative = Path(
                "output/sample/questions_json/2026/18_law_context_prepared/patch.json"
            )
            patch_path = root / patch_relative
            patch_path.parent.mkdir(parents=True)
            patch_path.write_text(
                json.dumps(
                    [{"original_question_id": "review-q1", "isLawRelated": True}]
                ),
                encoding="utf-8",
            )
            binding = SourceIdentityBinding.from_values(
                "sample:2026:q1",
                "review-q1",
                "source.json#1",
            )
            workspace = IsolatedQuestionPatchWorkspace.create(
                root,
                root / "output/question_review_console/run/isolated_workspace",
                qualification="sample",
                mutable_paths=[patch_relative.as_posix()],
            )

            workspace.apply_record_update(
                patch_relative,
                binding=binding,
                aliases={"review-q1"},
                set_fields={"isLawRelated": False},
                base_record={},
            )
            record = json.loads(
                (workspace.root / patch_relative).read_text(encoding="utf-8")
            )[0]

        self.assertEqual(
            {field: record[field] for field in binding.as_mapping()},
            binding.as_mapping(),
        )
        self.assertFalse(record["isLawRelated"])

    def test_server_preserves_existing_review_id_when_adding_source_refs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_relative = Path(
                "output/sample/review/law_revision_audit/2026.jsonl"
            )
            patch_path = root / patch_relative
            patch_path.parent.mkdir(parents=True)
            patch_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "law-revision-audit/v1",
                        "reviewQuestionId": "legacy-ui-id",
                        "auditStatus": "same_as_current",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            binding = SourceIdentityBinding.from_values(
                "sample:2026:q1",
                "firestore:q1-a,q1-b",
                "source.json#1",
            )
            workspace = IsolatedQuestionPatchWorkspace.create(
                root,
                root / "output/question_review_console/run/isolated_workspace",
                qualification="sample",
                mutable_paths=[patch_relative.as_posix()],
            )

            workspace.apply_record_update(
                patch_relative,
                binding=binding,
                aliases={"legacy-ui-id"},
                set_fields={"schemaVersion": "law-revision-audit/v2"},
                base_record={},
            )
            record = json.loads(
                (workspace.root / patch_relative).read_text(encoding="utf-8")
            )

        self.assertEqual(record["reviewQuestionId"], "legacy-ui-id")
        self.assertEqual(record["sourceQuestionKey"], binding.source_question_key)
        self.assertEqual(record["sourceRecordRef"], binding.source_record_ref)
        self.assertEqual(record["schemaVersion"], "law-revision-audit/v2")


if __name__ == "__main__":
    unittest.main()
