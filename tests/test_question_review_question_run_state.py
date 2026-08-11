from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.question_run_state import (
    RUN_SCHEMA_VERSION,
    QuestionRunStateError,
    QuestionRunStateStore,
    question_state_filename,
)


def _execution(question_id: str, index: int) -> dict:
    return {
        "questionId": question_id,
        "displayLabel": f"問題{index}",
        "displayOrder": index,
        "status": "queued",
        "stages": [
            {
                "stageId": "question_type",
                "stageCode": "10",
                "stageLabel": "問題形式",
                "workItemKey": f"{question_id}:question_type",
                "status": "queued",
                "validationAttempts": [],
            },
            {
                "stageId": "correct_choice",
                "stageCode": "23",
                "stageLabel": "正答",
                "workItemKey": f"{question_id}:correct_choice",
                "status": "queued",
                "validationAttempts": [],
            },
        ],
    }


class QuestionRunStateStoreTest(unittest.TestCase):
    def _initialize(self, root: Path, count: int = 3):
        run_dir = root / "output/question_review_console/workflow_runs/gas/run"
        run_dir.mkdir(parents=True)
        plan = {
            "qualification": "gas",
            "kind": "orchestration",
            "workType": "maintenance_flow",
            "sourceFiles": ["output/gas/questions_json/2025/00_source/q.json"],
            "selectedUpdateTargetIds": [
                "question_type.question_type",
            ],
            "questionExecutions": [
                _execution(f"question-{index}", index)
                for index in range(1, count + 1)
            ],
            "workVersionReceipt": {
                "recordedCount": 1,
                "items": [{"recordedCount": 1, "source": "resume"}],
            },
        }
        parent = {
            "runId": "run",
            "qualification": "gas",
            "kind": "orchestration",
            "workType": "maintenance_flow",
            "status": "queued",
            "queueStatus": "queued",
            "createdAt": "2026-07-26T00:00:00+09:00",
            "updatedAt": "2026-07-26T00:00:00+09:00",
            **copy.deepcopy(plan),
        }
        store = QuestionRunStateStore(root)
        manifest = store.initialize(run_dir, plan, parent)
        return store, run_dir, plan, manifest

    def test_initializes_immutable_plan_small_parent_and_one_json_per_question(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, run_dir, plan, manifest = self._initialize(root, count=64)

            self.assertEqual(manifest["schemaVersion"], RUN_SCHEMA_VERSION)
            self.assertNotIn("questionExecutions", manifest)
            self.assertNotIn("sourceFiles", manifest)
            self.assertNotIn("workVersionReceipt", manifest)
            self.assertEqual(
                manifest["selectedUpdateTargetIds"],
                plan["selectedUpdateTargetIds"],
            )
            self.assertLess(
                len(json.dumps(manifest, ensure_ascii=False).encode("utf-8")),
                256 * 1024,
            )
            self.assertEqual(len(list((run_dir / "questions").glob("*.json"))), 64)
            hydrated = store.hydrate(run_dir, manifest)
            self.assertEqual(hydrated["sourceFiles"], plan["sourceFiles"])
            self.assertEqual(len(hydrated["questionExecutions"]), 64)
            self.assertEqual(
                hydrated["workVersionReceipt"]["recordedCount"],
                1,
            )
            mismatched = copy.deepcopy(manifest)
            mismatched["selectedUpdateTargetIds"] = ["other.target"]
            with self.assertRaisesRegex(
                QuestionRunStateError,
                "再開項目",
            ):
                store.hydrate(run_dir, mismatched)

    def test_updates_only_target_question_and_rejects_stale_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, run_dir, _plan, manifest = self._initialize(root)
            untouched_path = (
                run_dir / "questions" / question_state_filename("question-2")
            )
            untouched = untouched_path.read_bytes()

            state = store.update_question(
                run_dir,
                manifest,
                "question-1",
                lambda value: value["execution"]["stages"][0].update(
                    status="validated"
                ),
                expected_revision=0,
            )
            self.assertEqual(state["revision"], 1)
            self.assertEqual(
                state["execution"]["stages"][0]["status"],
                "validated",
            )
            self.assertEqual(untouched_path.read_bytes(), untouched)
            with self.assertRaisesRegex(
                QuestionRunStateError,
                "stale update",
            ):
                store.update_question(
                    run_dir,
                    manifest,
                    "question-1",
                    lambda value: None,
                    expected_revision=0,
                )

    def test_tampered_question_and_plan_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, run_dir, _plan, manifest = self._initialize(root)
            question_path = (
                run_dir / "questions" / question_state_filename("question-1")
            )
            question = json.loads(question_path.read_text(encoding="utf-8"))
            question["execution"]["displayLabel"] = "改ざん"
            question_path.write_text(
                json.dumps(question, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(QuestionRunStateError, "selfHash"):
                store.load_question(run_dir, manifest, "question-1")

            store, run_dir, _plan, manifest = self._initialize(
                root / "second"
            )
            plan_path = run_dir / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["plan"]["qualification"] = "other"
            plan_path.write_text(
                json.dumps(plan, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(QuestionRunStateError, "hash"):
                store.load_plan(run_dir, manifest)

    def test_plan_read_accepts_one_ctime_only_race_after_bounded_reread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, run_dir, _plan, manifest = self._initialize(root)
            signatures = iter(((1, 100, 200, 300), (1, 100, 200, 301), (1, 100, 200, 301)))
            with patch.object(store, "_file_signature", side_effect=lambda *_args, **_kwargs: next(signatures)), patch(
                "tools.question_review_console.question_run_state._read_json",
                wraps=lambda path, **_kwargs: json.loads(path.read_text(encoding="utf-8")),
            ) as reads:
                loaded = store.load_plan(run_dir, manifest)

        self.assertEqual(loaded["qualification"], "gas")
        self.assertEqual(reads.call_count, 2)

    def test_sixty_four_question_initialization_survives_one_ctime_only_race(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_signature = QuestionRunStateStore._file_signature
            plan_signature_reads = 0

            def one_ctime_race(path, *, label):
                nonlocal plan_signature_reads
                signature = original_signature(path, label=label)
                if path.name != "plan.json":
                    return signature
                plan_signature_reads += 1
                if plan_signature_reads == 1:
                    return (*signature[:3], signature[3] - 1)
                return signature

            with patch.object(
                QuestionRunStateStore,
                "_file_signature",
                side_effect=one_ctime_race,
            ):
                store, run_dir, _plan, manifest = self._initialize(root, count=64)

            hydrated = store.hydrate(run_dir, manifest)
            summary = json.loads(
                (run_dir / "question_summary.json").read_text(encoding="utf-8")
            )
            question_file_count = len(
                list((run_dir / "questions").glob("*.json"))
            )

        self.assertEqual(hydrated["planHash"], manifest["planHash"])
        self.assertGreaterEqual(plan_signature_reads, 3)
        self.assertEqual(len(hydrated["questionExecutions"]), 64)
        self.assertEqual(summary["questionCount"], 64)
        self.assertEqual(question_file_count, 64)

    def test_plan_read_rejects_non_ctime_signature_changes_without_reread(self):
        changes = ((2, 100, 200, 300), (1, 101, 200, 300), (1, 100, 201, 300))
        for changed in changes:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store, run_dir, _plan, manifest = self._initialize(root)
                signatures = iter(((1, 100, 200, 300), changed))
                with patch.object(store, "_file_signature", side_effect=lambda *_args, **_kwargs: next(signatures)), patch(
                    "tools.question_review_console.question_run_state._read_json",
                    wraps=lambda path, **_kwargs: json.loads(path.read_text(encoding="utf-8")),
                ) as reads, self.assertRaisesRegex(QuestionRunStateError, "before=.*after="):
                    store.load_plan(run_dir, manifest)
                self.assertEqual(reads.call_count, 1)

    def test_plan_read_rejects_repeated_ctime_only_change_with_signatures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, run_dir, _plan, manifest = self._initialize(root)
            signatures = iter(((1, 100, 200, 300), (1, 100, 200, 301), (1, 100, 200, 302)))
            with patch.object(store, "_file_signature", side_effect=lambda *_args, **_kwargs: next(signatures)), self.assertRaisesRegex(
                QuestionRunStateError,
                r"before=\(1, 100, 200, 301\), after=\(1, 100, 200, 302\)",
            ):
                store.load_plan(run_dir, manifest)

    def test_stable_plan_rejects_payload_manifest_and_canonical_hash_mismatch(self):
        for mismatch in ("payload", "manifest", "canonical"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store, run_dir, _plan, manifest = self._initialize(root)
                plan_path = run_dir / "plan.json"
                payload = json.loads(plan_path.read_text(encoding="utf-8"))
                if mismatch == "payload":
                    payload["planHash"] = "0" * 64
                elif mismatch == "manifest":
                    manifest["planHash"] = "1" * 64
                else:
                    payload["plan"]["qualification"] = "changed"
                plan_path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(QuestionRunStateError, "hash"):
                    store.load_plan(run_dir, manifest)

    def test_summary_is_rebuilt_from_question_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, run_dir, _plan, manifest = self._initialize(root)
            store.update_question(
                run_dir,
                manifest,
                "question-1",
                lambda value: value["execution"]["stages"][0].update(
                    status="validated"
                ),
            )
            summary = store.rebuild_summary(run_dir, manifest)
            self.assertEqual(summary["questionCount"], 3)
            self.assertEqual(
                summary["questions"][0]["stages"][0]["status"],
                "validated",
            )
            self.assertEqual(summary["queueSummary"]["validatedWorkItemCount"], 1)

    def test_shared_prerequisite_receipts_are_part_of_hydrated_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store, run_dir, _plan, manifest = self._initialize(root)
            manifest["sharedWorkVersionReceipts"] = [
                {"recordedCount": 2, "source": "category_setup"}
            ]

            hydrated = store.hydrate(run_dir, manifest)

            self.assertEqual(
                hydrated["workVersionReceipt"]["recordedCount"],
                3,
            )
            self.assertEqual(
                len(hydrated["workVersionReceipt"]["items"]),
                2,
            )


if __name__ == "__main__":
    unittest.main()
