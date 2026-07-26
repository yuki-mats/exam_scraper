from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.qualification_runs import (
    QualificationRunError,
    QualificationRunStore,
)


def _flow_plan(target_path: Path) -> dict:
    question_id = "sample-2026-q1"
    return {
        "qualification": "sample",
        "lawWorkflowEnabled": True,
        "stageId": "question_type",
        "stageIds": ["question_type"],
        "stageCode": "10",
        "stageLabel": "問題形式",
        "mode": "remaining",
        "modeLabel": "未整備",
        "kind": "orchestration",
        "workType": "maintenance_flow",
        "targetCount": 1,
        "workItemCount": 1,
        "targetGroupIds": ["2026"],
        "progressTargets": [
            {
                "id": question_id,
                "uiQuestionId": question_id,
                "questionKey": "sample:2026:q1",
                "reviewKey": "sample:2026:q1",
                "sourceQuestionKey": "sample:2026:q1",
                "sourceRecordRef": "question.json#0",
                "reviewQuestionId": question_id,
                "listGroupId": "2026",
                "aliases": [
                    question_id,
                    "sample:2026:q1",
                    "question.json#0",
                ],
            }
        ],
        "progressStages": [
            {
                "id": "question_type",
                "code": "10",
                "label": "問題形式",
            }
        ],
        "policyTargets": {"question_type": [question_id]},
        "sourceFiles": [],
        "allowedPatchFiles": [target_path.as_posix()],
        "allowedWriteFiles": [],
        "questionExecutions": [
            {
                "questionId": question_id,
                "listGroupId": "2026",
                "status": "queued",
                "stages": [
                    {
                        "stageId": "question_type",
                        "stageCode": "10",
                        "stageLabel": "問題形式",
                        "workItemKey": f"{question_id}:question_type",
                        "status": "queued",
                        "validationAttempts": [],
                        "childRunIds": [],
                    }
                ],
            }
        ],
        "phaseExecutions": [
            {
                "id": "question_type",
                "status": "pending",
            }
        ],
        "queueStatus": "queued",
        "queueOrder": "question_turn",
        "retrySafe": True,
    }


class V2QuestionRunRecoveryTest(unittest.TestCase):
    def test_attempt_lookup_uses_question_hash_without_plan_id_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = Path(
                "output/sample/questions_json/2026/10_fixed/q.json"
            )
            store = QualificationRunStore(root)
            plan = _flow_plan(target)
            first = plan["questionExecutions"][0]
            plan["questionExecutions"] = [
                {
                    **first,
                    "questionId": f"sample-2026-q{index}",
                }
                for index in range(1, 99)
            ]
            run = store.create(plan, status="queued", prompt="flow")
            question_id = "sample-2026-q98"
            attempt = store.create_question_attempt(
                "sample",
                str(run["runId"]),
                question_id,
                "question_type",
                {
                    **plan,
                    "kind": "human",
                    "workType": "maintenance_question_type_candidate",
                    "parentRunId": run["runId"],
                },
                "prompt",
            )
            store.question_states.question_ids = lambda *_args, **_kwargs: (
                (_ for _ in ()).throw(
                    AssertionError("immutable plan ID scan must not run")
                )
            )

            recovered = store.get("sample", str(attempt["runId"]))

            self.assertEqual(recovered["questionId"], question_id)

    def test_terminal_question_attempt_is_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = Path(
                "output/sample/questions_json/2026/10_fixed/q.json"
            )
            store = QualificationRunStore(root)
            run = store.create(
                _flow_plan(target),
                status="queued",
                prompt="flow",
            )
            run_id = str(run["runId"])
            question_id = "sample-2026-q1"
            attempt = store.create_question_attempt(
                "sample",
                run_id,
                question_id,
                "question_type",
                {
                    **_flow_plan(target),
                    "kind": "human",
                    "workType": "maintenance_question_type_candidate",
                    "parentRunId": run_id,
                },
                "prompt",
            )
            attempt_id = str(attempt["runId"])
            store.update(
                "sample",
                attempt_id,
                status="succeeded",
                receiptValidated=True,
            )

            store.update(
                "sample",
                attempt_id,
                status="succeeded",
                receiptValidated=True,
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "終端済み",
            ):
                store.update(
                    "sample",
                    attempt_id,
                    status="failed",
                )

    def test_open_question_transaction_rolls_back_and_requeues_on_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "output/sample/questions_json/2026/10_fixed/q.json"
            target.parent.mkdir(parents=True)
            target.write_text('{"value":"before"}\n', encoding="utf-8")
            relative_target = target.relative_to(root)
            plan = _flow_plan(relative_target)
            store = QualificationRunStore(root)
            run = store.create(plan, status="queued", prompt="flow")
            run_id = str(run["runId"])
            question_id = "sample-2026-q1"
            child_plan = {
                **plan,
                "kind": "human",
                "workType": "maintenance_question_type_candidate",
                "parentRunId": run_id,
                "targetCount": 1,
                "workItemCount": 1,
            }
            attempt = store.create_question_attempt(
                "sample",
                run_id,
                question_id,
                "question_type",
                child_plan,
                "prompt",
            )
            attempt_id = str(attempt["runId"])
            store.update_question_stage(
                "sample",
                run_id,
                question_id,
                "question_type",
                status="committing",
                validationAttempts=[
                    {
                        "attempt": 1,
                        "childRunId": attempt_id,
                        "status": "running",
                    }
                ],
                childRunIds=[attempt_id],
            )
            store.write_baseline(
                "sample",
                attempt_id,
                (target,),
            )
            store.update(
                "sample",
                attempt_id,
                status="running",
                candidateTransactionOpen=True,
                receiptValidated=False,
            )
            store.update(
                "sample",
                run_id,
                status="running",
                queueStatus="running",
            )
            target.write_text('{"value":"after"}\n', encoding="utf-8")

            restarted = QualificationRunStore(root)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                '{"value":"after"}\n',
            )
            restarted.recover_interrupted_runs()
            recovered = restarted.get("sample", run_id)
            detail = restarted.question_detail(
                "sample",
                run_id,
                question_id,
            )

            self.assertEqual(
                target.read_text(encoding="utf-8"),
                '{"value":"before"}\n',
            )
            self.assertEqual(recovered["status"], "interrupted")
            self.assertTrue(recovered["retrySafe"])
            self.assertEqual(
                detail["execution"]["stages"][0]["status"],
                "queued",
            )
            self.assertEqual(
                detail["execution"]["stages"][0][
                    "validationAttempts"
                ][0]["status"],
                "interrupted",
            )
            self.assertFalse(
                (
                    restarted.run_directory("sample", attempt_id)
                    / "manifest.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
