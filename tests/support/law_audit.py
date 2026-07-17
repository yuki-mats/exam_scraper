from __future__ import annotations

from scripts.common.law_audit_sidecar_contract import LAW_AUDIT_SCHEMA_V2


def valid_v2_audit_row(
    review_id: str,
    source_key: str,
    *,
    source_ref: str = "question_2026_1.json#0",
    choice_count: int = 1,
    **overrides: object,
) -> dict[str, object]:
    """Return a complete valid v2 sidecar row for focused test overrides."""

    row: dict[str, object] = {
        "schemaVersion": LAW_AUDIT_SCHEMA_V2,
        "qualification": "sample",
        "listGroupId": "2026",
        "reviewQuestionId": review_id,
        "sourceQuestionKey": source_key,
        "sourceRecordRef": source_ref,
        "examYear": 2026,
        "isLawRelated": False,
        "auditedAt": "2026-07-17T12:00:00+09:00",
        "nextAuditDueAt": "2027-07-17",
        "auditMethodVersion": "law-grounded-audit/v2",
        "auditInputHash": "sha256:" + "a" * 64,
        "auditRunId": "audit-run-1",
        "lawCorpusSnapshotId": "egov-2026-07-17",
        "primaryAuditRunId": "primary-run-1",
        "secondaryAuditRunId": "secondary-run-1",
        "tertiaryAuditRunId": None,
        "reconciliationStatus": "matched",
        "auditStatus": "not_law_related",
        "reviewState": "secondary_verified",
        "examTimeDecision": ["正しい"] * choice_count,
        "currentLawDecision": ["正しい"] * choice_count,
        "userVisibleNoticeRequired": False,
        "noticeReason": "",
        "lawReferences": [],
        "sourceSummary": "sourceと一次情報を照合した。",
        "remainingRisk": "",
    }
    row.update(overrides)
    if row["auditStatus"] == "updated_to_current_law":
        if "reviewState" not in overrides:
            row["reviewState"] = "tertiary_verified"
        if "tertiaryAuditRunId" not in overrides:
            row["tertiaryAuditRunId"] = "tertiary-run-1"
        if "userVisibleNoticeRequired" not in overrides:
            row["userVisibleNoticeRequired"] = True
        if "noticeReason" not in overrides:
            row["noticeReason"] = "現行法に合わせて正誤を更新した。"
    return row
