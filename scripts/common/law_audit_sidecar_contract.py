from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

from scripts.common.question_identity import SOURCE_IDENTITY_BINDING_FIELDS


LAW_AUDIT_SCHEMA_V2 = "law-revision-audit/v2"
LAW_AUDIT_STATUSES = {
    "same_as_current",
    "updated_to_current_law",
    "hold",
    "not_law_related",
}
LAW_AUDIT_REVIEW_STATES = {
    "primary_checked",
    "secondary_verified",
    "tertiary_verified",
    "needs_secondary_review",
    "needs_tertiary_review",
}

_REVIEW_STATE_ALIASES = {
    "primary_verified": "primary_checked",
}
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalize_audit_review_state(value: Any) -> str:
    normalized = _text(value).lower().replace("-", "_").replace(" ", "_")
    return _REVIEW_STATE_ALIASES.get(normalized, normalized)


def law_audit_sidecar_metadata_errors(
    entry: dict[str, Any] | Any,
    *,
    expected_choice_count: int | None = None,
    expected_qualification: str | None = None,
    expected_list_group_id: str | None = None,
) -> list[str]:
    """保存時とmaterialize時に共有するsidecar v2 metadata契約を検証する。"""

    if not isinstance(entry, dict):
        return ["監査行がobjectではありません。"]
    errors: list[str] = []
    if entry.get("schemaVersion") != LAW_AUDIT_SCHEMA_V2:
        errors.append("schemaVersionがlaw-revision-audit/v2ではありません。")

    required_text_fields = (
        "qualification",
        "listGroupId",
        *SOURCE_IDENTITY_BINDING_FIELDS,
        "auditedAt",
        "nextAuditDueAt",
        "auditMethodVersion",
        "auditInputHash",
        "auditRunId",
        "lawCorpusSnapshotId",
        "primaryAuditRunId",
        "secondaryAuditRunId",
        "reconciliationStatus",
        "auditStatus",
        "reviewState",
        "sourceSummary",
    )
    for field in required_text_fields:
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            errors.append(f"{field}が非空stringではありません。")
    if (
        expected_qualification is not None
        and _text(entry.get("qualification")) != expected_qualification
    ):
        errors.append("qualificationが対象資格と一致しません。")
    if (
        expected_list_group_id is not None
        and _text(entry.get("listGroupId")) != expected_list_group_id
    ):
        errors.append("listGroupIdが対象年度・区分と一致しません。")

    exam_year = entry.get("examYear")
    if isinstance(exam_year, bool):
        errors.append("examYearが4桁の西暦ではありません。")
    else:
        try:
            normalized_year = int(str(exam_year))
        except (TypeError, ValueError):
            normalized_year = 0
        if not 1900 <= normalized_year <= 2200:
            errors.append("examYearが4桁の西暦ではありません。")

    audited_at = _text(entry.get("auditedAt"))
    if audited_at:
        try:
            parsed_datetime = datetime.fromisoformat(audited_at.replace("Z", "+00:00"))
            if parsed_datetime.tzinfo is None:
                raise ValueError
        except ValueError:
            errors.append("auditedAtがtimezone付きISO-8601 datetimeではありません。")
    next_due = _text(entry.get("nextAuditDueAt"))
    if next_due:
        try:
            date.fromisoformat(next_due)
        except ValueError:
            errors.append("nextAuditDueAtがISO-8601 dateではありません。")

    audit_input_hash = _text(entry.get("auditInputHash"))
    if audit_input_hash and not _SHA256_PATTERN.fullmatch(audit_input_hash):
        errors.append("auditInputHashがsha256:<64 hex>ではありません。")
    source_ref = _text(entry.get("sourceRecordRef"))
    if source_ref:
        relative_text, separator, index_text = source_ref.rpartition("#")
        relative = PurePosixPath(relative_text)
        if (
            separator != "#"
            or not index_text.isdigit()
            or relative.is_absolute()
            or relative.suffix.lower() != ".json"
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            errors.append(
                "sourceRecordRefが00_source相対JSON path#record indexではありません。"
            )

    audit_status = _text(entry.get("auditStatus"))
    if audit_status and audit_status not in LAW_AUDIT_STATUSES:
        errors.append("auditStatusが未定義です。")
    review_state = normalize_audit_review_state(entry.get("reviewState"))
    if review_state and review_state not in LAW_AUDIT_REVIEW_STATES:
        errors.append("reviewStateが未定義です。")

    decisions: dict[str, list[Any]] = {}
    for field in ("examTimeDecision", "currentLawDecision"):
        value = entry.get(field)
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
        ):
            errors.append(f"{field}が選択肢順の非空string配列ではありません。")
        else:
            decisions[field] = value
    if len(decisions) == 2:
        if len(decisions["examTimeDecision"]) != len(
            decisions["currentLawDecision"]
        ):
            errors.append("二つの選択肢判定の件数が一致しません。")
        if (
            expected_choice_count is not None
            and len(decisions["examTimeDecision"]) != expected_choice_count
        ):
            errors.append(
                "選択肢判定の件数がsource/patchの選択肢数と一致しません。"
            )

    if not isinstance(entry.get("isLawRelated"), bool):
        errors.append("isLawRelatedがboolではありません。")
    if not isinstance(entry.get("userVisibleNoticeRequired"), bool):
        errors.append("userVisibleNoticeRequiredがboolではありません。")
    if not isinstance(entry.get("noticeReason"), str):
        errors.append("noticeReasonがstringではありません。")
    if not isinstance(entry.get("lawReferences"), list):
        errors.append("lawReferencesが配列ではありません。")
    if not isinstance(entry.get("remainingRisk"), str):
        errors.append("remainingRiskがstringではありません。")

    tertiary_run_id = entry.get("tertiaryAuditRunId")
    if "tertiaryAuditRunId" not in entry or not (
        tertiary_run_id is None
        or (isinstance(tertiary_run_id, str) and bool(tertiary_run_id.strip()))
    ):
        errors.append("tertiaryAuditRunIdがnull又は非空stringではありません。")
    if audit_status == "updated_to_current_law":
        if review_state != "tertiary_verified":
            errors.append(
                "updated_to_current_lawのreviewStateがtertiary_verifiedではありません。"
            )
        if not _text(tertiary_run_id):
            errors.append("updated_to_current_lawにtertiaryAuditRunIdがありません。")
        if entry.get("userVisibleNoticeRequired") is not True:
            errors.append(
                "updated_to_current_lawのuserVisibleNoticeRequiredがtrueではありません。"
            )
        if not _text(entry.get("noticeReason")):
            errors.append("updated_to_current_lawのnoticeReasonがありません。")
    elif entry.get("userVisibleNoticeRequired") is True and not _text(
        entry.get("noticeReason")
    ):
        errors.append("注記が必要なのにnoticeReasonがありません。")
    return errors
