from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.question_bank.feedback_daily import (
    _private_db,
    daily_summary,
    promote_ai_candidate,
    reconcile,
    record_decision,
    record_proposal,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FeedbackDailyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "private" / "ledger.sqlite3"
        self.db = _private_db(self.db_path)
        self.addCleanup(self.db.close)
        self.path1 = "users/user-1/questionIssueReportSubmissions/report-1"
        self.path2 = "users/user-2/questionIssueReportSubmissions/report-2"
        self.path3 = "users/user-3/questionIssueReportSubmissions/report-3"
        self.snapshot = {
            "cases": [
                {"id": "case-a", "workflowStatus": "unreviewed"},
                {"id": "case-b", "workflowStatus": "reviewed_no_change"},
            ],
            "caseReports": {
                "case-a": [
                    {"sourceSubmissionPath": self.path1},
                    {"sourceSubmissionPath": self.path2},
                ],
                "case-b": [{"sourceSubmissionPath": self.path1}],
            },
            "receipts": [
                {"sourceSubmissionPath": self.path1, "status": "processed"},
                {"sourceSubmissionPath": self.path2, "status": "duplicate"},
            ],
            "submissions": [
                {"sourceSubmissionPath": self.path1, "reportId": "report-1",
                 "questionId": "q1", "categories": ["question_content", "other"],
                 "receivedAt": "2026-10-01T00:00:00Z"},
                {"sourceSubmissionPath": self.path2, "reportId": "report-2",
                 "questionId": "q1", "categories": ["question_content"],
                 "receivedAt": "2026-10-01T00:01:00Z"},
                {"sourceSubmissionPath": self.path3, "reportId": "report-3",
                 "questionId": "q2", "categories": ["correct_answer"],
                 "receivedAt": "2026-10-01T00:02:00Z"},
            ],
            "aiQuestions": [
                {"sourcePath": "memos/memo-1", "questionId": "q1",
                 "createdAt": "2026-10-01T00:00:00Z", "textHash": sha("why?"),
                 "visibility": "public"},
                {"sourcePath": "memos/memo-2", "questionId": "q2",
                 "createdAt": "2026-10-01T00:00:00Z", "textHash": sha("private"),
                 "visibility": "private"},
            ],
        }

    def test_each_submission_has_one_task_and_reconciliation_flags_gap(self) -> None:
        result = reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(result["reportTasks"], 3)
        self.assertEqual(result["intakeGaps"], 1)
        self.assertEqual(result["intakeGapTaskIds"], [sha(self.path3)])
        self.assertEqual(result["reportsAwaitingDecision"], 2)
        rows = self.db.execute(
            "SELECT source_path, case_ids_json FROM report_tasks ORDER BY source_path"
        ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual(json.loads(rows[0]["case_ids_json"]), ["case-a", "case-b"])
        self.assertEqual(json.loads(rows[1]["case_ids_json"]), ["case-a"])
        self.assertEqual(result["aiQuestionsAwaitingReview"], 1)
        self.assertEqual(self.db_path.stat().st_mode & 0o777, 0o600)

    def test_decisions_and_proposal_survive_rescan_and_are_individual(self) -> None:
        reconcile(self.db, self.snapshot, source="fixture")
        task1 = sha(self.path1)
        task2 = sha(self.path2)
        record_decision(
            self.db, task_id=task1, decision="fix_required",
            reason="official source differs", evidence_ref="official-pdf:page-2",
            case_id="case-a",
        )
        self.assertEqual(daily_summary(self.db)["reportsAwaitingDecision"], 2)
        record_decision(
            self.db, task_id=task1, decision="fix_required",
            reason="official source differs", evidence_ref="official-pdf:page-2",
            case_id="case-b",
        )
        record_proposal(
            self.db, task_id=task1, proposal_hash=sha("proposal"),
            proposal_ref="private:proposal-1",
        )
        self.snapshot["cases"][0]["workflowStatus"] = "ready_for_approval"
        result = reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(result["patchApprovalsWaiting"], 1)
        self.assertEqual(result["reportsAwaitingDecision"], 1)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM decision_history WHERE task_id=?", (task1,)
        ).fetchone()[0], 2)
        self.assertIsNone(self.db.execute(
            "SELECT decision FROM report_tasks WHERE task_id=?", (task2,)
        ).fetchone()[0])

    def test_missing_source_and_changed_ai_text_are_not_silently_accepted(self) -> None:
        reconcile(self.db, self.snapshot, source="fixture")
        candidate_id = sha("memos/memo-1")
        need_id = promote_ai_candidate(
            self.db, candidate_id=candidate_id, title="Improve explanation",
            need="Explain why choice 2 is wrong", target="question:q1",
        )
        self.assertTrue(need_id)
        self.snapshot["submissions"] = self.snapshot["submissions"][:2]
        self.snapshot["aiQuestions"][0]["textHash"] = sha("changed")
        result = reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(result["intakeGaps"], 1)
        self.assertEqual(result["aiQuestionsAwaitingReview"], 1)
        self.assertIsNone(self.db.execute(
            "SELECT improvement_task_id FROM ai_question_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()[0])
        self.snapshot["aiQuestions"] = []
        reconcile(self.db, self.snapshot, source="fixture")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM ai_question_candidates"
        ).fetchone()[0], 0)
        self.assertEqual(daily_summary(self.db)["improvementTasksOpen"], 1)

    def test_incomplete_intake_cannot_receive_decision(self) -> None:
        reconcile(self.db, self.snapshot, source="fixture")
        with self.assertRaises(ValueError):
            record_decision(
                self.db, task_id=sha(self.path3), decision="no_change",
                reason="checked", evidence_ref="source:1",
            )
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM decision_history"
        ).fetchone()[0], 0)

    def test_multi_category_decisions_and_reopened_case_require_recheck(self) -> None:
        reconcile(self.db, self.snapshot, source="fixture")
        task_id = sha(self.path1)
        with self.assertRaises(ValueError):
            record_decision(
                self.db, task_id=task_id, decision="no_change",
                reason="checked", evidence_ref="source:1",
            )
        record_decision(
            self.db, task_id=task_id, case_id="case-a",
            decision="fix_required", reason="source differs", evidence_ref="source:1",
        )
        record_decision(
            self.db, task_id=task_id, case_id="case-b",
            decision="no_change", reason="source agrees", evidence_ref="source:2",
        )
        self.assertEqual(self.db.execute(
            "SELECT decision FROM report_tasks WHERE task_id=?", (task_id,)
        ).fetchone()[0], "mixed")
        self.snapshot["cases"][1]["workflowStatus"] = "unreviewed"
        reconcile(self.db, self.snapshot, source="fixture")
        self.assertIsNone(self.db.execute(
            "SELECT decision FROM report_tasks WHERE task_id=?", (task_id,)
        ).fetchone()[0])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM decision_history WHERE task_id=?", (task_id,)
        ).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
