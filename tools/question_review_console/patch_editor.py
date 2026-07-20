from __future__ import annotations

import copy
import hashlib
import json
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from scripts.common.question_identity import SourceIdentityBinding
from scripts.common.suggested_question_contract import (
    public_choice_indexes,
    validation_errors as suggested_question_validation_errors,
)
from scripts.common.explanation_contract import (
    explanation_shape_errors,
    uses_question_level_explanation,
)
from tools.question_review_console.explanation_quality import (
    explanation_style_issues,
)
from tools.question_review_console.projection import (
    normalize_verdict,
    record_aliases,
)
from tools.question_review_console.patch_validation import (
    patch_entry_required_warnings,
    projected_required_warnings,
)
from tools.question_review_console.review_store import atomic_write
from tools.question_review_console.write_transaction import (
    WriteTransactionError,
    capture_write_snapshot,
    restore_write_snapshot,
)


ALLOWED_FIELDS = {
    "correctChoiceText",
    "explanationText",
    "suggestedQuestionDetailsByChoice",
}
EXPLANATION_FIELDS = ALLOWED_FIELDS - {"correctChoiceText"}


class DirectEditError(ValueError):
    def __init__(self, message: str, *, codex_required: bool = False):
        super().__init__(message)
        self.codex_required = codex_required


class PatchEntryNotFound(DirectEditError):
    pass


def _records_container(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("patched_questions", "question_bodies", "questions"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise DirectEditError("patchの問題配列を特定できません。", codex_required=True)


def _find_entry(
    records: list[Any],
    aliases: set[str],
    source_binding: SourceIdentityBinding,
) -> dict[str, Any]:
    if source_binding.is_complete():
        exact_matches = [
            record
            for record in records
            if isinstance(record, dict)
            and SourceIdentityBinding.from_mapping(record) == source_binding
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise DirectEditError(
                f"完全一致するpatch entryが重複しています（{len(exact_matches)}件）。",
                codex_required=True,
            )

    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and not SourceIdentityBinding.from_mapping(record).is_complete()
        and record_aliases(record) & aliases
    ]
    unique = {id(record): record for record in matches}
    if not unique:
        raise PatchEntryNotFound(
            "対象patch entryがありません。",
            codex_required=True,
        )
    if len(unique) != 1:
        raise DirectEditError(
            f"対象patch entryを一意に特定できません（{len(unique)}件）。",
            codex_required=True,
        )
    return next(iter(unique.values()))


def _preview_token(state_hash: str, changes: Mapping[str, Any], reason: str) -> str:
    value = json.dumps(
        {"stateHash": state_hash, "changes": changes, "reason": reason},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PatchEditor:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.transactions_root = (
            self.repo_root
            / "output/question_review_console/direct_edit_transactions"
        )
        self._recovery_errors: list[str] = []
        self._recover_pending_transactions()

    def preview(
        self,
        question: Mapping[str, Any],
        changes: Mapping[str, Any],
        reason: str,
        expected_state_hash: str,
    ) -> dict[str, Any]:
        if expected_state_hash != question.get("stateHash"):
            raise DirectEditError("表示後に対象問題が更新されました。再読込してください。")
        unknown = sorted(set(changes) - ALLOWED_FIELDS)
        if unknown:
            raise DirectEditError(
                f"直接編集できないfieldです: {', '.join(unknown)}",
                codex_required=True,
            )
        if not changes:
            raise DirectEditError("変更内容がありません。")
        projected = question.get("projected") or {}
        normalized_changes = copy.deepcopy(dict(changes))
        if "correctChoiceText" in normalized_changes and isinstance(
            normalized_changes["correctChoiceText"], list
        ):
            normalized_changes["correctChoiceText"] = [
                normalize_verdict(value)
                for value in normalized_changes["correctChoiceText"]
            ]
        diffs = []
        for field, value in list(normalized_changes.items()):
            if projected.get(field) == value:
                normalized_changes.pop(field)
                continue
            diffs.append({"field": field, "before": projected.get(field), "after": value})
        if not normalized_changes:
            raise DirectEditError("現在値と同じです。")

        choices = projected.get("choiceTextList")
        choice_count = len(choices) if isinstance(choices, list) else 0
        self._validate_explanation_fields(projected, normalized_changes, choice_count)
        if "correctChoiceText" in normalized_changes:
            if not reason.strip():
                raise DirectEditError("正誤変更には理由が必要です。")
            if projected.get("isLawRelated") is True:
                raise DirectEditError(
                    "法令問題の正誤変更は法令根拠と監査情報の更新が必要です。",
                    codex_required=True,
                )
            correctness = normalized_changes["correctChoiceText"]
            if not isinstance(correctness, list) or len(correctness) != choice_count:
                raise DirectEditError("正誤配列の数が選択肢数と一致しません。")
            if any(
                normalize_verdict(value) not in {"正しい", "間違い"}
                for value in correctness
            ):
                raise DirectEditError("正誤は「正しい」又は「間違い」で指定してください。")
            explanations = normalized_changes.get(
                "explanationText", projected.get("explanationText")
            )
            if explanation_shape_errors(
                explanations,
                question_type=projected.get("questionType"),
                choice_count=choice_count,
            ):
                raise DirectEditError("正誤変更時は問題形式に合う基本解説が必要です。")

        final_record = copy.deepcopy(dict(projected))
        final_record.update(copy.deepcopy(normalized_changes))
        if {"correctChoiceText", "explanationText"} & normalized_changes.keys():
            explanations = final_record.get("explanationText")
            correctness = final_record.get("correctChoiceText")
            if isinstance(explanations, list):
                explanation_issues = explanation_style_issues(
                    explanations,
                    correctness if isinstance(correctness, list) else None,
                    choice_texts=final_record.get("choiceTextList"),
                    require_verdict_prefix=(
                        choice_count > 0
                        and not uses_question_level_explanation(
                            final_record.get("questionType")
                        )
                    ),
                    question_type=final_record.get("questionType"),
                )
                if explanation_issues:
                    raise DirectEditError(
                        "解説形式を確認してください: "
                        + " ".join(explanation_issues[:3])
                    )

        return {
            "changes": normalized_changes,
            "diffs": diffs,
            "previewToken": _preview_token(expected_state_hash, normalized_changes, reason),
            "codexRequired": False,
            "validationWarnings": projected_required_warnings(final_record),
        }

    def apply(
        self,
        question: Mapping[str, Any],
        changes: Mapping[str, Any],
        reason: str,
        expected_state_hash: str,
        preview_token: str,
    ) -> dict[str, Any]:
        if self._recovery_errors:
            raise DirectEditError(
                "前回の直接編集を開始前状態へ復元できません。技術ログを確認してください。"
            )
        preview = self.preview(question, changes, reason, expected_state_hash)
        if preview["previewToken"] != preview_token:
            raise DirectEditError("確認後に変更内容が変わりました。もう一度確認してください。")
        normalized = preview["changes"]
        projected = question.get("projected") or {}
        final_record = copy.deepcopy(dict(projected))
        final_record.update(copy.deepcopy(normalized))
        original_id, question_url = self._patch_identity(question)
        source_binding = SourceIdentityBinding.from_values(
            question.get("sourceQuestionKey"),
            question.get("originalQuestionId") or original_id,
            question.get("sourceRecordRef"),
        )
        patch_identity = {
            "original_question_id": original_id,
            "question_url": question_url,
        }
        if source_binding.is_complete():
            patch_identity.update(source_binding.as_mapping())
        patch_identity = {
            field: value for field, value in patch_identity.items() if value
        }
        aliases = record_aliases(question.get("projected") or {}) | record_aliases(
            question.get("source") or {}
        )
        aliases.update(patch_identity.values())
        changed_paths: list[str] = []
        pending_writes: list[tuple[Path, Any, bytes | None]] = []

        explanation_changes = {
            field: value for field, value in normalized.items() if field in EXPLANATION_FIELDS
        }
        if explanation_changes:
            explanation_path = self._path_for_stage(question, "21_explanationText_added")
            if explanation_path is None:
                raise DirectEditError(
                    "対応する21_explanationText_addedがありません。",
                    codex_required=True,
                )
            original_bytes = explanation_path.read_bytes()
            payload = json.loads(original_bytes.decode("utf-8"))
            entry = _find_entry(
                _records_container(payload), aliases, source_binding
            )
            entry.update(
                {
                    "explanationText": copy.deepcopy(
                        final_record.get("explanationText")
                    ),
                    "suggestedQuestionDetailsByChoice": copy.deepcopy(
                        final_record.get("suggestedQuestionDetailsByChoice") or []
                    ),
                    **patch_identity,
                }
            )
            entry.pop("suggestedQuestions", None)
            entry.pop("suggestedQuestionDetails", None)
            self._validate_patch_entry(
                entry,
                "explanation",
            )
            pending_writes.append((explanation_path, payload, original_bytes))
            changed_paths.append(str(explanation_path.relative_to(self.repo_root)))

        if "correctChoiceText" in normalized:
            correct_path = self._path_for_stage(question, "23_correctChoiceText_fixed")
            if correct_path is None:
                source_path = self.repo_root / str(question["paths"]["source"])
                filename = source_path.name.replace(".json", "_correctChoiceText_fixed.json")
                correct_path = source_path.parent.parent / "23_correctChoiceText_fixed" / filename
                payload: Any = []
                original_bytes = None
            else:
                original_bytes = correct_path.read_bytes()
                payload = json.loads(original_bytes.decode("utf-8"))
            records = _records_container(payload)
            try:
                entry = _find_entry(records, aliases, source_binding)
            except PatchEntryNotFound:
                entry = dict(patch_identity)
                records.append(entry)
            entry.update(
                {
                    "correctChoiceText_changed": True,
                    "correctChoiceText_change_detail": "問題整備システムで正誤を修正。",
                    "correctChoiceText_change_reason": reason.strip(),
                    "correctChoiceText": copy.deepcopy(normalized["correctChoiceText"]),
                    **patch_identity,
                }
            )
            self._validate_patch_entry(
                entry,
                "correctChoice",
            )
            pending_writes.append((correct_path, payload, original_bytes))
            changed_paths.append(str(correct_path.relative_to(self.repo_root)))

        for path, _, expected_bytes in pending_writes:
            current_bytes = path.read_bytes() if path.exists() else None
            if current_bytes != expected_bytes:
                raise DirectEditError(
                    f"保存直前に{path.name}が更新されました。再読込してください。"
                )
        try:
            transaction_dir, snapshot = self._begin_transaction(
                [path for path, _, _ in pending_writes],
                changed_paths,
            )
        except (OSError, WriteTransactionError) as exc:
            raise DirectEditError(
                "patch保存前の開始状態を記録できないため、変更していません。"
            ) from exc
        try:
            for path, payload, _ in pending_writes:
                self._write_patch_payload(path, payload)
            self._finish_transaction(
                transaction_dir,
                snapshot,
                status="committed",
                changed_paths=changed_paths,
            )
        except Exception as exc:  # noqa: BLE001
            try:
                restore_write_snapshot(
                    self.repo_root,
                    snapshot,
                    transaction_dir / "baseline_files",
                )
                self._finish_transaction(
                    transaction_dir,
                    snapshot,
                    status="rolled_back",
                    changed_paths=changed_paths,
                    error=str(exc),
                )
            except Exception as rollback_error:  # noqa: BLE001
                self._recovery_errors.append(
                    f"{transaction_dir.relative_to(self.repo_root)}/manifest.json: "
                    f"{rollback_error}"
                )
                raise DirectEditError(
                    "patch保存に失敗し、開始前状態も復元できませんでした: "
                    f"{rollback_error}"
                ) from exc
            raise DirectEditError(
                "patch保存に失敗したため、変更を開始前状態へ戻しました。"
            ) from exc

        return {
            "changedPaths": changed_paths,
            "diffs": preview["diffs"],
            "validationWarnings": preview["validationWarnings"],
        }

    def _begin_transaction(
        self,
        paths: list[Path],
        changed_paths: list[str],
    ) -> tuple[Path, dict[str, Any]]:
        now = datetime.now(timezone.utc).astimezone()
        transaction_id = (
            now.strftime("%Y%m%dT%H%M%S%f")
            + "-"
            + secrets.token_hex(4)
        )
        transaction_dir = self.transactions_root / transaction_id
        transaction_dir.mkdir(parents=True, exist_ok=False)
        try:
            snapshot = capture_write_snapshot(
                self.repo_root,
                paths,
                transaction_dir / "baseline_files",
            )
            self._write_transaction_manifest(
                transaction_dir,
                {
                    "schemaVersion": "question-direct-edit-transaction/v1",
                    "transactionId": transaction_id,
                    "status": "prepared",
                    "createdAt": now.isoformat(timespec="seconds"),
                    "changedPaths": list(changed_paths),
                    "writeTransaction": snapshot,
                },
            )
        except Exception:
            shutil.rmtree(transaction_dir, ignore_errors=True)
            raise
        return transaction_dir, snapshot

    def _finish_transaction(
        self,
        transaction_dir: Path,
        snapshot: Mapping[str, Any],
        *,
        status: str,
        changed_paths: list[str],
        error: str = "",
    ) -> None:
        manifest_path = transaction_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "status": status,
                "finishedAt": datetime.now(timezone.utc)
                .astimezone()
                .isoformat(timespec="seconds"),
                "changedPaths": list(changed_paths),
                "error": error or None,
                "writeTransaction": dict(snapshot),
            }
        )
        self._write_transaction_manifest(transaction_dir, manifest)
        shutil.rmtree(transaction_dir / "baseline_files", ignore_errors=True)

    @staticmethod
    def _write_patch_payload(path: Path, payload: Any) -> None:
        atomic_write(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    @staticmethod
    def _write_transaction_manifest(
        transaction_dir: Path,
        manifest: Mapping[str, Any],
    ) -> None:
        atomic_write(
            transaction_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )

    def _recover_pending_transactions(self) -> None:
        if not self.transactions_root.is_dir():
            return
        for manifest_path in sorted(self.transactions_root.glob("*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    not isinstance(manifest, Mapping)
                    or manifest.get("schemaVersion")
                    != "question-direct-edit-transaction/v1"
                    or manifest.get("status") != "prepared"
                ):
                    continue
                snapshot = manifest.get("writeTransaction")
                if not isinstance(snapshot, Mapping):
                    raise WriteTransactionError(
                        "直接編集transactionのbaselineがありません。"
                    )
                transaction_dir = manifest_path.parent
                restore_write_snapshot(
                    self.repo_root,
                    snapshot,
                    transaction_dir / "baseline_files",
                )
                self._finish_transaction(
                    transaction_dir,
                    snapshot,
                    status="rolled_back",
                    changed_paths=[
                        str(value)
                        for value in manifest.get("changedPaths") or []
                    ],
                    error="ローカルUI再起動時に未確定の直接編集を復元しました。",
                )
            except Exception as exc:  # noqa: BLE001
                self._recovery_errors.append(
                    f"{manifest_path.relative_to(self.repo_root)}: {exc}"
                )

    @staticmethod
    def _patch_identity(question: Mapping[str, Any]) -> tuple[str, str]:
        source = question.get("source") or {}
        original_id = str(
            source.get("original_question_id")
            or source.get("public_question_id")
            or question.get("originalQuestionId")
            or ""
        ).strip()
        question_url = str(source.get("question_url") or "").strip()
        missing = []
        if not original_id:
            missing.append("original_question_id")
        if not question_url:
            missing.append("question_url")
        if missing:
            raise DirectEditError(
                "patch保存に必要な識別fieldを特定できません: "
                + ", ".join(missing),
                codex_required=True,
            )
        return original_id, question_url

    @staticmethod
    def _validate_patch_entry(
        entry: Mapping[str, Any], stage: str
    ) -> None:
        warnings = patch_entry_required_warnings(entry, stage)
        if warnings:
            raise DirectEditError(
                "patchの必須fieldが不足しています: "
                + " / ".join(warning["detail"] for warning in warnings),
                codex_required=True,
            )

    @staticmethod
    def _validate_explanation_fields(
        projected: Mapping[str, Any], changes: Mapping[str, Any], choice_count: int
    ) -> None:
        explanations = changes.get("explanationText")
        if explanations is not None:
            errors = explanation_shape_errors(
                explanations,
                question_type=projected.get("questionType"),
                choice_count=choice_count,
            )
            if errors:
                raise DirectEditError("解説形式が問題形式と一致しません: " + " / ".join(errors))
        details_by_choice = changes.get("suggestedQuestionDetailsByChoice")
        if details_by_choice is not None:
            final_correct = changes.get(
                "correctChoiceText", projected.get("correctChoiceText")
            )
            errors = suggested_question_validation_errors(
                details_by_choice,
                choice_count=choice_count,
                allowed_choice_indexes=public_choice_indexes(
                    projected.get("questionType"),
                    final_correct,
                    choice_count,
                ),
            )
            if errors:
                raise DirectEditError(
                    "補足質問は公開対象の選択肢ごとに回答付きで最大3件です: "
                    + " / ".join(errors)
                )

    def _path_for_stage(self, question: Mapping[str, Any], stage: str) -> Path | None:
        for value in question.get("paths", {}).get("patches") or []:
            if f"/{stage}/" in f"/{value}":
                path = (self.repo_root / value).resolve()
                if path.is_relative_to(self.repo_root) and path.is_file():
                    return path
        return None
