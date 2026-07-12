from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.question_review_console.projection import (
    explanation_prefix_matches,
    normalize_verdict,
    record_aliases,
)
from tools.question_review_console.patch_validation import (
    patch_entry_required_warnings,
    projected_required_warnings,
)
from tools.question_review_console.review_store import atomic_write


ALLOWED_FIELDS = {
    "correctChoiceText",
    "explanationText",
    "suggestedQuestions",
    "suggestedQuestionDetails",
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


def _find_entry(records: list[Any], aliases: set[str]) -> dict[str, Any]:
    matches = [
        record
        for record in records
        if isinstance(record, dict) and record_aliases(record) & aliases
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
            if any(normalize_verdict(value) not in {"正しい", "間違い"} for value in correctness):
                raise DirectEditError("正誤は「正しい」又は「間違い」で指定してください。")
            explanations = normalized_changes.get("explanationText", projected.get("explanationText"))
            if not isinstance(explanations, list) or len(explanations) != choice_count:
                raise DirectEditError("正誤変更時は全選択肢の解説が必要です。")
            mismatch = [
                index
                for index, (verdict, explanation) in enumerate(zip(correctness, explanations))
                if not explanation_prefix_matches(verdict, explanation)
            ]
            if mismatch:
                raise DirectEditError(
                    "正誤と解説先頭が一致しません: "
                    + ", ".join(str(index + 1) for index in mismatch)
                )

        final_record = copy.deepcopy(dict(projected))
        final_record.update(copy.deepcopy(normalized_changes))

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
        preview = self.preview(question, changes, reason, expected_state_hash)
        if preview["previewToken"] != preview_token:
            raise DirectEditError("確認後に変更内容が変わりました。もう一度確認してください。")
        normalized = preview["changes"]
        projected = question.get("projected") or {}
        final_record = copy.deepcopy(dict(projected))
        final_record.update(copy.deepcopy(normalized))
        original_id, question_url = self._patch_identity(question)
        aliases = record_aliases(question.get("projected") or {}) | record_aliases(
            question.get("source") or {}
        )
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
            entry = _find_entry(_records_container(payload), aliases)
            entry.update(
                {
                    "explanationText": copy.deepcopy(
                        final_record.get("explanationText")
                    ),
                    "suggestedQuestions": copy.deepcopy(
                        final_record.get("suggestedQuestions") or []
                    ),
                    "suggestedQuestionDetails": copy.deepcopy(
                        final_record.get("suggestedQuestionDetails") or []
                    ),
                    "original_question_id": original_id,
                    "question_url": question_url,
                }
            )
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
                entry = _find_entry(records, aliases)
            except PatchEntryNotFound:
                entry = {
                    "original_question_id": original_id,
                    "question_url": question_url,
                }
                records.append(entry)
            entry.update(
                {
                    "correctChoiceText_changed": True,
                    "correctChoiceText_change_detail": "問題整備コントロールセンターで正誤を修正。",
                    "correctChoiceText_change_reason": reason.strip(),
                    "correctChoiceText": copy.deepcopy(normalized["correctChoiceText"]),
                    "original_question_id": original_id,
                    "question_url": question_url,
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
        for path, payload, _ in pending_writes:
            atomic_write(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            )

        return {
            "changedPaths": changed_paths,
            "diffs": preview["diffs"],
            "validationWarnings": preview["validationWarnings"],
        }

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
            if not isinstance(explanations, list) or len(explanations) != choice_count:
                raise DirectEditError("解説数が選択肢数と一致しません。")
            if any(not str(value or "").strip() for value in explanations):
                raise DirectEditError("空の解説は保存できません。")
        questions = changes.get("suggestedQuestions")
        details = changes.get("suggestedQuestionDetails")
        final_questions = questions if questions is not None else projected.get("suggestedQuestions")
        final_details = details if details is not None else projected.get("suggestedQuestionDetails")
        if questions is not None and (
            not isinstance(questions, list) or any(not str(value or "").strip() for value in questions)
        ):
            raise DirectEditError("補足質問は空でない文字列の配列にしてください。")
        if details is not None:
            if not isinstance(details, list) or any(
                not isinstance(item, dict)
                or set(item) != {"question", "answer"}
                or not str(item.get("question") or "").strip()
                or not str(item.get("answer") or "").strip()
                for item in details
            ):
                raise DirectEditError("補足回答はquestionとanswerだけを持つ配列にしてください。")
        if isinstance(final_questions, list) and isinstance(final_details, list):
            detail_questions = [str(item.get("question") or "") for item in final_details if isinstance(item, dict)]
            if [str(value) for value in final_questions] != detail_questions:
                raise DirectEditError("補足質問と補足回答のquestionが一致しません。")

    def _path_for_stage(self, question: Mapping[str, Any], stage: str) -> Path | None:
        for value in question.get("paths", {}).get("patches") or []:
            if f"/{stage}/" in f"/{value}":
                path = (self.repo_root / value).resolve()
                if path.is_relative_to(self.repo_root) and path.is_file():
                    return path
        return None
