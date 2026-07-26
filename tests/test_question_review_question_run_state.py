from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

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
