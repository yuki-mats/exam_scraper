from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.question_review_console.jobs import JobConflictError, JobManager
from tools.question_review_console.qualification_workflow import QualificationWorkflow
from tools.question_review_console.workflow_runner import ArtifactSynchronizer


ACTIVE_RUN_STATUSES = {
    "queued",
    "running",
    "awaiting_changes",
    "interrupted",
    "failed",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
        raise ValueError(f"invalid path segment: {value}")
    return value


class QualificationRunError(RuntimeError):
    pass


class QualificationRunStore:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.root = self.repo_root / "output" / "question_review_console" / "workflow_runs"
        self._lock = threading.RLock()
        self._recover_interrupted_runs()

    def create(
        self,
        plan: Mapping[str, Any],
        *,
        status: str,
        prompt: str | None = None,
        resumed_from: str | None = None,
    ) -> dict[str, Any]:
        qualification = _safe_segment(str(plan["qualification"]))
        run_id = f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{secrets.token_hex(4)}"
        run_dir = self.root / qualification / run_id
        now = _now()
        manifest = {
            "runId": run_id,
            "qualification": qualification,
            "stageId": str(plan["stageId"]),
            "stageCode": str(plan["stageCode"]),
            "stageLabel": str(plan["stageLabel"]),
            "mode": str(plan["mode"]),
            "modeLabel": str(plan["modeLabel"]),
            "kind": str(plan["kind"]),
            "status": status,
            "targetCount": int(plan["targetCount"]),
            "targetGroupIds": list(plan.get("targetGroupIds") or []),
            "completedGroupIds": [],
            "jobId": None,
            "resumedFrom": resumed_from,
            "createdAt": now,
            "updatedAt": now,
            "finishedAt": None,
            "error": None,
            "result": None,
            "promptPath": None,
        }
        with self._lock:
            run_dir.mkdir(parents=True, exist_ok=False)
            if prompt is not None:
                prompt_path = run_dir / "prompt.md"
                prompt_path.write_text(prompt, encoding="utf-8")
                manifest["promptPath"] = str(prompt_path.relative_to(self.repo_root))
            self._write_manifest(run_dir / "manifest.json", manifest)
        return copy.deepcopy(manifest)

    def update(self, qualification: str, run_id: str, **changes: Any) -> dict[str, Any]:
        path = self._manifest_path(qualification, run_id)
        with self._lock:
            manifest = self._load_manifest(path)
            manifest.update(changes)
            manifest["updatedAt"] = _now()
            if manifest.get("status") in {"succeeded", "failed"}:
                manifest["finishedAt"] = manifest.get("finishedAt") or manifest["updatedAt"]
            self._write_manifest(path, manifest)
        return copy.deepcopy(manifest)

    def list(self, qualification: str, *, limit: int = 8) -> list[dict[str, Any]]:
        qualification = _safe_segment(qualification)
        directory = self.root / qualification
        if not directory.is_dir():
            return []
        manifests: list[dict[str, Any]] = []
        with self._lock:
            for path in sorted(directory.glob("*/manifest.json"), reverse=True):
                manifest = self._load_manifest(path)
                manifests.append(self._public(manifest))
                if len(manifests) >= limit:
                    break
        return manifests

    def get(self, qualification: str, run_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public(self._load_manifest(self._manifest_path(qualification, run_id)))

    def prompt(self, qualification: str, run_id: str) -> str:
        manifest = self.get(qualification, run_id)
        relative = str(manifest.get("promptPath") or "")
        if not relative:
            raise QualificationRunError("この作業には再コピーできるCodex依頼がありません。")
        path = (self.repo_root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise QualificationRunError("保存済みのCodex依頼が見つかりません。")
        return path.read_text(encoding="utf-8")

    def _manifest_path(self, qualification: str, run_id: str) -> Path:
        return self.root / _safe_segment(qualification) / _safe_segment(run_id) / "manifest.json"

    def _recover_interrupted_runs(self) -> None:
        if not self.root.is_dir():
            return
        with self._lock:
            for path in self.root.glob("*/*/manifest.json"):
                manifest = self._load_manifest(path)
                if manifest.get("status") not in {"queued", "running"}:
                    continue
                manifest["status"] = "interrupted"
                manifest["error"] = "ローカルUIの再起動で処理が中断されました。再開できます。"
                manifest["updatedAt"] = _now()
                self._write_manifest(path, manifest)

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise QualificationRunError("作業履歴が見つかりません。")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise QualificationRunError("作業履歴の形式が不正です。")
        return value

    @staticmethod
    def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _public(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(dict(manifest))


class QualificationRunCoordinator:
    def __init__(
        self,
        repo_root: Path,
        workflow: QualificationWorkflow,
        synchronizer: ArtifactSynchronizer,
        jobs: JobManager,
        secret: str,
        *,
        store: QualificationRunStore | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.workflow = workflow
        self.synchronizer = synchronizer
        self.jobs = jobs
        self.secret = secret.encode("utf-8")
        self.store = store or QualificationRunStore(self.repo_root)

    def preview(
        self,
        qualification: str,
        stage_id: str,
        mode: str,
        *,
        resumed_from: str | None = None,
    ) -> dict[str, Any]:
        plan = self._plan(qualification, stage_id, mode, resumed_from)
        group_previews: list[dict[str, Any]] = []
        blocking_warnings: list[dict[str, Any]] = []
        if plan["kind"] == "machine":
            for group_id in plan["targetGroupIds"]:
                preview = self.synchronizer.preview(
                    qualification, group_id, force=bool(plan.get("force"))
                )
                group_previews.append(
                    {
                        "listGroupId": group_id,
                        "previewToken": preview["previewToken"],
                        "questionCount": preview["questionCount"],
                        "localReady": preview["localReady"],
                    }
                )
                blocking_warnings.extend(preview.get("requiredFieldWarnings") or [])
        token_payload = {"plan": plan, "groupPreviews": group_previews}
        return {
            "qualification": qualification,
            "stageId": stage_id,
            "stageCode": plan["stageCode"],
            "stageLabel": plan["stageLabel"],
            "purpose": plan["purpose"],
            "kind": plan["kind"],
            "mode": mode,
            "modeLabel": plan["modeLabel"],
            "resumedFrom": resumed_from,
            "targetCount": plan["targetCount"],
            "targetGroupIds": plan["targetGroupIds"],
            "canStart": bool(plan["targetCount"]) and not blocking_warnings,
            "blockingWarnings": blocking_warnings[:20],
            "isProductionWrite": False,
            "previewToken": self._token(token_payload),
        }

    def start(
        self,
        qualification: str,
        stage_id: str,
        mode: str,
        preview_token: str,
        *,
        resumed_from: str | None = None,
    ) -> dict[str, Any]:
        preview = self.preview(
            qualification, stage_id, mode, resumed_from=resumed_from
        )
        if not hmac.compare_digest(str(preview["previewToken"]), preview_token):
            raise QualificationRunError("対象が更新されました。もう一度確認してください。")
        if not preview["canStart"]:
            if preview["blockingWarnings"]:
                raise QualificationRunError("必須field不足があるため開始できません。")
            raise QualificationRunError("選択した範囲に対象はありません。")

        plan = self._plan(qualification, stage_id, mode, resumed_from)
        if plan["kind"] == "human":
            prompt = self.workflow.prompt(qualification, stage_id, mode)["prompt"]
            run = self.store.create(
                plan,
                status="awaiting_changes",
                prompt=prompt,
                resumed_from=resumed_from,
            )
            return {"run": run, "prompt": prompt, "job": None}

        run = self.store.create(
            plan, status="queued", resumed_from=resumed_from
        )
        try:
            job = self.jobs.start(
                kind="qualification-sync",
                key=f"qualification-sync:{qualification}",
                worker=lambda emit: self._run_delivery(plan, run["runId"], emit),
            )
        except JobConflictError:
            self.store.update(
                qualification,
                run["runId"],
                status="failed",
                error="この資格で別の出力処理が実行中です。",
            )
            raise
        run = self.store.update(
            qualification, run["runId"], jobId=job["jobId"]
        )
        return {"run": run, "prompt": None, "job": job}

    def recent(self, qualification: str) -> dict[str, Any]:
        runs = self.store.list(qualification)
        return {
            "qualification": qualification,
            "runs": runs,
            "activeRun": next(
                (run for run in runs if run.get("status") in ACTIVE_RUN_STATUSES),
                None,
            ),
        }

    def resume_prompt(self, qualification: str, run_id: str) -> dict[str, Any]:
        run = self.store.get(qualification, run_id)
        return {"run": run, "prompt": self.store.prompt(qualification, run_id)}

    def _run_delivery(
        self,
        plan: Mapping[str, Any],
        run_id: str,
        emit: Callable[[str], None],
    ) -> dict[str, Any]:
        qualification = str(plan["qualification"])
        completed: list[str] = []
        self.store.update(qualification, run_id, status="running")
        try:
            for group_id in plan["targetGroupIds"]:
                emit(f"{group_id}: 出力を確認します。")
                preview = self.synchronizer.preview(
                    qualification, group_id, force=bool(plan.get("force"))
                )
                result = self.synchronizer.run(
                    qualification,
                    group_id,
                    str(preview["previewToken"]),
                    emit,
                    force=bool(plan.get("force")),
                )
                completed.append(group_id)
                self.store.update(
                    qualification,
                    run_id,
                    completedGroupIds=list(completed),
                    result={"lastGroup": group_id, "message": result.get("message")},
                )
        except Exception as exc:  # noqa: BLE001
            self.store.update(
                qualification,
                run_id,
                status="failed",
                completedGroupIds=list(completed),
                error=str(exc),
            )
            raise
        message = f"{len(completed)}フォルダのMerge・Convert・upload-readyを確認しました。"
        self.store.update(
            qualification,
            run_id,
            status="succeeded",
            completedGroupIds=list(completed),
            result={"message": message},
        )
        return {
            "qualification": qualification,
            "runId": run_id,
            "completedGroupIds": completed,
            "message": message,
        }

    def _plan(
        self,
        qualification: str,
        stage_id: str,
        mode: str,
        resumed_from: str | None,
    ) -> dict[str, Any]:
        plan = dict(self.workflow.plan(qualification, stage_id, mode))
        if not resumed_from or plan["kind"] != "machine":
            return plan
        previous = self.store.get(qualification, resumed_from)
        if previous.get("stageId") != stage_id or previous.get("mode") != mode:
            raise QualificationRunError("再開元と工程又は対象範囲が一致しません。")
        completed = set(previous.get("completedGroupIds") or [])
        remaining = [
            group_id
            for group_id in plan.get("targetGroupIds") or []
            if group_id not in completed
        ]
        plan["targetGroupIds"] = remaining
        plan["targetCount"] = len(remaining)
        plan["sourceFiles"] = [
            str(Path("output") / qualification / "questions_json" / group_id)
            for group_id in remaining
        ]
        return plan

    def _token(self, payload: Mapping[str, Any]) -> str:
        value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
