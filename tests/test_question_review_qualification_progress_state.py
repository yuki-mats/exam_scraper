from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.question_review_console.qualification_progress import (
    derive_progress_completion,
)
from tools.question_review_console.qualification_runs import QualificationRunStore


def _plan(*, kind: str, stages: tuple[str, ...]) -> dict[str, object]:
    return {
        "qualification": "sample",
        "stageId": stages[0],
        "stageIds": list(stages),
        "stageCode": " → ".join(stages),
        "stageLabel": "整備",
        "mode": "remaining",
        "modeLabel": "未整備のみ",
        "kind": kind,
        "workType": "maintenance_flow" if kind == "orchestration" else "maintenance",
        "targetCount": 1,
        "workItemCount": len(stages),
        "targetGroupIds": ["2026"],
        "policyTargets": {stage: ["q1"] for stage in stages},
        "progressTargets": [
            {"id": "q1", "questionKey": "sample:2026:q1", "listGroupId": "2026"}
        ],
        "stagePlans": [
            {"stageId": stage, "stageCode": stage, "stageLabel": stage}
            for stage in stages
        ],
        "sourceFiles": [],
        "canonicalDocs": [],
    }


def _events(question_id: str, stage: str, *, complete: bool) -> bytes:
    values = [
        {"event": "question_started", "questionId": question_id},
        {
            "event": "stage_completed",
            "questionId": question_id,
            "stageId": stage,
        },
    ]
    if complete:
        values.append({"event": "question_completed", "questionId": question_id})
    return "".join(json.dumps(value) + "\n" for value in values).encode()


class QualificationProgressStateTests(unittest.TestCase):
    def test_pure_summary_keeps_touched_processed_and_validated_separate(self) -> None:
        completion = derive_progress_completion(
            {"q1"},
            {"q1": {"first", "second"}},
            {("q1", "first"), ("outside", "first")},
            {("q1", "first")},
            set(),
            set(),
            set(),
        )

        self.assertEqual(completion.touched_questions, {"q1"})
        self.assertEqual(completion.processed_questions, set())
        self.assertEqual(completion.validated_questions, set())

    def test_single_progress_uses_shared_partial_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            run = store.create(
                _plan(kind="human", stages=("first", "second")),
                status="running",
                prompt="work",
            )
            (root / run["progressReceiptPath"]).write_bytes(
                _events("q1", "first", complete=False)
            )

            progress = store.progress("sample", run["runId"])

        self.assertEqual(progress["touchedQuestionCount"], 1)
        self.assertEqual(progress["processedQuestionCount"], 0)
        self.assertEqual(progress["validatedQuestionCount"], 0)

    def test_combined_progress_uses_shared_partial_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            parent = store.create(
                _plan(kind="orchestration", stages=("first", "second")),
                status="running",
            )
            child_plan = _plan(kind="human", stages=("first",))
            child_plan["parentRunId"] = parent["runId"]
            child = store.create(child_plan, status="running", prompt="work")
            (root / child["progressReceiptPath"]).write_bytes(
                _events("q1", "first", complete=True)
            )
            store.update(
                "sample",
                child["runId"],
                status="succeeded",
                receiptValidated=True,
            )
            store.update(
                "sample",
                parent["runId"],
                status="failed",
                childRunIds=[child["runId"]],
            )

            progress = store.combined_progress("sample", parent["runId"])

        self.assertEqual(progress["touchedQuestionCount"], 1)
        self.assertEqual(progress["processedQuestionCount"], 0)
        self.assertEqual(progress["validatedQuestionCount"], 0)


if __name__ == "__main__":
    unittest.main()
