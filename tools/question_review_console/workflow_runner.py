from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from tools.question_review_console.failed_delta import unresolved_failed_delta_paths
from tools.question_review_console.firestore_readback import PRODUCTION_PROJECT_ID
from tools.question_review_console.inventory import QuestionInventory
from tools.question_review_console.law_audit_contract import LAW_AUDIT_ISSUES
from tools.question_review_console.law_audit_quality import (
    law_revision_current_verdict_issues,
)
from tools.question_review_console.projection import normalize_verdict
from tools.question_review_console.work_versions import QuestionWorkVersionStore
from tools.question_review_console.workflow_catalog import (
    WorkflowCatalog,
    policy_version_major,
)


LOCAL_STAGES = ("merge", "convert", "upload")
STRICT_LAW_VALIDATION_STAGES = ("merged", "firestore")
# Legacy law-audit facts predate verdict snapshots.  Work-version-aware sync
# adds verdict matching only after the current 03b MAJOR has been validated.
STRICT_LAW_VALIDATION_FLAGS = (
    "--require-all-law-related",
    "--fail-on-hold",
    "--require-evidence-summary",
    "--require-law-references",
    "--require-current-correct-choice",
    "--require-verified-law-references",
    "--require-public-law-evidence",
)
LOCAL_STALE_ISSUES = {
    "merge_stale",
    "convert_stale",
    "upload_stale",
    "upload_missing",
}


class WorkflowError(RuntimeError):
    pass


def sync_after_patch_update(
    synchronizer: "ArtifactSynchronizer",
    qualification: str,
    list_group_id: str,
    emit: Callable[[str], None],
    *,
    force: bool = True,
) -> dict[str, Any]:
    """Best-effort propagation used after a validated patch update.

    A validated patch is itself sufficient reason to rebuild.  The inventory
    stale detector is intentionally a UI hint and may not compare every nested
    field, so it must not suppress propagation after a save.
    """

    try:
        preview = synchronizer.preview(
            qualification,
            list_group_id,
            force=force,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "listGroupId": list_group_id,
            "status": "failed",
            "message": f"公開用データの状態を確認できませんでした: {exc}",
        }

    needs_sync = bool(
        preview.get("needsSync", not bool(preview.get("localReady")))
    )
    if not needs_sync:
        return {
            "listGroupId": list_group_id,
            "status": "current",
            "message": "Merge・Convert・upload-readyはすでに最新です。",
        }

    required_warnings = list(preview.get("requiredFieldWarnings") or [])
    failed_delta_paths = list(preview.get("failedDeltaPaths") or [])
    if required_warnings:
        return {
            "listGroupId": list_group_id,
            "status": "blocked",
            "message": (
                "必須field不足があるため公開用データを自動更新できませんでした"
                f"（{len(required_warnings)}問）。"
            ),
        }
    if failed_delta_paths:
        return {
            "listGroupId": list_group_id,
            "status": "blocked",
            "message": (
                "失敗したCodex turnの未確定patchがあるため公開用データを"
                "自動更新できませんでした。"
            ),
        }
    strict_validation_warnings = list(
        preview.get("strictValidationWarnings") or []
    )
    if strict_validation_warnings:
        warning = strict_validation_warnings[0]
        detail = str(warning.get("detail") or "")
        field = str(warning.get("field") or "")
        field_note = f"（対象field: {field}）" if field else ""
        return {
            "listGroupId": list_group_id,
            "status": "blocked",
            "message": (
                "現行法監査済み問題に検証エラーがあるため"
                f"自動更新できません（{len(strict_validation_warnings)}件）。"
                + detail
                + field_note
            ),
        }
    if preview.get("canSync") is False:
        return {
            "listGroupId": list_group_id,
            "status": "blocked",
            "message": "公開用データを自動更新できない状態です。",
        }

    emit(f"{list_group_id}: 最新patchから公開用データを自動更新します。")
    try:
        result = synchronizer.run(
            qualification,
            list_group_id,
            str(preview["previewToken"]),
            emit,
            force=force,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "listGroupId": list_group_id,
            "status": "failed",
            "message": f"公開用データの自動更新に失敗しました: {exc}",
        }
    return {
        "listGroupId": list_group_id,
        "status": "succeeded",
        "message": str(
            result.get("message")
            or "Merge・Convert・upload-readyを最新patchへ同期しました。"
        ),
    }


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
        self.work_versions = QuestionWorkVersionStore(self.repo_root)
        self.workflow_catalog = WorkflowCatalog(self.repo_root)

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
        failed_delta_paths = list(
            unresolved_failed_delta_paths(
                self.repo_root,
                qualification,
                list_group_id,
            )
        )
        command = self._command(
            qualification,
            list_group_id,
            allow_missing_answer_result=allow_missing_answer_result,
        )
        _law_questions, current_law_questions = self._law_audit_version_scope(group)
        strict_validation_warnings = self._current_law_verdict_warnings(
            current_law_questions
        )
        strict_validation_question_ids = self._strict_validation_question_ids(
            current_law_questions
        )
        if len(strict_validation_question_ids) != len(current_law_questions):
            strict_validation_warnings.append(
                {
                    "code": "law_audit_identity_missing",
                    "questionId": "",
                    "sourceQuestionKey": "",
                    "field": "originalQuestionId",
                    "detail": "現行法監査済み問題の元問題IDを確認できません。",
                }
            )
        require_current_law_verdict = bool(current_law_questions)
        strict_validation_stages = (
            self._strict_validation_stages(group)
            if require_current_law_verdict and not strict_validation_warnings
            else []
        )
        token_payload = {
            "qualification": qualification,
            "listGroupId": list_group_id,
            "fingerprint": group["fingerprint"],
            "sourceHash": self._source_hash(qualification, list_group_id),
            "command": command,
            "strictValidationStages": strict_validation_stages,
            "strictValidationQuestionIds": strict_validation_question_ids,
            "requireCurrentLawVerdict": require_current_law_verdict,
            "strictValidationWarnings": strict_validation_warnings,
            "force": force,
        }
        return {
            **summary,
            "qualification": qualification,
            "listGroupId": list_group_id,
            "needsSync": force or not summary["localReady"],
            "force": force,
            "canSync": not (
                required_field_warnings
                or failed_delta_paths
                or strict_validation_warnings
            ),
            "requiredFieldWarnings": required_field_warnings,
            "failedDeltaPaths": failed_delta_paths,
            "allowMissingAnswerResult": allow_missing_answer_result,
            "strictValidationStages": strict_validation_stages,
            "strictValidationQuestionIds": strict_validation_question_ids,
            "requireCurrentLawVerdict": require_current_law_verdict,
            "strictValidationWarnings": strict_validation_warnings,
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
        if preview["failedDeltaPaths"]:
            raise WorkflowError(
                "失敗したCodex turnの未確定patchがあるため反映できません: "
                + ", ".join(preview["failedDeltaPaths"][:10])
            )
        if preview["strictValidationWarnings"]:
            details = " ".join(
                str(warning.get("detail") or "")
                for warning in preview["strictValidationWarnings"][:5]
            )
            raise WorkflowError(
                "現行法監査済み問題の正誤整合検証に失敗しました。"
                + details
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

        for stage in preview["strictValidationStages"]:
            emit(f"{list_group_id}: 法令成果物を厳格検証します（{stage}）。")
            validation_code = self.command_runner(
                self._strict_validation_command(
                    qualification,
                    list_group_id,
                    stage=stage,
                    original_question_ids=preview[
                        "strictValidationQuestionIds"
                    ],
                ),
                cwd=self.repo_root,
                env=self._environment(),
                emit=emit,
            )
            if validation_code != 0:
                raise WorkflowError(
                    "法令成果物の厳格検証に失敗しました"
                    f"（stage={stage}, exit={validation_code}）。"
                )
        if source_before != self._source_hash(qualification, list_group_id):
            raise WorkflowError("厳格検証中に00_sourceが変更されたため、結果を確定できません。")

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
    def _strict_validation_stages(group: Mapping[str, Any]) -> list[str]:
        has_law_audit_data = any(
            isinstance(question, Mapping)
            and (
                bool(set(question.get("issueCodes") or []) & LAW_AUDIT_ISSUES)
                or (
                    isinstance(question.get("projected"), Mapping)
                    and (
                        question["projected"].get("isLawRelated") is True
                        or bool(question["projected"].get("lawReferences"))
                        or bool(question["projected"].get("lawRevisionFacts"))
                    )
                )
            )
            for question in group.get("questions") or []
        )
        return list(STRICT_LAW_VALIDATION_STAGES) if has_law_audit_data else []

    @staticmethod
    def _law_related_questions(
        group: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        return [
            question
            for question in group.get("questions") or []
            if isinstance(question, Mapping)
            and isinstance(question.get("projected"), Mapping)
            and question["projected"].get("isLawRelated") is True
        ]

    def _law_audit_version_scope(
        self,
        group: Mapping[str, Any],
    ) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        law_questions = self._law_related_questions(group)
        if not law_questions:
            return [], []
        try:
            law_policy = next(
                stage
                for stage in self.workflow_catalog.load()["stages"]
                if stage.get("id") == "law_audit"
            )
            current_major = policy_version_major(
                law_policy.get("policyVersion"),
                "law_audit.policyVersion",
            )
            current_questions = [
                question
                for question in law_questions
                if self._recorded_law_audit_major(question) >= current_major
            ]
            return law_questions, current_questions
        except (KeyError, OSError, StopIteration, TypeError, ValueError) as exc:
            raise WorkflowError(
                f"法令監査の作業バージョンを確認できません: {exc}"
            ) from exc

    @staticmethod
    def _current_law_verdict_warnings(
        questions: list[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        for question in questions:
            projected = question.get("projected")
            if not isinstance(projected, Mapping):
                continue
            label = str(
                question.get("questionLabel")
                or question.get("originalQuestionId")
                or question.get("id")
                or "対象問題"
            )
            for issue in law_revision_current_verdict_issues(
                correct_choice_text=projected.get("correctChoiceText"),
                law_revision_facts=projected.get("lawRevisionFacts"),
            ):
                warnings.append(
                    {
                        "code": str(issue.get("code") or "law_audit_verdict_mismatch"),
                        "questionId": str(question.get("id") or ""),
                        "sourceQuestionKey": str(
                            question.get("sourceQuestionKey") or ""
                        ),
                        "field": str(issue.get("field") or "lawRevisionFacts"),
                        "detail": f"{label}: {issue.get('detail') or ''}",
                    }
                )
        return warnings

    @staticmethod
    def _strict_validation_question_ids(
        questions: list[Mapping[str, Any]],
    ) -> list[str]:
        values: list[str] = []
        for question in questions:
            projected = question.get("projected")
            projected = projected if isinstance(projected, Mapping) else {}
            value = str(
                projected.get("originalQuestionId")
                or projected.get("original_question_id")
                or projected.get("public_question_id")
                or question.get("originalQuestionId")
                or ""
            ).strip()
            if value and value not in values:
                values.append(value)
        return values

    def _recorded_law_audit_major(self, question: Mapping[str, Any]) -> int:
        record = self.work_versions.record_for(question)
        stages = record.get("stages") if isinstance(record, Mapping) else None
        law_audit = stages.get("law_audit") if isinstance(stages, Mapping) else None
        if not isinstance(law_audit, Mapping):
            return 0
        return policy_version_major(
            law_audit.get("version", "0.0"),
            "recorded.law_audit.version",
        )

    def _strict_validation_command(
        self,
        qualification: str,
        list_group_id: str,
        *,
        stage: str,
        original_question_ids: list[str],
    ) -> list[str]:
        if stage not in STRICT_LAW_VALIDATION_STAGES:
            raise WorkflowError(f"未対応の厳格検証stageです: {stage}")
        list_group_dir = (
            self.repo_root
            / "output"
            / qualification
            / "questions_json"
            / list_group_id
        )
        command = [
            sys.executable,
            str(
                self.repo_root
                / "scripts"
                / "check"
                / "check_law_revision_fact_coverage.py"
            ),
            "--list-group-dir",
            str(list_group_dir),
            "--stage",
            stage,
            *STRICT_LAW_VALIDATION_FLAGS,
        ]
        for question_id in original_question_ids:
            command.extend(("--original-question-id", question_id))
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
