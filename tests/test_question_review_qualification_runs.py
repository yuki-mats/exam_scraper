import json
import tempfile
import time
import unittest
from pathlib import Path

from tools.question_review_console.codex_app_server import AppServerTurnResult
from tools.question_review_console.jobs import JobManager
from tools.question_review_console.qualification_runs import (
    QualificationRunCoordinator,
    QualificationRunError,
    QualificationRunStore,
    _maintenance_session_phases,
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
            "outputFiles": [
                "output/sample/questions_json/2026/"
                "21_explanationText_added/patch.json"
            ],
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
        self.merge_calls = []
        self.local_ready = True
        self.can_sync = True

    def preview(self, qualification, list_group_id, *, force=False):
        return {
            "previewToken": f"token-{list_group_id}-{force}",
            "questionCount": 2,
            "localReady": self.local_ready,
            "needsSync": force or not self.local_ready,
            "canSync": self.can_sync,
            "requiredFieldWarnings": [],
            "failedDeltaPaths": [],
        }

    def run(self, qualification, list_group_id, token, emit, *, force=False):
        self.calls.append((qualification, list_group_id, force))
        self.local_ready = True
        emit(f"{list_group_id}: 完了")
        return {"message": "同期しました。"}

    def refresh_merged_views(self, qualification, list_group_id, emit):
        self.merge_calls.append((qualification, list_group_id))
        emit(f"{list_group_id}: 工程間merge完了")
        return {
            "listGroupId": list_group_id,
            "status": "succeeded",
            "message": "次工程用のmergeを完了しました。",
        }


class SuccessfulAppServer:
    configured = True
    provider = "Codex App Server"

    def __init__(
        self,
        changed_files=(),
        *,
        temporary_helper=False,
        receipt=None,
    ):
        self.changed_files = list(changed_files)
        self.temporary_helper = temporary_helper
        self.receipt = receipt
        self.kwargs = {}
        self.calls = []

    def assert_subscription_access(self, *, force=True):
        return {"allowed": True, "planType": "pro"}

    def run_turn(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if kwargs["work_type"] == "maintenance_research":
            kwargs["on_thread_started"](
                "thread-research-1", "session-research-1"
            )
            kwargs["on_turn_started"](
                "thread-research-1", "turn-research-1"
            )
            return AppServerTurnResult(
                thread_id="thread-research-1",
                session_id="session-research-1",
                turn_id="turn-research-1",
                final_message="問題IDごとの調査案",
                model="gpt-research-test",
                service_tier=None,
                subagent_thread_ids=("subagent-1", "subagent-2"),
                subagent_models=("gpt-5.5",),
                subagent_reasoning_efforts=("high",),
            )
        self.kwargs = kwargs
        kwargs["on_thread_started"](
            "thread-maintenance-1", "session-maintenance-1"
        )
        kwargs["on_turn_started"](
            "thread-maintenance-1", "turn-maintenance-1"
        )
        receipt_line = next(
            line
            for line in prompt.splitlines()
            if "完了時に検証結果を次へJSONで保存" in line
        )
        receipt = self.receipt or {
            "status": "succeeded",
            "summary": "対象工程を整備した。",
            "commands": [{"command": "python check.py", "status": "pass"}],
            "changedFiles": self.changed_files,
        }
        Path(receipt_line.split("`")[1]).write_text(
            json.dumps(receipt, ensure_ascii=False),
            encoding="utf-8",
        )
        notifications = []
        if self.temporary_helper:
            helper_path = kwargs["cwd"] / "generate_progress.py"
            helper_path.write_text("# disposable helper\n", encoding="utf-8")
            notifications.append(str(helper_path))
        return AppServerTurnResult(
            thread_id="thread-maintenance-1",
            session_id="session-maintenance-1",
            turn_id="turn-maintenance-1",
            final_message="整備完了",
            model="gpt-test",
            service_tier=None,
            changed_files=tuple(notifications),
        )


class ReceiptCompletingAppServer(SuccessfulAppServer):
    changed_file = (
        "output/sample/questions_json/2026/"
        "21_explanationText_added/patch.json"
    )

    def __init__(
        self,
        root,
        *,
        mutate_after_probe=False,
        clobber_manifest_after_probe=False,
    ):
        super().__init__()
        self.root = root
        self.mutate_after_probe = mutate_after_probe
        self.clobber_manifest_after_probe = clobber_manifest_after_probe

    def run_turn(self, prompt, **kwargs):
        if kwargs["work_type"] == "maintenance_research":
            return super().run_turn(prompt, **kwargs)
        self.calls.append((prompt, kwargs))
        self.kwargs = kwargs
        kwargs["on_thread_started"]("thread-receipt-1", "session-receipt-1")
        kwargs["on_turn_started"]("thread-receipt-1", "turn-receipt-1")
        patch_path = self.root / self.changed_file
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text("[]\n", encoding="utf-8")
        receipt_line = next(
            line
            for line in prompt.splitlines()
            if "完了時に検証結果を次へJSONで保存" in line
        )
        Path(receipt_line.split("`")[1]).write_text(
            json.dumps(
                {
                    "status": "succeeded",
                    "summary": "対象工程を整備した。",
                    "commands": [
                        {"command": "python check.py", "status": "pass"}
                    ],
                    "changedFiles": [self.changed_file],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        if not kwargs["completion_probe"]():
            raise AssertionError("成功receiptを検出できませんでした。")
        if self.clobber_manifest_after_probe:
            manifest_path = (
                Path(receipt_line.split("`")[1]).parent.parent / "manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["status"] = "running"
            manifest["result"] = None
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
        if self.mutate_after_probe:
            patch_path.write_text(
                "[ ]\n", encoding="utf-8"
            )
        return AppServerTurnResult(
            thread_id="thread-receipt-1",
            session_id="session-receipt-1",
            turn_id="turn-receipt-1",
            final_message="",
            model="gpt-test",
            service_tier=None,
            changed_files=(str(patch_path),),
            completion_mode="receipt_interrupted",
        )


class FailingAppServer:
    configured = True
    provider = "Codex App Server"

    def assert_subscription_access(self, *, force=True):
        return {"allowed": True, "planType": "pro"}

    def run_turn(self, prompt, **kwargs):
        kwargs["on_thread_started"](
            "thread-failed-1", "session-failed-1"
        )
        kwargs["on_turn_started"]("thread-failed-1", "turn-failed-1")
        raise RuntimeError("turn crashed")


class ConfiguredAppServer:
    configured = True
    provider = "Codex App Server"

    def assert_subscription_access(self, *, force=True):
        return {"allowed": True, "planType": "pro"}


class DeferredJobs:
    def start(self, *, kind, key, worker):
        self.worker = worker
        return {"jobId": "job-deferred", "status": "queued"}


class FlowAppServer:
    configured = True
    provider = "Codex App Server"

    def __init__(
        self,
        *,
        fail_on_writer=None,
        events=None,
        changed_files_by_work_type=None,
        before_receipt=None,
    ):
        self.fail_on_writer = fail_on_writer
        self.writer_count = 0
        self.calls = []
        self.events = events
        self.changed_files_by_work_type = changed_files_by_work_type or {}
        self.before_receipt = before_receipt

    def assert_subscription_access(self, *, force=True):
        return {"allowed": True, "planType": "pro"}

    def run_turn(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.events is not None:
            self.events.append(f"session:{kwargs['work_type']}")
        self.writer_count += 1
        number = self.writer_count
        kwargs["on_thread_started"](
            f"thread-flow-{number}", f"session-flow-{number}"
        )
        kwargs["on_turn_started"](
            f"thread-flow-{number}", f"turn-flow-{number}"
        )
        if self.fail_on_writer == number:
            raise RuntimeError(f"phase {number} failed")
        if self.before_receipt is not None:
            self.before_receipt(kwargs["work_type"])
        changed_files = list(
            self.changed_files_by_work_type.get(kwargs["work_type"], [])
        )
        receipt_line = next(
            line
            for line in prompt.splitlines()
            if "完了時に検証結果を次へJSONで保存" in line
        )
        Path(receipt_line.split("`")[1]).write_text(
            json.dumps(
                {
                    "status": "succeeded",
                    "summary": f"phase {number} completed",
                    "commands": [{"command": "python check.py", "status": "pass"}],
                    "changedFiles": changed_files,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return AppServerTurnResult(
            thread_id=f"thread-flow-{number}",
            session_id=f"session-flow-{number}",
            turn_id=f"turn-flow-{number}",
            final_message=f"phase {number} completed",
            model="gpt-test",
            service_tier=None,
        )


class SourceOnlyInventory:
    def inventory(self):
        return {
            "qualifications": [
                {"id": "new-exam", "listGroupIds": ["2026"]}
            ]
        }

    def group(self, qualification, list_group_id):
        original_id = f"new-exam-{list_group_id}-q1"
        return {
            "listGroupId": list_group_id,
            "questions": [
                {
                    "id": original_id,
                    "reviewKey": (
                        f"new-exam:{list_group_id}:"
                        f"question_{list_group_id}_1:{original_id}"
                    ),
                    "qualification": "new-exam",
                    "listGroupId": list_group_id,
                    "originalQuestionId": original_id,
                    "paths": {
                        "source": (
                            f"output/new-exam/questions_json/{list_group_id}/"
                            f"00_source/question_{list_group_id}_1.json"
                        ),
                        "patches": [],
                    },
                    "issues": [],
                    "issueCodes": [],
                    "isLawRelated": False,
                    "projected": {"originalQuestionId": original_id},
                    "workflow": {
                        "merge": "missing",
                        "convert": "missing",
                        "upload": "missing",
                    },
                }
            ],
        }


class MultiGroupSourceInventory(SourceOnlyInventory):
    def inventory(self):
        return {
            "qualifications": [
                {"id": "new-exam", "listGroupIds": ["2025", "2026"]}
            ]
        }


class NonLawSourceInventory(SourceOnlyInventory):
    def group(self, qualification, list_group_id):
        group = super().group(qualification, list_group_id)
        question = group["questions"][0]
        question["projected"] = {
            **question["projected"],
            "isLawRelated": False,
            "lawGroundedExplanationNotNeeded": True,
        }
        return group


class MultiGroupNonLawSourceInventory(NonLawSourceInventory):
    def inventory(self):
        return {
            "qualifications": [
                {"id": "new-exam", "listGroupIds": ["2025", "2026"]}
            ]
        }


class LawSourceInventory(SourceOnlyInventory):
    def group(self, qualification, list_group_id):
        group = super().group(qualification, list_group_id)
        question = group["questions"][0]
        question["isLawRelated"] = True
        question["projected"] = {
            **question["projected"],
            "isLawRelated": True,
            "lawGroundedExplanationNotNeeded": False,
            "correctChoiceText": ["正しい"],
            "lawRevisionFacts": [
                {
                    "auditStatus": "same_as_current",
                    "reviewState": "secondary_verified",
                    "current": {"correctChoiceText": "正しい"},
                    "evidenceSummary": {"verdict": "correct"},
                }
            ],
            "lawReferences": [
                {
                    "lawTitle": "ガス事業法",
                    "lawId": "329AC0000000051",
                    "article": "第2条",
                    "verificationStatus": "verified",
                }
            ],
            "explanationText": [
                "正しい。ガス事業法第2条の定義に該当する。"
            ],
            "suggestedQuestions": [
                "現行法のガス事業法第2条は何を定義していますか？"
            ],
            "suggestedQuestionDetails": [
                {"answer": "ガス事業法第2条が対象事業を定義しています。"}
            ],
        }
        return group


class IncompleteLawSourceInventory(LawSourceInventory):
    def group(self, qualification, list_group_id):
        group = super().group(qualification, list_group_id)
        group["questions"][0]["issueCodes"] = [
            "law_audit_metadata_incomplete"
        ]
        del group["questions"][0]["projected"]["lawRevisionFacts"][0][
            "current"
        ]
        return group


class UnverifiedLawSourceInventory(LawSourceInventory):
    def group(self, qualification, list_group_id):
        group = super().group(qualification, list_group_id)
        reference = group["questions"][0]["projected"]["lawReferences"][0]
        reference.pop("lawId")
        reference.pop("verificationStatus")
        return group

class QualificationRunTests(unittest.TestCase):
    @staticmethod
    def _write_law_audit_sidecar(
        root: Path,
        list_group_id: str,
        rows: list[dict],
    ) -> Path:
        path = (
            root
            / "output"
            / "new-exam"
            / "review"
            / "law_revision_audit"
            / f"{list_group_id}_law_revision_audit.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                json.dumps(
                    {
                        "qualification": "new-exam",
                        "listGroupId": list_group_id,
                        "sourceSummary": "分類と根拠を確認した。",
                        **row,
                    },
                    ensure_ascii=False,
                )
                + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _law_audit_policy_run(
        workflow: QualificationWorkflow,
        *,
        list_group_ids: list[str] | None = None,
    ) -> dict:
        groups = list_group_ids or ["2026"]
        policy = workflow.versioned_policies("new-exam")["law_audit"]
        targets = [f"new-exam-{group}-q1" for group in groups]
        return {
            "runId": "law-audit-sidecar-run",
            "qualification": "new-exam",
            "targetGroupIds": groups,
            "policyVersions": {"law_audit": policy["policyVersion"]},
            "policyFingerprints": {
                "law_audit": policy["policyFingerprint"]
            },
            "policyTargets": {"law_audit": targets},
        }

    def _run_receipt_completion(
        self,
        *,
        mutate_after_probe,
        clobber_manifest_after_probe=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                jobs,
                "secret",
                app_server=ReceiptCompletingAppServer(
                    root,
                    mutate_after_probe=mutate_after_probe,
                    clobber_manifest_after_probe=clobber_manifest_after_probe,
                ),
            )
            snapshots = iter(
                [
                    {},
                    {
                        Path(ReceiptCompletingAppServer.changed_file): "sha256:after"
                    },
                ]
            )
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 5
            job = jobs.get(started["job"]["jobId"])
            while (
                job["status"] in {"queued", "running"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])
        return job, run

    def test_every_top_maintenance_stage_has_its_own_session_phase(self):
        stage_ids = [
            "question_type",
            "question_intent",
            "correct_choice",
            "law_context",
            "explanation",
            "law_audit",
            "category_setup",
            "question_set",
        ]
        plan = {
            "stagePlans": [
                {
                    "stageId": stage_id,
                    "stageLabel": stage_id,
                    "stageCode": str(index),
                    "sessionGroup": (
                        "maintenance"
                        if index <= 5
                        else "law_audit"
                        if index == 6
                        else "question_set"
                    ),
                    "sessionLabel": (
                        "問題を整備"
                        if index <= 5
                        else "現行法を監査"
                        if index == 6
                        else "問題集を整備"
                    ),
                }
                for index, stage_id in enumerate(stage_ids, start=1)
            ]
        }

        phases = _maintenance_session_phases(plan)

        self.assertEqual([phase["id"] for phase in phases], stage_ids)
        self.assertEqual(
            [phase["sessionGroup"] for phase in phases],
            ["maintenance"] * 5 + ["law_audit", "question_set", "question_set"],
        )

    def test_human_run_records_validated_question_level_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["correct_choice", "explanation"],
                "stageCode": "02a → 03",
                "stageLabel": "複数工程",
                "mode": "outdated",
                "modeLabel": "洗い替え必要のみ",
                "kind": "human",
                "targetCount": 2,
                "workItemCount": 3,
                "targetGroupIds": ["2026"],
                "targetQuestionKeys": ["sample:2026:q01", "sample:2026:q02"],
                "policyTargets": {
                    "correct_choice": ["source-q1"],
                    "explanation": ["source-q1", "source-q2"],
                },
                "progressTargets": [
                    {
                        "id": "ui-q1",
                        "questionKey": "sample:2026:q01",
                        "listGroupId": "2026",
                        "questionLabel": "問1",
                        "bodyPreview": "問題本文1",
                        "aliases": ["source-q1"],
                    },
                    {
                        "id": "ui-q2",
                        "questionKey": "sample:2026:q02",
                        "listGroupId": "2026",
                        "questionLabel": "問2",
                        "bodyPreview": "問題本文2",
                        "aliases": ["source-q2"],
                    },
                ],
                "stagePlans": [
                    {
                        "stageId": "correct_choice",
                        "stageCode": "02a",
                        "stageLabel": "正答精査",
                    },
                    {
                        "stageId": "explanation",
                        "stageCode": "03",
                        "stageLabel": "解説整備",
                    },
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            run = store.create(plan, status="running", prompt="整備する。")
            progress_path = root / run["progressReceiptPath"]
            progress_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {"event": "question_started", "questionId": "source-q1"},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "event": "stage_completed",
                                "questionId": "ui-q1",
                                "stageId": "correct_choice",
                                "result": {
                                    "correctChoiceText": ["正しい", "誤り"],
                                    "privateReasoning": "表示してはいけない",
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "question_completed", "questionId": "ui-q1"},
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "event": "stage_completed",
                                "questionId": "ui-q2",
                                "stageId": "correct_choice",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {"event": "stage_completed", "questionId": "scope外", "stageId": "explanation"},
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            progress = store.progress("sample", run["runId"])
            prompt = store.prompt("sample", run["runId"])

        self.assertEqual(progress["completedQuestionCount"], 1)
        self.assertEqual(progress["completedWorkItemCount"], 1)
        self.assertEqual(progress["percent"], 50)
        self.assertEqual(progress["current"]["questionId"], "ui-q1")
        self.assertEqual(progress["groups"][0]["percent"], 50)
        self.assertEqual(progress["invalidEventCount"], 1)
        self.assertNotIn("privateReasoning", progress["events"][1]["result"])
        self.assertEqual(len(progress["questions"]), 1)
        self.assertTrue(progress["questions"][0]["completed"])
        self.assertEqual(len(progress["questions"][0]["outputs"]), 1)
        self.assertIn("画面用の問題別進捗", prompt)
        self.assertIn("progressTargets", prompt)
        self.assertIn("policyTargets", prompt)

    def test_progress_summarizes_all_58_questions_beyond_recent_event_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            question_ids = [f"q{index}" for index in range(1, 59)]
            plan = {
                "qualification": "sample",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "stageCode": "03",
                "stageLabel": "解説",
                "mode": "outdated",
                "modeLabel": "洗い替え必要のみ",
                "kind": "human",
                "targetCount": 58,
                "workItemCount": 58,
                "targetGroupIds": ["2026"],
                "policyTargets": {"explanation": question_ids},
                "progressTargets": [
                    {
                        "id": question_id,
                        "questionKey": f"sample:2026:{question_id}",
                        "listGroupId": "2026",
                        "questionLabel": f"問{index}",
                        "bodyPreview": f"問題本文{index}",
                        "aliases": [],
                    }
                    for index, question_id in enumerate(question_ids, start=1)
                ],
                "stagePlans": [
                    {
                        "stageId": "explanation",
                        "stageCode": "03",
                        "stageLabel": "解説",
                    }
                ],
                "sourceFiles": [],
                "canonicalDocs": [],
            }
            run = store.create(plan, status="running", prompt="整備する。")
            progress_path = root / run["progressReceiptPath"]
            lines = []
            for index, question_id in enumerate(question_ids, start=1):
                lines.extend(
                    [
                        {"event": "question_started", "questionId": question_id},
                        {
                            "event": "stage_completed",
                            "questionId": question_id,
                            "stageId": "explanation",
                            "result": {"explanationText": f"解説{index}"},
                        },
                        {"event": "question_completed", "questionId": question_id},
                    ]
                )
            progress_path.write_text(
                "\n".join(
                    json.dumps(line, ensure_ascii=False) for line in lines
                )
                + "\n",
                encoding="utf-8",
            )

            progress = store.progress("sample", run["runId"])

        self.assertEqual(len(progress["events"]), 40)
        self.assertEqual(len(progress["questions"]), 58)
        self.assertEqual(progress["questions"][0]["questionLabel"], "問1")
        self.assertEqual(progress["questions"][-1]["questionLabel"], "問58")
        self.assertEqual(
            progress["questions"][-1]["outputs"][0]["result"][
                "explanationText"
            ],
            "解説58",
        )

    def test_progress_receipt_is_not_treated_as_a_maintenance_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = coordinator.store.create(
                FakeWorkflow().plan("sample", "law_audit"),
                status="running",
                prompt="整備する。",
            )
            progress_relative = str(
                (root / run["progressReceiptPath"]).relative_to(root)
            )

            coordinator._validate_changed_files(
                "sample",
                run["runId"],
                coordinator.store.get("sample", run["runId"]),
                (progress_relative,),
                (progress_relative,),
            )

    def test_validated_run_records_only_the_manifest_stage_version(self):
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
            policy = workflow.versioned_policies("new-exam")["question_type"]
            run = {
                "runId": "run-question-type",
                "qualification": "new-exam",
                "targetGroupIds": ["2026"],
                "policyVersions": {
                    "question_type": policy["policyVersion"],
                    "question_intent": workflow.versioned_policies("new-exam")[
                        "question_intent"
                    ]["policyVersion"],
                },
                "policyFingerprints": {
                    "question_type": policy["policyFingerprint"],
                    "question_intent": workflow.versioned_policies("new-exam")[
                        "question_intent"
                    ]["policyFingerprint"],
                },
                "policyTargets": {
                    "question_type": ["new-exam-2026-q1"],
                    "question_intent": [],
                },
            }

            receipt = coordinator._record_work_versions(run)
            item = SourceOnlyInventory().group("new-exam", "2026")["questions"][0]
            status = workflow.work_versions.status_for(
                item,
                workflow.versioned_policies("new-exam").values(),
            )

        self.assertEqual(receipt["recordedCount"], 1)
        by_id = {stage["id"]: stage for stage in status["stages"]}
        self.assertEqual(by_id["question_type"]["status"], "current")
        self.assertEqual(by_id["question_intent"]["status"], "unrecorded")

    def test_explanation_version_recording_rejects_old_legal_style(self):
        with self.assertRaisesRegex(
            QualificationRunError, "03 解説の日本語品質検証"
        ):
            QualificationRunCoordinator._validate_explanation_quality(
                [
                    {
                        "originalQuestionId": "q1",
                        "projected": {
                            "explanationText": [
                                "正しい。ガス事業法第2条第1項は、"
                                "小売供給を定義している。"
                            ]
                        },
                    }
                ]
            )

    def test_explanation_version_recording_rejects_missing_or_opposite_prefix(self):
        for explanation in (
            "定義に一致するため正しい。",
            "間違い。定義に一致する。",
            "正しい。A",
        ):
            with self.subTest(explanation=explanation), self.assertRaisesRegex(
                QualificationRunError, "03 解説の日本語品質検証"
            ):
                QualificationRunCoordinator._validate_explanation_quality(
                    [
                        {
                            "originalQuestionId": "q1",
                            "projected": {
                                "choiceTextList": ["A"],
                                "correctChoiceText": ["正しい"],
                                "explanationText": [explanation],
                            },
                        }
                    ]
                )

    def test_run_policy_drift_blocks_version_recording(self):
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
            policies = workflow.versioned_policies("new-exam")
            question_type = policies["question_type"]
            question_intent = policies["question_intent"]

            with self.assertRaisesRegex(QualificationRunError, "実行中"):
                coordinator._record_work_versions(
                    {
                        "runId": "run-stale",
                        "qualification": "new-exam",
                        "targetGroupIds": ["2026"],
                        "policyVersions": {
                            "question_type": question_type["policyVersion"],
                            "question_intent": question_intent["policyVersion"],
                        },
                        "policyFingerprints": {
                            "question_type": question_type["policyFingerprint"],
                            "question_intent": "stale",
                        },
                        "policyTargets": {
                            "question_type": ["new-exam-2026-q1"],
                            "question_intent": ["new-exam-2026-q1"],
                        },
                    }
                )
            version_path_exists = workflow.work_versions.path_for(
                "new-exam", "2026"
            ).exists()

        self.assertFalse(version_path_exists)

    def test_qualification_stage_writable_roots_are_limited_to_its_patch_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = FakeWorkflow().plan("sample", "question_type")
            plan["stageIds"] = ["question_type"]
            plan["workType"] = "maintenance"
            run = coordinator.store.create(plan, status="queued", prompt="work")

            roots, _created = coordinator._maintenance_writable_roots(
                "sample", run["runId"]
            )

        expected_group = root / "output/sample/questions_json/2026"
        self.assertIn(expected_group / "10_questionType_fixed", roots)
        self.assertIn(expected_group / "99_model_review_flags", roots)
        self.assertNotIn(expected_group / "21_explanationText_added", roots)
        self.assertNotIn(root / "output/sample/category", roots)
        self.assertNotIn(root / "prompt/qualification_docs/sample", roots)

    def test_writable_root_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            outside = root / "outside"
            outside.mkdir()
            patch_root = (
                root
                / "output/sample/questions_json/2026/10_questionType_fixed"
            )
            patch_root.parent.mkdir(parents=True)
            patch_root.symlink_to(outside, target_is_directory=True)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = FakeWorkflow().plan("sample", "question_type")
            plan["stageIds"] = ["question_type"]
            run = coordinator.store.create(plan, status="queued", prompt="work")

            with self.assertRaisesRegex(QualificationRunError, "symlink"):
                coordinator._maintenance_writable_roots(
                    "sample", run["runId"]
                )

    def test_rework_writable_roots_follow_structured_evaluation_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            started = coordinator.start_review(
                {
                    "id": "question-1",
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "stateHash": "state-1",
                    "originalQuestionId": "original-1",
                    "paths": {
                        "source": (
                            "output/sample/questions_json/2026/"
                            "00_source/question_2026_1.json"
                        ),
                        "patches": [],
                    },
                },
                {
                    "reviewId": "review-1",
                    "prompt": "rework",
                    "investigationScope": "current_question",
                    "evaluationSnapshot": {
                        "reworkItems": [
                            {"stage": "03", "message": "fix", "choiceIndexes": [0]}
                        ]
                    },
                },
                work_type="rework",
            )
            run = coordinator.store.get("sample", started["run"]["runId"])
            roots, _created = coordinator._maintenance_writable_roots(
                "sample", run["runId"]
            )

        expected_group = root / "output/sample/questions_json/2026"
        self.assertIn(expected_group / "21_explanationText_added", roots)
        self.assertIn(expected_group / "99_model_review_flags", roots)
        self.assertNotIn(expected_group / "23_correctChoiceText_fixed", roots)
        self.assertNotIn(root / "output/sample/category", roots)
        self.assertEqual(run["policyVersions"], {"explanation": "2.2"})
        self.assertEqual(run["parallelWorkerLimit"], 1)
        self.assertEqual(run["writeWorkerLimit"], 1)

    def test_qualification_law_audit_preserves_trusted_sources_and_record_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            source_files = [
                "output/sample/questions_json/2026/00_source/q1.json",
                "output/sample/questions_json/2026/00_source/q2.json",
            ]
            started = coordinator.start_review(
                {
                    "id": "anchor",
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "stateHash": "state-1",
                    "paths": {"source": source_files[0], "patches": []},
                },
                {
                    "reviewId": "review-1",
                    "prompt": "law audit",
                    "requestKind": "qualification_law_audit",
                    "investigationScope": "qualification",
                    "issueTypes": ["law_audit_metadata_incomplete"],
                    "targetSourceFiles": source_files,
                    "targetRecordAliasGroups": [["q1"], ["q2"]],
                    "targetSourceRecordScopes": {
                        source_files[0]: [["q1"]],
                        source_files[1]: [["q2"]],
                    },
                },
                work_type="maintenance",
            )
            run = coordinator.store.get(
                "sample", started["run"]["runId"]
            )

        self.assertEqual(run["sourceFiles"], source_files)
        self.assertEqual(run["targetRecordAliasGroups"], [["q1"], ["q2"]])
        self.assertEqual(run["parallelWorkerLimit"], 2)
        self.assertEqual(run["targetRecordAliases"], ["q1", "q2"])
        self.assertEqual(run["targetCount"], 2)
        self.assertEqual(run["policyVersions"], {"law_audit": "2.0"})
        expected_record_files = {
            path
            for path in [*run["allowedPatchFiles"], *run["allowedWriteFiles"]]
            if Path(path).suffix in {".json", ".jsonl"}
        }
        self.assertEqual(set(run["targetRecordScopes"]), expected_record_files)
        self.assertEqual(
            run["targetRecordScopes"][
                "output/sample/review/law_revision_audit/"
                "2026_law_revision_audit.jsonl"
            ],
            [["q1"], ["q2"]],
        )
        self.assertTrue(
            any("18_law_context_prepared/q1_" in path for path in run["allowedPatchFiles"])
        )
        self.assertTrue(
            any("23_correctChoiceText_fixed/q2_" in path for path in run["allowedPatchFiles"])
        )
        self.assertFalse(
            any("_explanationText_needs_5_5_high_review" in path for path in run["allowedPatchFiles"])
        )
        self.assertTrue(
            any("_lawRevision_needs_5_5_high_review" in path for path in run["allowedPatchFiles"])
        )

    def test_current_question_law_hold_scopes_every_allowed_record_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            started = coordinator.start_review(
                {
                    "id": "question-1",
                    "qualification": "sample",
                    "listGroupId": "2026",
                    "stateHash": "state-1",
                    "originalQuestionId": "original-1",
                    "sourceQuestionKey": "sample:2026:q1",
                    "paths": {
                        "source": (
                            "output/sample/questions_json/2026/"
                            "00_source/question_2026_1.json"
                        ),
                        "patches": [],
                    },
                },
                {
                    "reviewId": "review-1",
                    "prompt": "law hold review",
                    "investigationScope": "current_question",
                    "issueTypes": ["law_hold"],
                    "fields": ["lawReferences", "lawRevisionFacts"],
                },
                work_type="maintenance",
            )
            run = coordinator.store.get(
                "sample", started["run"]["runId"]
            )

        expected_record_files = {
            path
            for path in [*run["allowedPatchFiles"], *run["allowedWriteFiles"]]
            if Path(path).suffix in {".json", ".jsonl"}
        }
        self.assertEqual(set(run["targetRecordScopes"]), expected_record_files)
        self.assertTrue(
            any(
                "_lawRevision_needs_5_5_high_review" in path
                for path in run["targetRecordScopes"]
            )
        )
        self.assertTrue(
            any("21_explanationText_added" in path for path in run["allowedPatchFiles"])
        )

    def test_post_fix_law_fields_preserve_law_audit_contract_and_failed_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                DeferredJobs(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            question = {
                "id": "question-1",
                "qualification": "sample",
                "listGroupId": "2026",
                "stateHash": "state-1",
                "originalQuestionId": "original-1",
                "sourceQuestionKey": "sample:2026:q1",
                "paths": {
                    "source": (
                        "output/sample/questions_json/2026/"
                        "00_source/question_2026_1.json"
                    ),
                    "patches": [],
                },
            }
            failed = coordinator.start_review(
                question,
                {
                    "reviewId": "review-failed",
                    "prompt": "law hold review",
                    "investigationScope": "current_question",
                    "issueTypes": ["law_hold"],
                    "fields": ["lawReferences", "lawRevisionFacts"],
                },
                work_type="maintenance",
            )["run"]
            failed_path = next(
                path
                for path in failed["allowedWriteFiles"]
                if path.endswith("_law_revision_audit.jsonl")
            )
            coordinator.store.update(
                "sample",
                failed["runId"],
                status="failed",
                result={
                    "status": "failed",
                    "changedFiles": [failed_path],
                    "resolvedFailedDeltaPaths": [],
                },
            )

            retried = coordinator.start_review(
                question,
                {
                    "reviewId": "review-retry",
                    "prompt": "post-fix law review",
                    "investigationScope": "current_question",
                    "issueTypes": ["post_fix_review"],
                    "fields": ["lawReferences", "lawRevisionFacts"],
                },
                work_type="maintenance",
            )["run"]

        self.assertEqual(retried["policyVersions"], {"law_audit": "2.0"})
        self.assertEqual(retried["allowedWriteAreas"], ["review"])
        self.assertIn(failed_path, retried["allowedWriteFiles"])
        self.assertIn(failed_path, retried["resolvableFailedDeltaPaths"])
        self.assertTrue(
            any(
                "_lawRevision_needs_5_5_high_review" in path
                for path in retried["allowedPatchFiles"]
            )
        )

    def test_question_review_contract_limits_fields_and_patch_files(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            question = {
                "paths": {
                    "source": (
                        "output/sample/questions_json/2026/"
                        "00_source/question_2026_1.json"
                    ),
                    "patches": [
                        "output/sample/questions_json/2026/"
                        "21_explanationText_added/existing.json"
                    ],
                }
            }

            patch_dirs, write_areas, patch_files, write_files = (
                coordinator._review_write_contract(
                    question,
                    {
                        "fields": ["explanationText"],
                        "investigationScope": "current_question",
                    },
                )
            )

        self.assertEqual(
            patch_dirs,
            {"21_explanationText_added", "99_model_review_flags"},
        )
        self.assertEqual(write_areas, set())
        self.assertEqual(write_files, set())
        self.assertIn(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/question_2026_1_merged_explanationText_added.json",
            patch_files,
        )
        self.assertNotIn(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/existing.json",
            patch_files,
        )
        self.assertFalse(
            any("23_correctChoiceText_fixed" in path for path in patch_files)
        )

    def test_question_review_without_a_bounded_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            with self.assertRaisesRegex(QualificationRunError, "field"):
                coordinator._review_write_contract(
                    {"paths": {}},
                    {
                        "issueTypes": ["other"],
                        "investigationScope": "current_question",
                    },
                )

    def test_question_body_and_choices_require_the_dedicated_correction_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            question = {
                "paths": {
                    "source": (
                        "output/sample/questions_json/2026/"
                        "00_source/question_2026_1.json"
                    )
                }
            }
            reviews = (
                {"fields": ["questionBodyText"]},
                {"selection": {"fields": ["choiceTextList"]}},
                {"fields": ["explanationText", "questionBodyText"]},
            )

            for review in reviews:
                with self.subTest(review=review), self.assertRaisesRegex(
                    QualificationRunError, "自動整備対象外"
                ):
                    coordinator._review_write_contract(
                        question,
                        {**review, "investigationScope": "current_question"},
                    )

    def test_target_files_cannot_enable_question_issue_corrections(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            with self.assertRaisesRegex(
                QualificationRunError, "専用workflow"
            ):
                coordinator._review_write_contract(
                    {
                        "paths": {
                            "source": (
                                "output/sample/questions_json/2026/"
                                "00_source/question_2026_1.json"
                            )
                        }
                    },
                    {
                        "fields": ["explanationText"],
                        "targetFiles": [
                            "output/sample/questions_json/2026/"
                            "24_questionIssueCorrections/crafted.json"
                        ],
                        "investigationScope": "current_question",
                    },
                )

    def test_question_type_still_uses_only_its_patch_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            patch_dirs, _areas, patch_files, _write_files = (
                coordinator._review_write_contract(
                    {
                        "paths": {
                            "source": (
                                "output/sample/questions_json/2026/"
                                "00_source/question_2026_1.json"
                            )
                        }
                    },
                    {
                        "fields": ["questionType"],
                        "investigationScope": "current_question",
                    },
                )
            )

        self.assertEqual(
            patch_dirs, {"10_questionType_fixed", "99_model_review_flags"}
        )
        self.assertFalse(
            any("24_questionIssueCorrections" in path for path in patch_files)
        )
        self.assertTrue(
            any("_questionType_needs_5_5_high_review.jsonl" in path for path in patch_files)
        )
        self.assertFalse(
            any("_explanationText_needs_5_5_high_review.jsonl" in path for path in patch_files)
        )
        self.assertFalse(
            any("_lawRevision_needs_5_5_high_review.jsonl" in path for path in patch_files)
        )

    def test_category_setup_has_only_exact_non_patch_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = {
                "qualification": "sample",
                "stageId": "category_setup",
                "stageIds": ["category_setup"],
                "sourceFiles": [
                    "output/sample/questions_json/2026/00_source/q1.json"
                ],
                "outputFiles": [
                    "output/sample/category/category.json",
                    "prompt/qualification_docs/sample/03_category_preparation.md",
                ],
            }

            coordinator._apply_plan_write_contract(plan)

        self.assertEqual(plan["allowedPatchDirs"], [])
        self.assertEqual(plan["allowedPatchFiles"], [])
        self.assertEqual(
            plan["allowedWriteAreas"], ["category", "qualification_docs"]
        )
        self.assertEqual(
            plan["allowedWriteFiles"], sorted(plan["outputFiles"])
        )

    def test_category_setup_cannot_complete_without_valid_category(self):
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
            run = {
                "qualification": "new-exam",
                "stageId": "category_setup",
                "stageIds": ["category_setup"],
                "targetGroupIds": ["2026"],
                "policyVersions": {},
            }

            with self.assertRaisesRegex(
                QualificationRunError,
                "category.json",
            ):
                coordinator._record_work_versions(run)

    def test_multi_stage_contract_does_not_cross_product_sources_and_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            q1 = "output/sample/questions_json/2026/00_source/q1.json"
            q2 = "output/sample/questions_json/2026/00_source/q2.json"
            plan = {
                "qualification": "sample",
                "stageId": "multi",
                "stageIds": ["question_type", "explanation"],
                "stagePlans": [
                    {
                        "stageId": "question_type",
                        "stageIds": ["question_type"],
                        "sourceFiles": [q1],
                        "outputFiles": [],
                        "targetGroupIds": ["2026"],
                    },
                    {
                        "stageId": "explanation",
                        "stageIds": ["explanation"],
                        "sourceFiles": [q2],
                        "outputFiles": [],
                        "targetGroupIds": ["2026"],
                    },
                ],
            }

            coordinator._apply_plan_write_contract(plan)

        joined = "\n".join(plan["allowedPatchFiles"])
        self.assertIn("10_questionType_fixed/q1_questionType_fixed.json", joined)
        self.assertIn("21_explanationText_added/q2_merged_explanationText_added.json", joined)
        self.assertNotIn("21_explanationText_added/q1_", joined)
        self.assertNotIn("10_questionType_fixed/q2_", joined)
        self.assertIn("q1_questionType_needs_5_5_high_review.jsonl", joined)
        self.assertIn("q2_explanationText_needs_5_5_high_review.jsonl", joined)
        self.assertNotIn("q1_explanationText_needs_5_5_high_review.jsonl", joined)

    def test_year_scoped_law_plan_allows_only_its_review_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = {
                "qualification": "sample",
                "stageId": "law_audit",
                "stageIds": ["law_audit"],
                "sourceFiles": [
                    "output/sample/questions_json/2026/00_source/q1.json"
                ],
                "outputFiles": [],
                "targetGroupIds": ["2026"],
            }
            coordinator._apply_plan_write_contract(plan)
            run = {**plan, "runId": "run-1"}
            roots = coordinator._maintenance_root_candidates(
                "sample", "run-1", run
            )

            allowed = coordinator._maintenance_path_allowed_for_run(
                Path(
                    "output/sample/review/law_revision_audit/"
                    "2026_law_revision_audit.jsonl"
                ),
                roots,
                run,
            )
            rejected = coordinator._maintenance_path_allowed_for_run(
                Path(
                    "output/sample/review/law_revision_audit/"
                    "2025_law_revision_audit.jsonl"
                ),
                roots,
                run,
            )

        self.assertEqual(plan["allowedWriteAreas"], ["review"])
        self.assertNotIn("law_evidence", plan["allowedWriteAreas"])
        self.assertNotIn("reports", plan["allowedWriteAreas"])
        self.assertTrue(allowed)
        self.assertFalse(rejected)

    def test_current_question_law_sidecar_is_an_exact_file_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            question = {
                "qualification": "sample",
                "listGroupId": "2026",
                "paths": {
                    "source": (
                        "output/sample/questions_json/2026/"
                        "00_source/question_2026_1.json"
                    )
                },
            }
            patch_dirs, write_areas, patch_files, write_files = (
                coordinator._review_write_contract(
                    question,
                    {
                        "fields": ["lawRevisionFacts"],
                        "issueTypes": ["law_audit_metadata_incomplete"],
                        "investigationScope": "current_question",
                    },
                )
            )
            run = {
                "qualification": "sample",
                "stageIds": ["maintenance"],
                "targetGroupIds": ["2026"],
                "allowedPatchDirs": sorted(patch_dirs),
                "allowedPatchFiles": sorted(patch_files),
                "allowedWriteAreas": sorted(write_areas),
                "allowedWriteFiles": sorted(write_files),
            }
            roots = coordinator._maintenance_root_candidates(
                "sample", "run-1", run
            )

            target_allowed = coordinator._maintenance_path_allowed_for_run(
                Path(next(iter(write_files))), roots, run
            )
            other_group_allowed = coordinator._maintenance_path_allowed_for_run(
                Path(
                    "output/sample/review/law_revision_audit/"
                    "2025_law_revision_audit.jsonl"
                ),
                roots,
                run,
            )

        self.assertEqual(write_areas, {"review"})
        self.assertTrue(target_allowed)
        self.assertFalse(other_group_allowed)

    def test_current_question_prefers_the_selected_timestamp_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_root = (
                root
                / "output/sample/questions_json/2026/21_explanationText_added"
            )
            patch_root.mkdir(parents=True)
            fixed = patch_root / "question_2026_1_merged_explanationText_added.json"
            latest = (
                patch_root
                / "question_2026_1_merged_explanationText_added_20260714_1200.json"
            )
            fixed.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            _dirs, _areas, patch_files, _write_files = (
                coordinator._review_write_contract(
                    {
                        "paths": {
                            "source": (
                                "output/sample/questions_json/2026/"
                                "00_source/question_2026_1.json"
                            ),
                            "patches": [],
                        }
                    },
                    {
                        "fields": ["explanationText"],
                        "investigationScope": "current_question",
                    },
                )
            )

        self.assertIn(str(latest.relative_to(root)), patch_files)
        self.assertNotIn(str(fixed.relative_to(root)), patch_files)

    def test_qualification_plan_rejects_an_unplanned_same_stage_file(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = coordinator._plan(
                "sample", "law_audit", "remaining", None
            )
            run = {**plan, "runId": "run-1"}
            roots = coordinator._maintenance_root_candidates(
                "sample", "run-1", run
            )

            planned = coordinator._maintenance_path_allowed_for_run(
                Path(plan["allowedPatchFiles"][0]), roots, run
            )
            unplanned = coordinator._maintenance_path_allowed_for_run(
                Path(
                    "output/sample/questions_json/2026/"
                    "21_explanationText_added/unplanned.json"
                ),
                roots,
                run,
            )

        self.assertTrue(planned)
        self.assertFalse(unplanned)

    def test_qualification_plan_exposes_only_resolvable_failed_delta_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            planned = (
                "output/sample/questions_json/2026/"
                "21_explanationText_added/patch.json"
            )
            unplanned = (
                "output/sample/questions_json/2026/"
                "18_law_context_prepared/law.json"
            )
            manifest = (
                root
                / "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json"
            )
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "qualification": "sample",
                        "status": "failed",
                        "workType": "maintenance",
                        "stageIds": ["explanation"],
                        "policyVersions": {"explanation": "1.0"},
                        "targetGroupIds": ["2026"],
                        "allowedPatchDirs": [
                            "18_law_context_prepared",
                            "21_explanationText_added",
                        ],
                        "allowedWriteAreas": [],
                        "allowedPatchFiles": [planned, unplanned],
                        "allowedWriteFiles": [],
                        "targetRecordScopes": {
                            planned: [["q1"]],
                            unplanned: [["q1"]],
                        },
                        "result": {"changedFiles": [planned, unplanned]},
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
            )
            plan = {
                "qualification": "sample",
                "workType": "maintenance",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "policyVersions": {"explanation": "1.0"},
                "targetGroupIds": ["2026"],
                "allowedPatchDirs": ["21_explanationText_added"],
                "allowedWriteAreas": [],
                "allowedPatchFiles": [planned],
                "allowedWriteFiles": [],
                "targetRecordScopes": {planned: [["q1"]]},
            }
            plan["resolvableFailedDeltaPaths"] = (
                coordinator._resolvable_for_plan(
                    "sample",
                    ["2026"],
                    plan,
                )
            )

            self.assertEqual(plan["resolvableFailedDeltaPaths"], [planned])
            run = {
                **plan,
                "result": {
                    "changedFiles": [],
                    "resolvedFailedDeltaPaths": [planned],
                },
            }
            coordinator._validate_changed_files(
                "sample", "run-1", run, (), ()
            )

    def test_record_scope_rejects_a_different_question_in_aggregate_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/aggregate.json"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"original_question_id": "q1", "value": 1},
                            {"original_question_id": "q2", "value": 1},
                        ]
                    }
                ),
                encoding="utf-8",
            )
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
            path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"original_question_id": "q1", "value": 1},
                            {"original_question_id": "q2", "value": 2},
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
                QualificationRunError, "対象問題以外"
            ):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {relative},
                )

    def test_record_scope_allows_only_the_target_record_to_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/aggregate.json"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            before = {
                "question_bodies": [
                    {"original_question_id": "q1", "value": 1},
                    {"original_question_id": "q2", "value": 1},
                ]
            }
            path.write_text(json.dumps(before), encoding="utf-8")
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
            before["question_bodies"][0]["value"] = 2
            path.write_text(json.dumps(before), encoding="utf-8")
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

    def test_record_scope_rejects_target_record_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path(
                "output/sample/questions_json/2026/"
                "21_explanationText_added/aggregate.json"
            )
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"originalQuestionId": "q1", "value": 1},
                            {"originalQuestionId": "q2", "value": 1},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan.update(
                {
                    "targetRecordAliasGroups": [["q1"]],
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
            path.write_text(
                json.dumps(
                    {
                        "question_bodies": [
                            {"originalQuestionId": "q2", "value": 1}
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

            with self.assertRaisesRegex(QualificationRunError, "record削除"):
                coordinator._validate_record_scope(
                    "sample",
                    run["runId"],
                    store.get("sample", run["runId"]),
                    {relative},
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

    def test_record_scope_is_file_specific_across_year_sidecars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_relative = Path(
                "output/sample/review/law_revision_audit/"
                "2025_law_revision_audit.jsonl"
            )
            second_relative = Path(
                "output/sample/review/law_revision_audit/"
                "2026_law_revision_audit.jsonl"
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

    def test_machine_preview_does_not_require_app_server_binary(self):
        class MissingAppServer:
            configured = False
            provider = "Codex App Server"

        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                app_server=MissingAppServer(),
            )

            machine = coordinator.preview("sample", "delivery", "remaining")
            human = coordinator.preview("sample", "law_audit", "remaining")

        self.assertTrue(machine["canStart"])
        self.assertFalse(human["canStart"])

    def test_rejects_changed_files_outside_maintenance_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )

            with self.assertRaises(QualificationRunError):
                coordinator._validate_changed_files(
                    "sample",
                    "run-1",
                    {
                        "stageId": "law_audit",
                        "stageIds": ["law_audit"],
                        "targetGroupIds": ["2026"],
                        "result": {
                            "changedFiles": ["tools/question_review_console/server.py"]
                        }
                    },
                    (),
                )

            with self.assertRaises(QualificationRunError):
                coordinator._validate_changed_files(
                    "sample",
                    "run-1",
                    {"result": {"changedFiles": []}},
                    (),
                    ("document/operations/local_question_review_console.md",),
                )

    def test_human_run_executes_in_fresh_app_server_thread_and_records_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = SuccessfulAppServer(
                (
                    "output/sample/questions_json/2026/"
                    "21_explanationText_added/patch.json",
                ),
                temporary_helper=True,
            )
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            snapshots = iter(
                [
                    {},
                    {
                        Path(
                            "output/sample/questions_json/2026/"
                            "21_explanationText_added/patch.json"
                        ): "sha256:after"
                    },
                ]
            )
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertIsNone(started["prompt"])
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["workType"], "maintenance")
        self.assertEqual(run["sandbox"], "workspace-write")
        self.assertEqual(run["threadId"], "thread-maintenance-1")
        self.assertEqual(run["sessionId"], "session-maintenance-1")
        self.assertEqual(run["turnId"], "turn-maintenance-1")
        self.assertEqual(run["model"], "gpt-test")
        self.assertIsNone(run["serviceTier"])
        self.assertEqual(run["reasoningEffort"], "high")
        self.assertEqual(run["parallelStrategy"], "read_only_research")
        self.assertEqual(run["parallelWorkerLimit"], 2)
        self.assertEqual(run["writeWorkerLimit"], 1)
        self.assertEqual(run["researchStatus"], "succeeded")
        self.assertEqual(run["researchThreadId"], "thread-research-1")
        self.assertEqual(run["researchSessionId"], "session-research-1")
        self.assertEqual(run["researchTurnId"], "turn-research-1")
        self.assertEqual(run["researchModel"], "gpt-research-test")
        self.assertEqual(run["researchSubagentCount"], 2)
        self.assertEqual(
            run["researchSubagentThreadIds"], ["subagent-1", "subagent-2"]
        )
        self.assertEqual(len(app_server.calls), 2)
        research_prompt, research_kwargs = app_server.calls[0]
        writer_prompt, writer_kwargs = app_server.calls[1]
        self.assertEqual(research_kwargs["work_type"], "maintenance_research")
        self.assertEqual(research_kwargs["sandbox"], "read-only")
        self.assertNotIn("writable_roots", research_kwargs)
        self.assertIn("read-only並列調査", research_prompt)
        self.assertNotIn("画面用の問題別進捗", research_prompt)
        self.assertNotIn("完了時に検証結果を次へJSONで保存", research_prompt)
        self.assertEqual(writer_kwargs["work_type"], "maintenance")
        self.assertEqual(writer_kwargs["sandbox"], "workspace-write")
        self.assertIn("問題IDごとの調査案", writer_prompt)
        self.assertIn(str((root / ".venv/bin/python").resolve()), writer_prompt)
        self.assertIn("成功ならpass、失敗ならfail", writer_prompt)
        self.assertIn("独自の代替検証だけで成功扱いにせず", writer_prompt)
        self.assertIn("result.jsonを最後のfile操作", writer_prompt)
        self.assertEqual(app_server.kwargs["work_type"], "maintenance")
        self.assertEqual(synchronizer.calls, [("sample", "2026", True)])
        self.assertEqual(run["artifactSync"]["status"], "succeeded")
        self.assertNotEqual(app_server.kwargs["cwd"], root)
        self.assertTrue(app_server.kwargs["writable_roots"])
        self.assertTrue(
            all(
                path.resolve().is_relative_to(root.resolve())
                for path in app_server.kwargs["writable_roots"]
            )
        )
        run_dir = (
            root / "output/question_review_console/workflow_runs/sample" / run["runId"]
        ).resolve()
        self.assertIn(
            run_dir / "agent_output",
            app_server.kwargs["writable_roots"],
        )
        self.assertNotIn(run_dir, app_server.kwargs["writable_roots"])

    def test_success_receipt_can_finish_writer_before_turn_final_answer(self):
        job, run = self._run_receipt_completion(mutate_after_probe=False)

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["turnCompletionMode"], "receipt_interrupted")

    def test_success_receipt_survives_concurrent_manifest_refresh(self):
        job, run = self._run_receipt_completion(
            mutate_after_probe=False,
            clobber_manifest_after_probe=True,
        )

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["turnCompletionMode"], "receipt_interrupted")

    def test_change_after_success_receipt_is_not_accepted(self):
        job, run = self._run_receipt_completion(mutate_after_probe=True)

        self.assertEqual(job["status"], "failed")
        self.assertIn("成功receiptの保存後にfile変更", job["error"])
        self.assertEqual(run["status"], "failed")

    def test_failed_receipt_keeps_summary_and_first_failed_command(self):
        receipt = {
            "status": "failed",
            "summary": "工程別検証は成功したが、全体検証に失敗した。",
            "commands": [
                {"command": "python scoped_check.py", "status": "pass"},
                {"command": "python quality_gate.py", "status": "fail"},
                {"command": "python later_check.py", "status": "fail"},
            ],
            "changedFiles": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=SuccessfulAppServer(receipt=receipt),
            )
            snapshots = iter([{}, {}])
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started["job"]["jobId"])
            while (
                job["status"] in {"queued", "running"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertEqual(run["status"], "failed")
        self.assertIsNone(run["receiptError"])
        self.assertEqual(run["result"]["summary"], receipt["summary"])
        self.assertEqual(run["result"]["commands"], receipt["commands"])
        self.assertIn(receipt["summary"], run["error"])
        self.assertIn("python quality_gate.py", run["error"])
        self.assertNotIn("python later_check.py", run["error"])
        self.assertNotIn("有効な成功receipt", run["error"])

    def test_human_run_succeeds_with_warning_when_automatic_sync_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changed_file = (
                "output/sample/questions_json/2026/"
                "21_explanationText_added/patch.json"
            )
            app_server = SuccessfulAppServer((changed_file,))
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            synchronizer.can_sync = False
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            snapshots = iter(
                [
                    {},
                    {Path(changed_file): "sha256:after"},
                ]
            )
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "succeeded")
        self.assertTrue(job["result"]["warning"])
        self.assertEqual(job["result"]["artifactSync"]["status"], "blocked")
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["artifactSync"]["status"], "blocked")
        self.assertEqual(synchronizer.calls, [])

    def test_change_notifications_allow_only_turn_workspace_or_repository(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as turn_directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            root = Path(directory)
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            turn_workspace = Path(turn_directory)
            outside = Path(outside_directory)
            repository_file = root / "output/sample/questions_json/2026/patch.json"

            persistent = coordinator._repository_change_notifications(
                (
                    str(turn_workspace / "helper.py"),
                    "relative-helper.py",
                    str(repository_file),
                ),
                transient_root=turn_workspace,
            )

            self.assertEqual(
                persistent,
                ("output/sample/questions_json/2026/patch.json",),
            )
            with self.assertRaisesRegex(
                QualificationRunError, "repository外のfile変更"
            ):
                coordinator._repository_change_notifications(
                    (str(outside / "file.json"),),
                    transient_root=turn_workspace,
                )
            (turn_workspace / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                QualificationRunError, "repository外のfile変更"
            ):
                coordinator._repository_change_notifications(
                    ("escape/file.json",),
                    transient_root=turn_workspace,
                )

    def test_path_fingerprint_ignores_ctime_only_cloud_hydration(self):
        class StubPath:
            def __init__(self, *, size=10, mtime_ns=100, ctime_ns=200):
                self._stat = type(
                    "StubStat",
                    (),
                    {
                        "st_mode": 0o100644,
                        "st_size": size,
                        "st_mtime_ns": mtime_ns,
                        "st_ctime_ns": ctime_ns,
                    },
                )()

            def lstat(self):
                return self._stat

            def is_symlink(self):
                return False

        original = QualificationRunCoordinator._path_fingerprint(
            StubPath(ctime_ns=200)
        )
        hydrated = QualificationRunCoordinator._path_fingerprint(
            StubPath(ctime_ns=300)
        )
        modified = QualificationRunCoordinator._path_fingerprint(
            StubPath(mtime_ns=101, ctime_ns=300)
        )

        self.assertEqual(original, hydrated)
        self.assertNotEqual(original, modified)

    def test_version_recording_failure_never_marks_the_receipt_as_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=SuccessfulAppServer(),
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}

            def fail_version_recording(_run):
                raise QualificationRunError("version recording failed")

            coordinator._record_work_versions = fail_version_recording
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertEqual(run["status"], "failed")
        self.assertFalse(run["receiptValidated"])
        self.assertIsNone(run["workVersionReceipt"])
        self.assertIsNotNone(run["finishedAt"])

    def test_success_receipt_must_match_the_actual_final_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            with self.assertRaisesRegex(
                QualificationRunError, "実際の最終差分にない"
            ):
                coordinator._validate_changed_files(
                    "sample",
                    "run-1",
                    {
                        "stageId": "law_audit",
                        "stageIds": ["law_audit"],
                        "targetGroupIds": ["2026"],
                        "result": {
                            "changedFiles": [
                                "output/sample/questions_json/2026/"
                                "21_explanationText_added/patch.json"
                            ]
                        }
                    },
                    (),
                    (),
                )

    def test_agent_output_rejects_files_other_than_the_result_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            extra = (
                "output/question_review_console/workflow_runs/sample/"
                "run-1/agent_output/notes.json"
            )

            with self.assertRaisesRegex(QualificationRunError, "result.json以外"):
                coordinator._validate_changed_files(
                    "sample",
                    "run-1",
                    {
                        "stageId": "law_audit",
                        "stageIds": ["law_audit"],
                        "targetGroupIds": ["2026"],
                        "result": {"changedFiles": [extra]},
                    },
                    (extra,),
                    (extra,),
                )

    def test_success_receipt_can_resolve_only_a_preexisting_failed_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            path = (
                "output/sample/questions_json/2026/"
                "21_explanationText_added/verified.json"
            )
            run = {
                "stageId": "law_audit",
                "stageIds": ["law_audit"],
                "targetGroupIds": ["2026"],
                "resolvableFailedDeltaPaths": [path],
                "result": {
                    "changedFiles": [],
                    "resolvedFailedDeltaPaths": [path],
                },
            }

            coordinator._validate_changed_files(
                "sample", "run-1", run, (), ()
            )

            run["resolvableFailedDeltaPaths"] = []
            with self.assertRaisesRegex(
                QualificationRunError, "未確定でなかった"
            ):
                coordinator._validate_changed_files(
                    "sample", "run-1", run, (), ()
                )

    def test_success_receipt_cannot_resolve_a_path_outside_its_write_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            planned = (
                "output/sample/questions_json/2026/"
                "21_explanationText_added/patch.json"
            )
            outside = (
                "output/sample/questions_json/2026/"
                "18_law_context_prepared/law.json"
            )
            run = {
                "qualification": "sample",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "targetGroupIds": ["2026"],
                "allowedPatchDirs": ["21_explanationText_added"],
                "allowedPatchFiles": [planned],
                "resolvableFailedDeltaPaths": [outside],
                "result": {
                    "changedFiles": [],
                    "resolvedFailedDeltaPaths": [outside],
                },
            }

            with self.assertRaisesRegex(
                QualificationRunError, "整備責務外の未確定差分"
            ):
                coordinator._validate_changed_files(
                    "sample", "run-1", run, (), ()
                )

    def test_success_receipt_can_explicitly_resolve_unknown_delta_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            sentinel = (
                "output/question_review_console/workflow_runs/sample/"
                "20260101-run/manifest.json"
            )
            run = {
                "qualification": "sample",
                "stageId": "explanation",
                "stageIds": ["explanation"],
                "targetGroupIds": ["2026"],
                "resolvableFailedDeltaPaths": [sentinel],
                "result": {
                    "changedFiles": [],
                    "resolvedFailedDeltaPaths": [sentinel],
                },
            }

            coordinator._validate_changed_files(
                "sample", "run-1", run, (), ()
            )

    def test_failed_turn_still_records_the_actual_repository_diff(self):
        changed_path = Path(
            "output/sample/questions_json/2026/"
            "21_explanationText_added/patch.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=FailingAppServer(),
            )
            snapshots = iter([{}, {changed_path: "sha256:partial"}])
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertIn("turn crashed", job["error"])
        self.assertIn(str(changed_path), job["error"])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["result"]["changedFiles"], [str(changed_path)])
        self.assertEqual(run["threadId"], "thread-failed-1")
        self.assertEqual(run["turnId"], "turn-failed-1")

    def test_unsafe_failed_turn_receipt_excludes_progress_file(self):
        unsafe_path = Path("tools/question_review_console/unsafe.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = JobManager()
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                FakeSynchronizer(),
                jobs,
                "secret",
                app_server=FailingAppServer(),
            )
            snapshot_count = 0

            def snapshots(qualification, run_id):
                nonlocal snapshot_count
                snapshot_count += 1
                if snapshot_count == 1:
                    return {}
                progress_path = Path(
                    "output",
                    "question_review_console",
                    "workflow_runs",
                    qualification,
                    run_id,
                    "agent_output",
                    "progress.jsonl",
                )
                return {
                    progress_path: "sha256:progress",
                    unsafe_path: "sha256:unsafe",
                }

            coordinator._repository_file_fingerprints = snapshots
            preview = coordinator.preview("sample", "law_audit", "remaining")
            started = coordinator.start(
                "sample", "law_audit", "remaining", preview["previewToken"]
            )
            deadline = time.monotonic() + 2
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.refresh("sample", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertIn("整備責務外", job["error"])
        self.assertEqual(run["result"]["changedFiles"], [str(unsafe_path)])

    def test_review_qualification_scope_investigates_broadly_but_writes_anchor_group(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = FakeWorkflow()
            workflow.inventory = MultiGroupSourceInventory()
            coordinator = QualificationRunCoordinator(
                Path(directory),
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            groups = coordinator._review_target_group_ids(
                {
                    "qualification": "new-exam",
                    "listGroupId": "2026",
                },
                {"investigationScope": "qualification"},
            )

        self.assertEqual(groups, ["2026"])

    def test_review_cannot_write_across_qualifications_in_one_session(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            with self.assertRaisesRegex(QualificationRunError, "1資格ずつ"):
                coordinator._review_target_group_ids(
                    {"qualification": "sample", "listGroupId": "2026"},
                    {"investigationScope": "all_qualifications"},
                )

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
        self.assertEqual(preview["outputFileCount"], 3)
        self.assertEqual(started["run"]["stageId"], "setup")
        self.assertIn("qualification_docs/new-exam", started["prompt"])
        self.assertIn("## 完了記録", started["prompt"])
        self.assertIn("result.json", started["prompt"])
        self.assertNotIn("## 問題文", started["prompt"])

    def test_multi_stage_year_refresh_is_saved_as_one_human_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, MultiGroupSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            stage_ids = ["question_type", "question_intent", "correct_choice"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "group_refresh",
                stage_ids=stage_ids,
                list_group_ids=["2025", "2026"],
            )
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "group_refresh",
                preview["previewToken"],
                stage_ids=stage_ids,
                list_group_ids=["2025", "2026"],
            )

        self.assertEqual(preview["stageId"], "multi")
        self.assertEqual(preview["stageIds"], stage_ids)
        self.assertEqual(preview["stageCount"], 3)
        self.assertEqual(preview["targetCount"], 2)
        self.assertEqual(preview["workItemCount"], 6)
        self.assertEqual(preview["targetGroupIds"], ["2025", "2026"])
        self.assertEqual(preview["scopeListGroupIds"], ["2025", "2026"])
        self.assertEqual(started["run"]["stageIds"], stage_ids)
        self.assertEqual(started["run"]["workItemCount"], 6)
        self.assertIsNone(started["run"]["scopeListGroupId"])
        self.assertEqual(started["run"]["scopeListGroupIds"], ["2025", "2026"])
        self.assertIn("対象listGroupId: `2025`, `2026`", started["prompt"])
        self.assertIn("一問を読み、その問題について選択工程", started["prompt"])

    def test_top_maintenance_uses_fresh_writer_sessions_for_separate_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = []

            def write_law_sidecar(work_type):
                if work_type != "maintenance_law_audit":
                    return
                self._write_law_audit_sidecar(
                    root,
                    "2026",
                    [
                        {
                            "reviewQuestionId": "new-exam-2026-q1",
                            "isLawRelated": True,
                            "auditStatus": "same_as_current",
                            "reviewState": "secondary_verified",
                            "lawReferences": [
                                [
                                    {
                                        "lawTitle": "ガス事業法",
                                        "lawId": "329AC0000000051",
                                        "article": "2",
                                        "verificationStatus": "verified",
                                    }
                                ]
                            ],
                        }
                    ],
                )

            app_server = FlowAppServer(
                events=events,
                before_receipt=write_law_sidecar,
            )
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            original_merge = synchronizer.refresh_merged_views

            def refresh_merged_views(qualification, list_group_id, emit):
                events.append("merge")
                return original_merge(qualification, list_group_id, emit)

            synchronizer.refresh_merged_views = refresh_merged_views
            original_sync = synchronizer.run

            def run_sync(qualification, list_group_id, token, emit, *, force=False):
                events.append("final-sync")
                return original_sync(
                    qualification, list_group_id, token, emit, force=force
                )

            synchronizer.run = run_sync
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            stage_ids = ["question_type", "law_audit"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "outdated",
                stage_ids=stage_ids,
                list_group_ids=["2026"],
            )
            self.assertEqual(preview["scopeListGroupIds"], ["2026"])
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            deadline = time.monotonic() + 3
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])
            recent = coordinator.recent("new-exam")

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["workType"], "maintenance_flow")
        self.assertEqual(
            [item["id"] for item in run["phaseExecutions"]],
            ["question_type", "law_audit"],
        )
        self.assertTrue(
            all(item["status"] == "succeeded" for item in run["phaseExecutions"])
        )
        self.assertEqual(len(run["childRunIds"]), 2)
        sessions = {item["sessionId"] for item in run["phaseExecutions"]}
        threads = {item["threadId"] for item in run["phaseExecutions"]}
        self.assertEqual(len(sessions), 2)
        self.assertEqual(len(threads), 2)
        self.assertEqual(
            [kwargs["work_type"] for _, kwargs in app_server.calls],
            ["maintenance_question_type", "maintenance_law_audit"],
        )
        self.assertEqual(synchronizer.merge_calls, [("new-exam", "2026")])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])
        self.assertEqual(
            events,
            [
                "session:maintenance_question_type",
                "merge",
                "session:maintenance_law_audit",
                "final-sync",
            ],
        )
        self.assertEqual(recent["runs"][0]["runId"], run["runId"])
        self.assertTrue(all(not item.get("parentRunId") for item in recent["runs"]))

    def test_top_maintenance_keeps_validated_work_when_final_sync_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            synchronizer.can_sync = False
            coordinator = QualificationRunCoordinator(
                root,
                FakeWorkflow(),
                synchronizer,
                JobManager(),
                "secret",
            )
            parent_plan = FakeWorkflow().plan("sample", "law_audit")
            parent_plan.update(
                {
                    "stageId": "multi",
                    "stageIds": ["law_audit"],
                    "stageCode": "03b",
                    "stageLabel": "トップ整備",
                    "workType": "maintenance_flow",
                    "phaseExecutions": [
                        {
                            "id": "law_audit",
                            "index": 0,
                            "label": "現行法監査",
                            "stageIds": ["law_audit"],
                            "stageCodes": ["03b"],
                            "status": "pending",
                        }
                    ],
                }
            )
            parent = coordinator.store.create(parent_plan, status="queued")
            phase_plan = FakeWorkflow().plan("sample", "law_audit")
            phase_plan.update(
                {
                    "workType": "maintenance_law_audit",
                    "parentRunId": parent["runId"],
                    "flowPhaseId": "law_audit",
                    "phaseIndex": 0,
                }
            )
            coordinator._flow_phase_plan_prompt = (
                lambda _parent, _phase: (phase_plan, "phase prompt")
            )

            def complete_child(qualification, run_id, *_args, **_kwargs):
                coordinator.store.update(
                    qualification,
                    run_id,
                    status="succeeded",
                    receiptValidated=True,
                    workVersionReceipt={"recordedCount": 3},
                    artifactSync={"status": "deferred", "groups": []},
                )

            coordinator._run_human = complete_child

            result = coordinator._run_maintenance_flow(
                "sample", parent["runId"], lambda _message: None
            )
            run = coordinator.store.refresh("sample", parent["runId"])

        self.assertTrue(result["warning"])
        self.assertEqual(result["artifactSync"]["status"], "blocked")
        self.assertEqual(run["status"], "succeeded")
        self.assertTrue(run["receiptValidated"])
        self.assertEqual(run["artifactSync"]["status"], "blocked")
        self.assertIsNone(run["receiptError"])
        self.assertIsNone(run["error"])
        self.assertEqual(synchronizer.calls, [])

    def test_flow_phase_recomputes_failed_delta_after_specializing_work_type(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                FakeWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                app_server=ConfiguredAppServer(),
            )
            coordinator._plan = lambda *_args, **_kwargs: {
                "targetCount": 1,
                "targetGroupIds": ["2026"],
                "workType": "maintenance",
            }
            coordinator._resolvable_for_plan = (
                lambda _qualification, _group_ids, plan: (
                    ["resolved-by-law-audit"]
                    if plan.get("workType") == "maintenance_law_audit"
                    else []
                )
            )

            plan, _prompt = coordinator._flow_phase_plan_prompt(
                {
                    "qualification": "sample",
                    "mode": "outdated",
                    "scopeListGroupIds": [],
                    "runId": "parent-run",
                },
                {
                    "id": "law_audit",
                    "index": 0,
                    "stageIds": ["law_audit"],
                },
            )

        self.assertEqual(plan["workType"], "maintenance_law_audit")
        self.assertEqual(
            plan["resolvableFailedDeltaPaths"],
            ["resolved-by-law-audit"],
        )

    def test_flow_phase_promotes_to_group_refresh_for_failed_aggregate_delta(self):
        class ScopedWorkflow(FakeWorkflow):
            def prompt(self, qualification, stage_id, mode="remaining", **_scope):
                return {
                    "qualification": qualification,
                    "stageId": stage_id,
                    "mode": mode,
                    "targetCount": 3,
                    "prompt": f"prompt:{mode}",
                }

        with tempfile.TemporaryDirectory() as directory:
            coordinator = QualificationRunCoordinator(
                Path(directory),
                ScopedWorkflow(),
                FakeSynchronizer(),
                JobManager(),
                "secret",
                app_server=ConfiguredAppServer(),
            )

            def phase_plan(_qualification, _stage_id, mode, _resumed, **_scope):
                return {
                    "targetCount": 58 if mode == "group_refresh" else 34,
                    "targetGroupIds": ["2026"],
                    "workType": "maintenance",
                    "mode": mode,
                }

            coordinator._plan = phase_plan
            coordinator._resolvable_for_plan = (
                lambda _qualification, _group_ids, plan: (
                    ["failed-aggregate.json"]
                    if plan.get("workType") == "maintenance_law_audit"
                    and plan.get("targetCount") == 58
                    else []
                )
            )

            plan, prompt = coordinator._flow_phase_plan_prompt(
                {
                    "qualification": "sample",
                    "mode": "outdated",
                    "scopeListGroupIds": ["2026"],
                    "runId": "parent-run",
                },
                {
                    "id": "law_audit",
                    "index": 0,
                    "stageIds": ["law_audit"],
                },
            )

        self.assertEqual(plan["mode"], "group_refresh")
        self.assertEqual(plan["targetCount"], 58)
        self.assertEqual(
            plan["resolvableFailedDeltaPaths"],
            ["failed-aggregate.json"],
        )
        self.assertEqual(prompt, "prompt:group_refresh")

    def test_top_maintenance_skips_current_phase_before_outdated_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = LawSourceInventory()
            workflow = QualificationWorkflow(root, inventory)
            question = inventory.group("new-exam", "2026")["questions"][0]
            workflow.work_versions.record_stage(
                [question],
                workflow.versioned_policies("new-exam")["question_type"],
                run_id="completed-question-type",
                source="test",
            )

            def write_law_sidecar(work_type):
                if work_type != "maintenance_law_audit":
                    return
                self._write_law_audit_sidecar(
                    root,
                    "2026",
                    [
                        {
                            "reviewQuestionId": "new-exam-2026-q1",
                            "isLawRelated": True,
                            "auditStatus": "same_as_current",
                            "reviewState": "secondary_verified",
                            "lawReferences": [
                                [
                                    {
                                        "lawTitle": "ガス事業法",
                                        "lawId": "329AC0000000051",
                                        "article": "2",
                                        "verificationStatus": "verified",
                                    }
                                ]
                            ],
                        }
                    ],
                )

            app_server = FlowAppServer(before_receipt=write_law_sidecar)
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            stage_ids = ["question_type", "law_audit"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "outdated",
                stage_ids=stage_ids,
                list_group_ids=["2026"],
            )
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            deadline = time.monotonic() + 3
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])

        self.assertEqual(preview["targetCount"], 1)
        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(
            [item["status"] for item in run["phaseExecutions"]],
            ["skipped", "succeeded"],
        )
        self.assertEqual(len(run["childRunIds"]), 1)
        self.assertEqual(
            [kwargs["work_type"] for _, kwargs in app_server.calls],
            ["maintenance_law_audit"],
        )
        self.assertEqual(synchronizer.merge_calls, [])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

    def test_top_maintenance_stops_before_later_session_after_phase_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app_server = FlowAppServer(fail_on_writer=2)
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            coordinator._repository_file_fingerprints = lambda *_args: {}
            stage_ids = ["question_type", "law_audit"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "outdated",
                stage_ids=stage_ids,
                list_group_ids=["2026"],
            )
            self.assertEqual(preview["scopeListGroupIds"], ["2026"])
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            deadline = time.monotonic() + 3
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])

        self.assertEqual(job["status"], "failed")
        self.assertEqual(run["status"], "failed")
        self.assertEqual(
            [item["status"] for item in run["phaseExecutions"]],
            ["succeeded", "failed"],
            job,
        )
        self.assertEqual(len(run["childRunIds"]), 2)
        self.assertEqual(len(app_server.calls), 2)
        self.assertEqual(synchronizer.merge_calls, [("new-exam", "2026")])
        self.assertEqual(synchronizer.calls, [])
        self.assertEqual(run["workVersionReceipt"]["recordedCount"], 1)
        self.assertIn("phase 2 failed", run["error"])

    def test_top_maintenance_prepares_category_then_uses_separate_question_set_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            category_path = Path("output/new-exam/category/category.json")

            def write_category(work_type):
                if work_type != "maintenance_category_setup":
                    return
                absolute = root / category_path
                absolute.parent.mkdir(parents=True, exist_ok=True)
                absolute.write_text(
                    json.dumps(
                        {
                            "folders": [{"folderId": "folder-1"}],
                            "questionSets": [
                                {
                                    "questionSetId": "set-1",
                                    "folderId": "folder-1",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            app_server = FlowAppServer(
                changed_files_by_work_type={
                    "maintenance_category_setup": [category_path.as_posix()]
                },
                before_receipt=write_category,
            )
            jobs = JobManager()
            synchronizer = FakeSynchronizer()
            synchronizer.local_ready = False
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                synchronizer,
                jobs,
                "secret",
                app_server=app_server,
            )
            snapshots = iter(
                [
                    {},
                    {category_path: "sha256:category"},
                    {category_path: "sha256:category"},
                    {category_path: "sha256:category"},
                ]
            )
            coordinator._repository_file_fingerprints = lambda *_args: next(
                snapshots
            )
            stage_ids = ["category_setup", "question_set"]
            preview = coordinator.preview(
                "new-exam",
                stage_ids[0],
                "outdated",
                stage_ids=stage_ids,
                list_group_ids=["2026"],
            )
            self.assertEqual(preview["scopeListGroupIds"], ["2026"])
            started = coordinator.start(
                "new-exam",
                preview["stageId"],
                "outdated",
                preview["previewToken"],
                stage_ids=preview["stageIds"],
                list_group_ids=preview["scopeListGroupIds"],
            )
            deadline = time.monotonic() + 3
            job = jobs.get(started["job"]["jobId"])
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = jobs.get(started["job"]["jobId"])
            run = coordinator.store.get("new-exam", started["run"]["runId"])

        self.assertEqual(job["status"], "succeeded", job)
        self.assertEqual(
            [item["id"] for item in run["phaseExecutions"]],
            ["category_setup", "question_set"],
        )
        self.assertEqual(
            len({item["sessionId"] for item in run["phaseExecutions"]}),
            2,
        )
        self.assertEqual(
            [kwargs["work_type"] for _, kwargs in app_server.calls],
            ["maintenance_category_setup", "maintenance_question_set"],
        )
        self.assertEqual(run["stageIds"], preview["stageIds"])
        self.assertEqual(run["scopeListGroupIds"], preview["scopeListGroupIds"])
        self.assertEqual(run["targetCount"], preview["targetCount"])
        self.assertEqual(run["workItemCount"], preview["workItemCount"])
        self.assertEqual(synchronizer.merge_calls, [])
        self.assertEqual(synchronizer.calls, [("new-exam", "2026", True)])

    def test_law_audit_quality_accepts_explicitly_non_law_question(self):
        question = {
            "id": "non-law-question",
            "questionLabel": "問1",
            "isLawRelated": False,
            "issueCodes": [],
            "projected": {"isLawRelated": False},
        }

        QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_quality_rejects_unpublished_law_evidence(self):
        question = {
            "id": "law-question",
            "questionLabel": "問2",
            "isLawRelated": True,
            "issueCodes": [],
            "projected": {
                "isLawRelated": True,
                "lawRevisionFacts": [{"auditStatus": "same_as_current"}],
                "lawReferences": [
                    {"lawTitle": "ガス事業法", "article": "第2条"}
                ],
                "explanationText": ["正しい。定義に該当する。"],
                "suggestedQuestions": ["この内容はどうなっていますか？"],
                "suggestedQuestionDetails": [
                    {"answer": "対象となる事業を定めたものです。"}
                ],
            },
        }

        with self.assertRaisesRegex(
            QualificationRunError,
            "law-related suggestedQuestions.*concrete law evidence anchor",
        ):
            QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_quality_accepts_published_law_evidence(self):
        question = {
            "id": "law-question",
            "questionLabel": "問2",
            "isLawRelated": True,
            "issueCodes": [],
            "projected": {
                "isLawRelated": True,
                "correctChoiceText": ["正しい"],
                "lawRevisionFacts": [
                    {
                        "auditStatus": "same_as_current",
                        "current": {"correctChoiceText": "正しい"},
                        "evidenceSummary": {"verdict": "correct"},
                    }
                ],
                "lawReferences": [
                    {"lawTitle": "ガス事業法", "article": "第2条"}
                ],
                "explanationText": [
                    "正しい。ガス事業法第2条の定義に該当する。"
                ],
                "suggestedQuestions": [
                    "現行法のガス事業法第2条は何を定義していますか？"
                ],
                "suggestedQuestionDetails": [
                    {"answer": "ガス事業法第2条が対象事業を定義しています。"}
                ],
            },
        }

        QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_quality_uses_projected_metadata_before_artifact_sync(self):
        question = LawSourceInventory().group("new-exam", "2026")["questions"][0]
        question["issueCodes"] = [
            "law_audit_metadata_incomplete",
            "law_audit_verdict_mismatch",
            "law_hold",
            "law_basis_missing",
        ]

        QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_quality_does_not_require_law_verdicts_for_non_law_question(self):
        question = NonLawSourceInventory().group("new-exam", "2026")[
            "questions"
        ][0]
        question["projected"].update(
            {
                "correctChoiceText": ["正しい"],
                "lawRevisionFacts": {
                    "auditStatus": "not_law_related",
                    "reviewState": "secondary_verified",
                },
            }
        )

        QualificationRunCoordinator._validate_law_audit_quality([question])

    def test_law_audit_version_rejects_sidecar_classification_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, NonLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": True,
                        "auditStatus": "hold",
                        "reviewState": "needs_secondary_review",
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "sidecar整合.*isLawRelated",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(workflow)
                )
            question = workflow.inventory.group("new-exam", "2026")[
                "questions"
            ][0]
            status = workflow.work_versions.status_for(
                question,
                [workflow.versioned_policies("new-exam")["law_audit"]],
            )

        self.assertEqual(status["stages"][0]["status"], "unrecorded")

    def test_law_audit_version_records_matching_non_law_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, NonLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )

            receipt = coordinator._record_work_versions(
                self._law_audit_policy_run(workflow)
            )
            question = workflow.inventory.group("new-exam", "2026")[
                "questions"
            ][0]
            status = workflow.work_versions.status_for(
                question,
                [workflow.versioned_policies("new-exam")["law_audit"]],
            )

        self.assertEqual(receipt["recordedCount"], 1)
        self.assertEqual(status["stages"][0]["status"], "current")

    def test_law_audit_version_rejects_missing_or_duplicate_sidecar_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, NonLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            run = self._law_audit_policy_run(workflow)

            with self.assertRaisesRegex(
                QualificationRunError,
                "監査sidecarがありません",
            ):
                coordinator._record_work_versions(run)

            row = {
                "reviewQuestionId": "new-exam-2026-q1",
                "isLawRelated": False,
                "auditStatus": "not_law_related",
                "reviewState": "secondary_verified",
            }
            self._write_law_audit_sidecar(root, "2026", [row, row])
            with self.assertRaisesRegex(
                QualificationRunError,
                "対応行が2件",
            ):
                coordinator._record_work_versions(run)

            self._write_law_audit_sidecar(
                root,
                "2026",
                [{**row, "sourceSummary": {"text": "文字列ではない"}}],
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "監査sidecar.sourceSummaryがありません",
            ):
                coordinator._record_work_versions(run)

    def test_law_audit_version_validates_nested_verified_basis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            row = {
                "reviewQuestionId": "new-exam-2026-q1",
                "isLawRelated": True,
                "auditStatus": "same_as_current",
                "reviewState": "secondary_verified",
                "lawReferences": [
                    [
                        {
                            "lawTitle": "ガス事業法",
                            "lawId": "329AC0000000051",
                            "article": "2",
                            "verificationStatus": "verified",
                        }
                    ]
                ],
            }
            self._write_law_audit_sidecar(root, "2026", [row])

            receipt = coordinator._record_work_versions(
                self._law_audit_policy_run(workflow)
            )

        self.assertEqual(receipt["recordedCount"], 1)

    def test_law_audit_version_rejects_unverified_projected_basis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(
                root,
                UnverifiedLawSourceInventory(),
            )
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": True,
                        "auditStatus": "same_as_current",
                        "reviewState": "secondary_verified",
                        "lawReferences": [
                            [
                                {
                                    "lawTitle": "ガス事業法",
                                    "lawId": "329AC0000000051",
                                    "article": "2",
                                    "verificationStatus": "verified",
                                }
                            ]
                        ],
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "projected lawReferencesにverified",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(workflow)
                )

    def test_law_audit_version_rejects_different_verified_basis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, LawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": True,
                        "auditStatus": "same_as_current",
                        "reviewState": "secondary_verified",
                        "lawReferences": [
                            [
                                {
                                    "lawTitle": "消防法",
                                    "lawId": "323AC1000000186",
                                    "article": "3",
                                    "verificationStatus": "verified",
                                }
                            ]
                        ],
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "verified法令根拠が一致しません",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(workflow)
                )

    def test_law_audit_sidecar_rejects_unpublished_projected_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = LawSourceInventory().group("new-exam", "2026")[
                "questions"
            ][0]
            fact = question["projected"]["lawRevisionFacts"][0]
            fact["auditStatus"] = "hold"
            fact["reviewState"] = "needs_secondary_review"
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, LawSourceInventory()),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": True,
                        "auditStatus": "same_as_current",
                        "reviewState": "secondary_verified",
                        "lawReferences": [
                            [
                                {
                                    "lawTitle": "ガス事業法",
                                    "lawId": "329AC0000000051",
                                    "article": "2",
                                    "verificationStatus": "verified",
                                }
                            ]
                        ],
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "projected lawRevisionFactsが公開確定状態ではありません",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [question],
                )

    def test_non_law_sidecar_rejects_stale_hold_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = NonLawSourceInventory().group("new-exam", "2026")[
                "questions"
            ][0]
            question["projected"]["lawRevisionFacts"] = [
                {
                    "auditStatus": "hold",
                    "reviewState": "needs_secondary_review",
                }
            ]
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, NonLawSourceInventory()),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "非法令問題のprojected lawRevisionFacts",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [question],
                )

    def test_non_law_sidecar_rejects_flag_or_stale_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = NonLawSourceInventory().group("new-exam", "2026")[
                "questions"
            ][0]
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, NonLawSourceInventory()),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                        "lawReferences": [],
                    }
                ],
            )

            question["projected"]["lawGroundedExplanationNotNeeded"] = False
            with self.assertRaisesRegex(
                QualificationRunError,
                "lawGroundedExplanationNotNeededがtrueではありません",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [question],
                )

            question["projected"]["lawGroundedExplanationNotNeeded"] = True
            question["projected"]["lawReferences"] = [
                {
                    "lawTitle": "ガス事業法",
                    "lawId": "329AC0000000051",
                    "article": "2",
                    "verificationStatus": "verified",
                }
            ]
            with self.assertRaisesRegex(
                QualificationRunError,
                "非法令問題のprojected lawReferencesが空ではありません",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [question],
                )

    def test_law_audit_rejects_scalar_law_revision_facts(self):
        law_question = LawSourceInventory().group("new-exam", "2026")[
            "questions"
        ][0]
        law_question["projected"]["lawRevisionFacts"] = "invalid"
        with self.assertRaisesRegex(
            QualificationRunError,
            "lawRevisionFactsを確認できません",
        ):
            QualificationRunCoordinator._validate_law_audit_quality(
                [law_question]
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            non_law_question = NonLawSourceInventory().group(
                "new-exam",
                "2026",
            )["questions"][0]
            non_law_question["projected"]["lawRevisionFacts"] = "invalid"
            coordinator = QualificationRunCoordinator(
                root,
                QualificationWorkflow(root, NonLawSourceInventory()),
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2026",
                [
                    {
                        "reviewQuestionId": "new-exam-2026-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )
            with self.assertRaisesRegex(
                QualificationRunError,
                "lawRevisionFactsの型が不正",
            ):
                coordinator._validate_law_audit_sidecar_consistency(
                    "new-exam",
                    [non_law_question],
                )

    def test_law_audit_version_is_atomic_when_one_group_sidecar_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = MultiGroupNonLawSourceInventory()
            workflow = QualificationWorkflow(root, inventory)
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            self._write_law_audit_sidecar(
                root,
                "2025",
                [
                    {
                        "reviewQuestionId": "new-exam-2025-q1",
                        "isLawRelated": False,
                        "auditStatus": "not_law_related",
                        "reviewState": "secondary_verified",
                    }
                ],
            )

            with self.assertRaisesRegex(
                QualificationRunError,
                "2026_law_revision_audit.jsonl: 監査sidecarがありません",
            ):
                coordinator._record_work_versions(
                    self._law_audit_policy_run(
                        workflow,
                        list_group_ids=["2025", "2026"],
                    )
                )
            statuses = [
                workflow.work_versions.status_for(
                    inventory.group("new-exam", group)["questions"][0],
                    [workflow.versioned_policies("new-exam")["law_audit"]],
                )["stages"][0]["status"]
                for group in ("2025", "2026")
            ]

        self.assertEqual(statuses, ["unrecorded", "unrecorded"])

    def test_law_audit_version_is_not_recorded_while_quality_warning_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = QualificationWorkflow(root, IncompleteLawSourceInventory())
            coordinator = QualificationRunCoordinator(
                root,
                workflow,
                FakeSynchronizer(),
                JobManager(),
                "secret",
            )
            plan = coordinator._plan(
                "new-exam", "law_audit", "outdated", None
            )
            plan["runId"] = "law-audit-run"

            with self.assertRaisesRegex(
                QualificationRunError, "03b 現行法監査の必須メタデータ"
            ):
                coordinator._record_work_versions(plan)
            status = workflow.work_versions.status_for(
                IncompleteLawSourceInventory().group("new-exam", "2026")[
                    "questions"
                ][0],
                [workflow.versioned_policies("new-exam")["law_audit"]],
            )

        self.assertEqual(status["stages"][0]["status"], "unrecorded")

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
        self.assertIsNone(recent["activeRun"])
        self.assertEqual(recent["runs"][0]["runId"], started["run"]["runId"])
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

        self.assertIsNone(recent["activeRun"])
        self.assertEqual(recent["runs"][0]["status"], "awaiting_changes")
        self.assertIn("pass検証", recent["runs"][0]["receiptError"])

    def test_result_receipt_normalizes_past_tense_command_statuses(self):
        succeeded = QualificationRunStore._validated_result_receipt(
            {
                "status": "succeeded",
                "summary": "検証済み。",
                "commands": [{"command": "check", "status": "passed"}],
            }
        )
        failed = QualificationRunStore._validated_result_receipt(
            {
                "status": "failed",
                "summary": "検証失敗。",
                "commands": [{"command": "check", "status": "failed"}],
            }
        )

        self.assertEqual(succeeded["commands"][0]["status"], "pass")
        self.assertEqual(failed["commands"][0]["status"], "fail")

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

        self.assertIsNone(recent["activeRun"])
        self.assertEqual(recent["runs"][0]["status"], "awaiting_changes")

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

    def test_validated_patch_run_recovers_as_success_when_auto_sync_is_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            run = store.create(
                FakeWorkflow().plan("sample", "law_audit", "remaining"),
                status="validating",
                prompt="work",
            )
            store.update(
                "sample",
                run["runId"],
                receiptValidated=True,
                artifactSync={"status": "running", "groups": []},
            )

            recovered = QualificationRunStore(root).get("sample", run["runId"])

        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["artifactSync"]["status"], "interrupted")
        self.assertIsNone(recovered["error"])

    def test_running_human_manifest_recovers_partial_delta_from_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_root = (
                root
                / "output/sample/questions_json/2026/21_explanationText_added"
            )
            patch_root.mkdir(parents=True)
            deleted = patch_root / "deleted.json"
            deleted.write_text("before\n", encoding="utf-8")
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan["stageIds"] = ["law_audit"]
            plan["workType"] = "maintenance"
            run = store.create(plan, status="running", prompt="work")
            store.write_baseline(
                "sample",
                run["runId"],
                (
                    patch_root,
                    (root / run["resultReceiptPath"]).parent,
                ),
            )
            deleted.unlink()
            created = patch_root / "partial.json"
            created.write_text("partial\n", encoding="utf-8")

            recovered = QualificationRunStore(root).get("sample", run["runId"])

        self.assertEqual(recovered["status"], "failed")
        self.assertFalse(recovered["deltaUnknown"])
        self.assertEqual(
            recovered["result"]["changedFiles"],
            sorted(
                [
                    str(deleted.relative_to(root)),
                    str(created.relative_to(root)),
                ]
            ),
        )

    def test_running_human_without_baseline_is_unknown_and_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QualificationRunStore(root)
            plan = FakeWorkflow().plan("sample", "law_audit", "remaining")
            plan["stageIds"] = ["law_audit"]
            plan["workType"] = "maintenance"
            run = store.create(plan, status="running", prompt="work")

            recovered = QualificationRunStore(root).get("sample", run["runId"])

        self.assertEqual(recovered["status"], "interrupted")
        self.assertTrue(recovered["deltaUnknown"])

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
