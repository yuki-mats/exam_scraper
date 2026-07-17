import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.question_review_console.codex_app_server import AppServerTurnResult
from tools.question_review_console.failed_delta import (
    unresolved_failed_delta_paths,
)
from tools.question_review_console.jobs import JobManager
from tools.question_review_console.qualification_runs import (
    QualificationRunCoordinator,
    QualificationRunError,
    QualificationRunStore,
    _maintenance_session_phases,
)
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tests.support.law_audit import valid_v2_audit_row


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


def _write_completed_progress(prompt: str) -> None:
    manifest_line = next(
        (
            line
            for line in prompt.splitlines()
            if "progressTargetsとprogressStages" in line
        ),
        "",
    )
    if not manifest_line:
        return
    manifest_path = Path(manifest_line.split("`")[1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    progress_path = manifest_path.parent / "agent_output" / "progress.jsonl"
    events = []
    policy_targets = manifest.get("policyTargets") or {}
    for target in manifest.get("progressTargets") or []:
        aliases = {
            str(target.get("id") or ""),
            str(target.get("questionKey") or ""),
            *(str(value) for value in target.get("aliases") or []),
        } - {""}
        events.append(
            {"event": "question_started", "questionId": target["id"]}
        )
        for stage in manifest.get("progressStages") or []:
            stage_id = str(stage.get("id") or "")
            planned = {
                str(value) for value in policy_targets.get(stage_id) or []
            }
            if planned and not aliases & planned:
                continue
            events.append(
                {
                    "event": "stage_completed",
                    "questionId": target["id"],
                    "stageId": stage_id,
                    "result": {"summary": "検証済み"},
                }
            )
        events.append(
            {"event": "question_completed", "questionId": target["id"]}
        )
    progress_path.write_text(
        "".join(
            json.dumps(event, ensure_ascii=False) + "\n" for event in events
        ),
        encoding="utf-8",
    )

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
        _write_completed_progress(prompt)
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
        _write_completed_progress(prompt)
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
                    "sourceQuestionKey": f"new-exam:{list_group_id}:q1",
                    "sourceRecordRef": f"question_{list_group_id}_1.json#0",
                    "source": {"originalQuestionId": original_id},
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
            "choiceTextList": ["A"],
            "correctChoiceText": ["正しい"],
            "explanationText": ["正しい。法令に関係しない技術事項である。"],
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


class QualificationRunTestSupport(unittest.TestCase):

    def _wait_for_job(
        self,
        jobs: JobManager,
        job_id: str,
        *,
        timeout: float = 3,
    ) -> dict:
        deadline = time.monotonic() + timeout
        job = jobs.get(job_id)
        while (
            job["status"] in {"queued", "running"}
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
            job = jobs.get(job_id)
        self.assertNotIn(
            job["status"],
            {"queued", "running"},
            f"job did not finish within {timeout}s: {job}",
        )
        return job

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
                        **valid_v2_audit_row(
                            str(row.get("reviewQuestionId") or ""),
                            str(
                                row.get("sourceQuestionKey")
                                or f"new-exam:{list_group_id}:q1"
                            ),
                            source_ref=str(
                                row.get("sourceRecordRef")
                                or f"question_{list_group_id}_1.json#0"
                            ),
                            qualification="new-exam",
                            listGroupId=list_group_id,
                            examYear=(
                                int(list_group_id)
                                if list_group_id.isdigit()
                                and len(list_group_id) == 4
                                else 2026
                            ),
                            sourceSummary="分類と根拠を確認した。",
                        ),
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
            job = self._wait_for_job(
                jobs,
                started["job"]["jobId"],
                timeout=5,
            )
            run = coordinator.store.refresh("sample", started["run"]["runId"])
        return job, run


__all__ = [
    name for name in globals() if not name.startswith("__")
]
