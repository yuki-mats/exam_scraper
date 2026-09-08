from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check.audit_question_run_performance import audit
from tools.question_review_console.question_run_state import QuestionRunStateStore


class PerformanceAuditTests(unittest.TestCase):
    def test_resume_rates_exclude_inherited_results_and_count_holds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            run.mkdir()
            executions = []
            for question_id, status, finished in (
                ("inherited", "validated", "2026-09-08T11:30:00+09:00"),
                ("new", "validated", "2026-09-08T11:59:30+09:00"),
                ("held", "blocked", "2026-09-08T11:59:40+09:00"),
            ):
                executions.append({"questionId": question_id, "status": status, "stages": [
                    {"stageId": "question_type", "status": status, "finishedAt": finished},
                ]})
            parent = {"runId": "run", "status": "running", "targetCount": 3,
                      "createdAt": "2026-09-08T11:59:00+09:00", "startedAt": "2026-09-08T11:59:00+09:00"}
            store = QuestionRunStateStore(root)
            manifest = store.initialize(run, {"questionExecutions": executions}, parent)
            (run / "manifest.json").write_text(json.dumps(manifest))
            store.update_question(run, manifest, "new", lambda s: s["attemptArtifacts"].update({"a": {
                "status": "succeeded", "stageId": "question_type",
                "result": {"commands": [{"command": "root-only", "status": "passed"}]},
                "batchQuestionResults": [{"commands": [{"command": "question content", "status": "failed"}]}],
            }}))
            report = audit(root, run, as_of="2026-09-08T12:00:00+09:00")
            self.assertEqual(report["remainingQuestions"], 1)
            self.assertEqual(report["blockedQuestions"], 1)
            self.assertEqual(report["completedQuestions"], 2)
            for window in report["windows"]:
                self.assertEqual(window["actualMinutes"], 1)
                self.assertEqual(window["validatedItemsPerHour"], 60)
                self.assertEqual(window["completedQuestionsPerHour"], 60)
            self.assertEqual(report["checks"], [{"command": "question content", "status": "failed", "count": 1}])
