import json
import tempfile
import time
import unittest
from pathlib import Path

from tools.question_review_console.jobs import JobManager
from tools.question_review_console.qualification_runs import (
    QualificationRunCoordinator,
    QualificationRunStore,
)
from tools.question_review_console.qualification_workflow import QualificationWorkflow


class FakeWorkflow:
    def plan(self, qualification, stage_id, mode="remaining"):
        machine = stage_id == "delivery"
        groups = ["2025", "2026"] if machine else ["2026"]
        return {
            "qualification": qualification,
            "stageId": stage_id,
            "stageCode": "出力" if machine else "03b",
            "stageLabel": "公開準備" if machine else "現行法監査",
            "purpose": "成果物を確認する" if machine else "一問一肢を監査する",
            "kind": "machine" if machine else "human",
            "mode": mode,
            "modeLabel": "全件洗い替え" if mode == "refresh" else "未作業のみ",
            "targetCount": len(groups) if machine else 3,
            "targetGroupIds": groups,
            "sourceFiles": ["output/sample/questions_json/2026/00_source"],
            "outputFiles": ["output/sample/questions_json/2026/21_explanationText_added/a.json"],
            "canonicalDocs": ["prompt/README.md"],
            "force": machine and mode == "refresh",
        }

    def prompt(self, qualification, stage_id, mode="remaining"):
        return {
            "qualification": qualification,
            "stageId": stage_id,
            "mode": mode,
            "targetCount": 3,
            "prompt": "# 資格単位の問題整備\n\n対象ファイルだけを一件ずつ監査する。\n",
        }


class FakeSynchronizer:
    def __init__(self):
        self.calls = []

    def preview(self, qualification, list_group_id, *, force=False):
        return {
            "previewToken": f"token-{list_group_id}-{force}",
            "questionCount": 2,
            "localReady": not force,
            "requiredFieldWarnings": [],
        }

    def run(self, qualification, list_group_id, token, emit, *, force=False):
        self.calls.append((qualification, list_group_id, force))
        emit(f"{list_group_id}: 完了")
        return {"message": "同期しました。"}


class SourceOnlyInventory:
    def inventory(self):
        return {
            "qualifications": [
                {"id": "new-exam", "listGroupIds": ["2026"]}
            ]
        }

    def group(self, qualification, list_group_id):
        return {
            "listGroupId": list_group_id,
            "questions": [
                {
                    "paths": {
                        "source": "output/new-exam/questions_json/2026/00_source/question_2026_1.json",
                        "patches": [],
                    },
                    "issues": [],
                    "issueCodes": [],
                    "isLawRelated": False,
                    "projected": {},
                    "workflow": {
                        "merge": "missing",
                        "convert": "missing",
                        "upload": "missing",
                    },
                }
            ],
        }


class QualificationRunTests(unittest.TestCase):
    def test_source_only_qualification_starts_setup_session(self):
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
            preview = coordinator.preview("new-exam", "setup", "remaining")
            started = coordinator.start(
                "new-exam", "setup", "remaining", preview["previewToken"]
            )

        self.assertEqual(preview["targetCount"], 1)
        self.assertIn("prompt/qualification_docs/README.md", preview["canonicalDocs"])
        self.assertEqual(preview["sourceFileCount"], 1)
        self.assertEqual(preview["outputFileCount"], 4)
        self.assertEqual(started["run"]["stageId"], "setup")
        self.assertIn("qualification_docs/new-exam", started["prompt"])
        self.assertIn("## 完了記録", started["prompt"])
        self.assertIn("result.json", started["prompt"])
        self.assertNotIn("## 問題文", started["prompt"])

    def test_human_run_persists_prompt_and_can_resume_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = FakeWorkflow()
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            restarted = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            recent = restarted.recent("sample")
            resumed = restarted.resume_prompt(
                "sample", started["run"]["runId"]
            )

        self.assertEqual(started["run"]["status"], "awaiting_changes")
        self.assertIsNone(started["job"])
        self.assertEqual(recent["activeRun"]["runId"], started["run"]["runId"])
        self.assertIn("資格単位の問題整備", resumed["prompt"])

    def test_human_run_converges_after_valid_result_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            receipt_path = root / started["run"]["resultReceiptPath"]
            receipt_path.write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "summary": "全対象を監査した。",
                        "commands": [
                            {"command": "python check.py", "status": "pass"}
                        ],
                        "changedFiles": ["output/sample/patch.json"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            recent = coordinator.recent("sample")

        self.assertIsNone(recent["activeRun"])
        self.assertEqual(recent["runs"][0]["status"], "succeeded")
        self.assertEqual(recent["runs"][0]["result"]["summary"], "全対象を監査した。")

    def test_invalid_success_receipt_does_not_complete_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            receipt_path = root / started["run"]["resultReceiptPath"]
            receipt_path.write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "summary": "検証していない。",
                        "commands": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            recent = coordinator.recent("sample")

        self.assertEqual(recent["activeRun"]["status"], "awaiting_changes")
        self.assertIn("pass検証", recent["activeRun"]["receiptError"])

    def test_result_receipt_path_in_manifest_cannot_redirect_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            redirected = root / "outside-result.json"
            redirected.write_text(
                json.dumps(
                    {
                        "status": "succeeded",
                        "summary": "外部",
                        "commands": [{"command": "fake", "status": "pass"}],
                    }
                ),
                encoding="utf-8",
            )
            coordinator.store.update(
                "sample",
                started["run"]["runId"],
                resultReceiptPath=str(redirected),
            )
            recent = coordinator.recent("sample")

        self.assertEqual(recent["activeRun"]["status"], "awaiting_changes")

    def test_delivery_run_processes_all_groups_and_records_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synchronizer = FakeSynchronizer()
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root, FakeWorkflow(), synchronizer, jobs, "secret"
            )
            preview = coordinator.preview("sample", "delivery", "refresh")
            started = coordinator.start(
                "sample", "delivery", "refresh", preview["previewToken"]
            )
            job_id = started["job"]["jobId"]
            deadline = time.monotonic() + 2
            job = jobs.get(job_id)
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(job_id)
            recent = coordinator.recent("sample")

        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(
            synchronizer.calls,
            [("sample", "2025", True), ("sample", "2026", True)],
        )
        self.assertEqual(recent["runs"][0]["status"], "succeeded")
        self.assertEqual(recent["runs"][0]["completedGroupIds"], ["2025", "2026"])

    def test_running_manifest_becomes_resumable_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            run = store.create(
                FakeWorkflow().plan("sample", "delivery", "remaining"),
                status="running",
            )
            recovered = QualificationRunStore(root).list("sample")

        self.assertEqual(recovered[0]["runId"], run["runId"])
        self.assertEqual(recovered[0]["status"], "interrupted")
        self.assertIn("再開", recovered[0]["error"])

    def test_resume_preview_excludes_completed_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            previous = store.create(
                FakeWorkflow().plan("sample", "delivery", "refresh"),
                status="failed",
            )
            store.update(
                "sample",
                previous["runId"],
                completedGroupIds=["2025"],
                error="2026で失敗",
            )
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                store=store,
            )
            preview = coordinator.preview(
                "sample",
                "delivery",
                "refresh",
                resumed_from=previous["runId"],
            )

        self.assertEqual(preview["targetGroupIds"], ["2026"])
        self.assertEqual(preview["targetCount"], 1)


if __name__ == "__main__":
    unittest.main()
