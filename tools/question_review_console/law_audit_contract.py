from __future__ import annotations

from typing import Any, Mapping


LAW_AUDIT_ISSUES = frozenset(
    {
        "law_audit_metadata_incomplete",
        "law_audit_verdict_mismatch",
        "law_basis_missing",
        "law_hold",
    }
)
LAW_AUDIT_REQUIRED_REPAIR_FIELDS = (
    "explanationText",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "lawReferences",
    "lawRevisionFacts",
)
QUALIFICATION_LAW_AUDIT_REQUEST = "qualification_law_audit"


def is_law_audit_review(review: Mapping[str, Any]) -> bool:
    """Return whether a review must preserve the complete law-audit contract."""

    issue_types = {
        str(value) for value in review.get("issueTypes") or [] if value
    }
    selection = review.get("selection")
    selection_fields = (
        selection.get("fields") if isinstance(selection, Mapping) else []
    )
    fields = {
        str(value).split(".", 1)[0].split("[", 1)[0]
        for value in [
            *(review.get("fields") or []),
            *(selection_fields or []),
        ]
        if value
    }
    evaluation_snapshot = review.get("evaluationSnapshot")
    rework_items = (
        evaluation_snapshot.get("reworkItems")
        if isinstance(evaluation_snapshot, Mapping)
        else []
    )
    return bool(
        issue_types & LAW_AUDIT_ISSUES
        or review.get("requestKind") == QUALIFICATION_LAW_AUDIT_REQUEST
        or any(field.startswith(("law", "isLawRelated")) for field in fields)
        or any(
            isinstance(item, Mapping)
            and str(item.get("stage") or "") == "03b"
            for item in rework_items or []
        )
    )
