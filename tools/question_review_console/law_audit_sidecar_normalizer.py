from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from scripts.common.law_audit_sidecar_contract import (
    LAW_AUDIT_SCHEMA_V2,
    law_audit_sidecar_metadata_errors,
)
from scripts.common.question_identity import (
    SourceIdentityBinding,
    source_identity_aliases,
    workflow_identity_aliases,
)
from tools.question_review_console.question_patch_proposal import (
    _canonical_file_locks,
)
from tools.question_review_console.review_store import atomic_write


class LawAuditSidecarNormalizationError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _question_aliases(question: Mapping[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for field in (
        "id",
        "uiQuestionId",
        "originalQuestionId",
        "reviewQuestionId",
        "sourceQuestionKey",
        "sourceRecordRef",
    ):
        if question.get(field):
            aliases.add(str(question[field]))
    for field in ("source", "projected"):
        record = question.get(field)
        if isinstance(record, Mapping):
            aliases.update(source_identity_aliases(record))
            aliases.update(workflow_identity_aliases(record))
    return aliases


def _question_binding(question: Mapping[str, Any]) -> SourceIdentityBinding:
    binding = SourceIdentityBinding.from_mapping(question)
    if binding.is_complete():
        return binding
    source = question.get("source")
    if isinstance(source, Mapping):
        binding = SourceIdentityBinding.from_mapping(source)
    if not binding.is_complete():
        raise LawAuditSidecarNormalizationError(
            "問題inventoryのsource identityが不完全です。"
        )
    return binding


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LawAuditSidecarNormalizationError(
                f"法令監査sidecarの{line_number}行目がJSONではありません: {path}"
            ) from exc
        if not isinstance(value, dict):
            raise LawAuditSidecarNormalizationError(
                f"法令監査sidecarの{line_number}行目がobjectではありません: {path}"
            )
        records.append(value)
    return records


def _render_jsonl(records: Iterable[Mapping[str, Any]]) -> str:
    return "".join(
        json.dumps(dict(record), ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )


def normalize_law_audit_sidecars(
    repo_root: Path,
    qualification: str,
    groups: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Canonicalize only sidecar schema/identity before a UI-started law run."""

    resolved_repo = repo_root.resolve()
    plans: list[dict[str, Any]] = []
    for list_group_id, raw_questions in sorted(groups.items()):
        questions = [dict(value) for value in raw_questions]
        relative = (
            Path("output")
            / qualification
            / "review"
            / "law_revision_audit"
            / f"{list_group_id}_law_revision_audit.jsonl"
        )
        path = resolved_repo / relative
        if not path.is_file():
            continue
        if path.is_symlink():
            raise LawAuditSidecarNormalizationError(
                f"法令監査sidecarにsymlinkは使用できません: {relative}"
            )

        choice_count_by_binding: dict[SourceIdentityBinding, int] = {}
        owners_by_alias: dict[str, set[SourceIdentityBinding]] = {}
        for question in questions:
            binding = _question_binding(question)
            projected = question.get("projected")
            source = question.get("source")
            record = (
                projected
                if isinstance(projected, Mapping)
                else source
                if isinstance(source, Mapping)
                else {}
            )
            choices = record.get("choiceTextList")
            if isinstance(choices, list):
                choice_count_by_binding[binding] = len(choices)
            for alias in _question_aliases(question) | set(binding.as_tuple()):
                owners_by_alias.setdefault(alias, set()).add(binding)

        before_bytes = path.read_bytes()
        records = _load_jsonl(path)
        normalized: list[dict[str, Any]] = []
        seen_bindings: set[SourceIdentityBinding] = set()
        changed_rows = 0
        deferred_metadata_rows = 0
        for row_index, row in enumerate(records):
            row_binding = SourceIdentityBinding.from_mapping(row)
            if row_binding.is_complete() and row_binding in choice_count_by_binding:
                candidates = {row_binding}
            else:
                aliases = (
                    source_identity_aliases(row)
                    | workflow_identity_aliases(row)
                    | {
                        str(row.get(field))
                        for field in (
                            "reviewQuestionId",
                            "sourceQuestionKey",
                            "sourceRecordRef",
                        )
                        if row.get(field)
                    }
                )
                candidates = {
                    binding
                    for alias in aliases
                    for binding in owners_by_alias.get(alias, set())
                }
            if len(candidates) != 1:
                raise LawAuditSidecarNormalizationError(
                    "法令監査sidecarを一問へ一意に対応できません: "
                    f"{relative}#{row_index} / candidates={len(candidates)}"
                )
            binding = next(iter(candidates))
            if binding in seen_bindings:
                raise LawAuditSidecarNormalizationError(
                    f"法令監査sidecarに同一問題が重複しています: {relative}"
                )
            seen_bindings.add(binding)
            updated = dict(row)
            updated.update(binding.as_mapping())
            promoted = {
                **updated,
                "schemaVersion": LAW_AUDIT_SCHEMA_V2,
            }
            promotion_errors = law_audit_sidecar_metadata_errors(
                promoted,
                expected_choice_count=choice_count_by_binding.get(binding),
                expected_qualification=qualification,
                expected_list_group_id=str(list_group_id),
            )
            if not promotion_errors:
                updated = promoted
            else:
                deferred_metadata_rows += 1
            if str(updated.get("qualification") or "") != qualification:
                raise LawAuditSidecarNormalizationError(
                    f"法令監査sidecarのqualificationが一致しません: "
                    f"{relative}#{row_index}"
                )
            if str(updated.get("listGroupId") or "") != str(list_group_id):
                raise LawAuditSidecarNormalizationError(
                    f"法令監査sidecarのlistGroupIdが一致しません: "
                    f"{relative}#{row_index}"
                )
            if updated != row:
                changed_rows += 1
            normalized.append(updated)

        after_text = _render_jsonl(normalized)
        after_bytes = after_text.encode("utf-8")
        plans.append(
            {
                "relative": relative,
                "path": path,
                "beforeBytes": before_bytes,
                "beforeHash": _sha256_bytes(before_bytes),
                "afterText": after_text,
                "afterBytes": after_bytes,
                "afterHash": _sha256_bytes(after_bytes),
                "rowCount": len(records),
                "changedRowCount": changed_rows,
                "deferredMetadataRowCount": deferred_metadata_rows,
            }
        )

    changed_plans = [
        plan for plan in plans if plan["beforeBytes"] != plan["afterBytes"]
    ]
    if changed_plans:
        with _canonical_file_locks(
            resolved_repo,
            [plan["relative"] for plan in changed_plans],
        ):
            for plan in changed_plans:
                if (
                    not plan["path"].is_file()
                    or plan["path"].is_symlink()
                    or _sha256_bytes(plan["path"].read_bytes())
                    != plan["beforeHash"]
                ):
                    raise LawAuditSidecarNormalizationError(
                        "正規化直前に法令監査sidecarが更新されました: "
                        f"{plan['relative']}"
                    )
            for plan in changed_plans:
                atomic_write(plan["path"], plan["afterText"])
            for plan in changed_plans:
                if (
                    not plan["path"].is_file()
                    or _sha256_bytes(plan["path"].read_bytes())
                    != plan["afterHash"]
                ):
                    raise LawAuditSidecarNormalizationError(
                        "法令監査sidecarの正規化結果を再読検証できません: "
                        f"{plan['relative']}"
                    )

    return {
        "schemaVersion": "law-audit-sidecar-normalization-receipt/v1",
        "status": "succeeded",
        "qualification": qualification,
        "fileCount": len(plans),
        "changedFileCount": len(changed_plans),
        "rowCount": sum(int(plan["rowCount"]) for plan in plans),
        "changedRowCount": sum(
            int(plan["changedRowCount"]) for plan in plans
        ),
        "deferredMetadataRowCount": sum(
            int(plan["deferredMetadataRowCount"]) for plan in plans
        ),
        "files": [
            {
                "path": plan["relative"].as_posix(),
                "beforeHash": plan["beforeHash"],
                "afterHash": plan["afterHash"],
                "rowCount": plan["rowCount"],
                "changedRowCount": plan["changedRowCount"],
                "deferredMetadataRowCount": plan[
                    "deferredMetadataRowCount"
                ],
            }
            for plan in plans
        ],
    }
