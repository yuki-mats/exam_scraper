from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.question_review_console.firestore_readback import PRODUCTION_PROJECT_ID
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.projection import normalize_verdict


LOCAL_STAGES = ("merge", "convert", "upload")
LOCAL_STALE_ISSUES = {
    "merge_stale",
    "convert_stale",
    "upload_stale",
    "upload_missing",
}


class WorkflowError(RuntimeError):
    pass


def aggregate_group_workflow(group: Mapping[str, Any]) -> dict[str, Any]:
    questions = group.get("questions") or []
    stages: dict[str, dict[str, Any]] = {}
    for stage in LOCAL_STAGES:
        counts = {"match": 0, "stale": 0, "missing": 0}
        for question in questions:
            status = str(question.get("workflow", {}).get(stage) or "missing")
            counts[status if status in counts else "stale"] += 1
        if counts["missing"]:
            status = "missing"
        elif counts["stale"]:
            status = "stale"
        else:
            status = "match"
        stages[stage] = {"status": status, "counts": counts}
    return {
        "questionCount": len(questions),
        "stages": stages,
        "localReady": all(stages[stage]["status"] == "match" for stage in LOCAL_STAGES),
    }


class ArtifactSynchronizer:
    def __init__(
        self,
        repo_root: Path,
        inventory: QuestionInventory,
        secret: str,
        *,
        command_runner: Callable[..., int] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.inventory = inventory
        self.secret = secret.encode("utf-8")
        self.command_runner = command_runner or self._run_command

    def preview(
        self, qualification: str, list_group_id: str, *, force: bool = False
    ) -> dict[str, Any]:
        group = self.inventory.group(qualification, list_group_id)
        summary = aggregate_group_workflow(group)
        allow_missing_answer_result = self._allow_missing_answer_result(group)
        required_field_warnings = [
            {
                "questionId": str(question.get("id") or ""),
                "sourceQuestionKey": str(question.get("sourceQuestionKey") or ""),
                "detail": str(issue.get("detail") or ""),
                "fields": list(issue.get("fields") or []),
            }
            for question in group.get("questions") or []
            for issue in question.get("issues") or []
            if issue.get("code") == "required_field_missing"
        ]
        command = self._command(
            qualification,
            list_group_id,
            allow_missing_answer_result=allow_missing_answer_result,
        )
        token_payload = {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "fingerprint": group["fingerprint"],
            "sourceHash": self._source_hash(qualification, list_group_id),
            "command": command,
            "force": force,
        }
        return {
            **summary,
            "qualification": qualification,
            "listGroupId": list_group_id,
            "needsSync": force or not summary["localReady"],
            "force": force,
            "canSync": not required_field_warnings,
            "requiredFieldWarnings": required_field_warnings,
            "allowMissingAnswerResult": allow_missing_answer_result,
            "previewToken": self._token(token_payload),
        }

    def run(
        self,
        qualification: str,
        list_group_id: str,
        preview_token: str,
        emit: Callable[[str], None],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        preview = self.preview(qualification, list_group_id, force=force)
        if not hmac.compare_digest(preview["previewToken"], preview_token):
            raise WorkflowError("確認後にpatch又は成果物が更新されました。再読込してください。")
        if preview["requiredFieldWarnings"]:
            raise WorkflowError(
                "必須field不足があるため反映できません。問題詳細の警告を修正してください。"
            )
        if not preview["needsSync"]:
            return {**preview, "message": "ローカル成果物はすでに最新です。"}

        source_before = self._source_hash(qualification, list_group_id)
        command = self._command(
            qualification,
            list_group_id,
            allow_missing_answer_result=preview["allowMissingAnswerResult"],
        )
        emit(f"対象: {qualification} / {list_group_id}")
        return_code = self.command_runner(
            command,
            cwd=self.repo_root,
            env=self._environment(),
            emit=emit,
        )
        if return_code != 0:
            raise WorkflowError(f"成果物の同期に失敗しました（exit={return_code}）。")
        if source_before != self._source_hash(qualification, list_group_id):
            raise WorkflowError("同期中に00_sourceが変更されたため、結果を確定できません。")

        self.inventory.invalidate(qualification, list_group_id)
        updated = self.preview(qualification, list_group_id)
        if not updated["localReady"]:
            states = ", ".join(
                f"{name}={value['status']}"
                for name, value in updated["stages"].items()
            )
            raise WorkflowError(f"同期後も差分が残っています: {states}")
        emit("Merge・Convert・upload-readyの一致を確認しました。")
        return {**updated, "message": "ローカル成果物を最新patchに同期しました。"}

    def _command(
        self,
        qualification: str,
        list_group_id: str,
        *,
        allow_missing_answer_result: bool,
    ) -> list[str]:
        base_dir = self.repo_root / "output" / qualification / "questions_json"
        if not (base_dir / list_group_id / "00_source").is_dir():
            raise WorkflowError("対象の00_sourceがありません。")
        command = [
            sys.executable,
            str(self.repo_root / "scripts" / "pipeline" / "prepare_firestore_upload.py"),
            list_group_id,
            "--base-dir",
            str(base_dir),
            "--skip-update-category-counts",
            "--upload-dry-run",
        ]
        if allow_missing_answer_result:
            command.append("--allow-missing-answer-result")
        return command

    @staticmethod
    def _allow_missing_answer_result(group: Mapping[str, Any]) -> bool:
        has_missing_answer_result = False
        for question in group.get("questions") or []:
            projected = question.get("projected") or {}
            choices = projected.get("choiceTextList")
            verdicts = projected.get("correctChoiceText")
            if not isinstance(choices, list) or not choices:
                return False
            if not isinstance(verdicts, list) or len(verdicts) != len(choices):
                return False
            if any(
                normalize_verdict(value) not in {"正しい", "間違い"}
                for value in verdicts
            ):
                return False
            if not str(projected.get("answer_result_text") or "").strip():
                has_missing_answer_result = True
        return has_missing_answer_result

    def _source_hash(self, qualification: str, list_group_id: str) -> str:
        source_dir = (
            self.repo_root
            / "output"
            / qualification
            / "questions_json"
            / list_group_id
            / "00_source"
        )
        digest = hashlib.sha256()
        for path in sorted(source_dir.glob("*.json")):
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _token(self, payload: Mapping[str, Any]) -> str:
        value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["FIREBASE_PROJECT_ID"] = PRODUCTION_PROJECT_ID
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (str(self.repo_root), env.get("PYTHONPATH"))
            if value
        )
        return env

    @staticmethod
    def _run_command(
        command: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        emit: Callable[[str], None],
    ) -> int:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            emit(line)
        return process.wait()
