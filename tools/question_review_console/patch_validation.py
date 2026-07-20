from __future__ import annotations

from typing import Any, Mapping

from tools.question_review_console.law_audit_quality import (
    law_revision_current_verdict_issues,
)
from tools.question_review_console.projection import normalize_verdict
from scripts.common.independent_question_images import (
    INDEPENDENT_IMAGE_REQUIRED_FIELD,
    INDEPENDENT_QUESTION_EXAM_SOURCE,
    published_image_urls,
)
from scripts.common.explanation_contract import explanation_shape_errors


PATCH_REQUIRED_FIELDS = {
    "explanation": {
        "explanationText",
        "suggestedQuestionDetailsByChoice",
        "original_question_id",
        "question_url",
    },
    "correctChoice": {
        "correctChoiceText",
        "original_question_id",
    },
}

UPLOAD_REQUIRED_STRING_FIELDS = (
    "questionId",
    "originalQuestionBodyText",
    "questionSetId",
    "questionText",
    "questionType",
    "qualificationId",
    "correctChoiceText",
    "explanationText",
)

UPLOAD_REQUIRED_BOOLEAN_FIELDS = (
    "isOfficial",
    "isDeleted",
    "isChoiceOnly",
    "isGroupable",
)


def patch_entry_required_warnings(
    entry: Mapping[str, Any],
    stage: str,
    *,
    require_question_url: bool = True,
) -> list[dict[str, str]]:
    required = set(PATCH_REQUIRED_FIELDS.get(stage, set()))
    if not require_question_url:
        required.discard("question_url")
    warnings = []
    for field in sorted(required):
        if field not in entry or entry.get(field) is None:
            warnings.append(
                {"field": field, "detail": f"{stage} patchに{field}がありません。"}
            )
    for field in ("original_question_id", "question_url"):
        if field in required and not str(entry.get(field) or "").strip():
            if not any(warning["field"] == field for warning in warnings):
                warnings.append(
                    {"field": field, "detail": f"{stage} patchの{field}が空です。"}
                )
    return warnings


def projected_required_warnings(record: Mapping[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []

    def add(field: str, detail: str) -> None:
        warnings.append({"field": field, "detail": detail})

    if not str(record.get("questionBodyText") or "").strip():
        add("questionBodyText", "問題文がありません。")
    if not str(record.get("questionType") or "").strip():
        add("questionType", "questionTypeがありません。")

    choices = record.get("choiceTextList")
    if not isinstance(choices, list) or not choices:
        add("choiceTextList", "選択肢がありません。")
        choice_count = 0
    else:
        choice_count = len(choices)
        if any(not str(value or "").strip() for value in choices):
            add("choiceTextList", "空の選択肢があります。")

    correctness = record.get("correctChoiceText")
    if not isinstance(correctness, list) or len(correctness) != choice_count:
        add("correctChoiceText", "正誤数が選択肢数と一致しません。")
    elif any(
        normalize_verdict(value) not in {"正しい", "間違い"}
        for value in correctness
    ):
        add("correctChoiceText", "未確定又は不正な正誤があります。")

    explanations = record.get("explanationText")
    explanation_errors = explanation_shape_errors(
        explanations,
        question_type=record.get("questionType"),
        choice_count=choice_count,
    )
    if explanation_errors:
        add("explanationText", " / ".join(explanation_errors))

    if str(record.get("examSource") or "").strip() == INDEPENDENT_QUESTION_EXAM_SOURCE:
        image_required = record.get(INDEPENDENT_IMAGE_REQUIRED_FIELD)
        if not isinstance(image_required, bool):
            add(
                INDEPENDENT_IMAGE_REQUIRED_FIELD,
                "独自問題の画像要否が未検証です。05独自問題化を再実行してください。",
            )
        elif image_required and not published_image_urls(record):
            add(
                "questionImageStorageUrls",
                "画像が必要な独自問題に独自生成画像がありません。",
            )

    return warnings


def upload_document_required_warnings(
    document: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return every missing upload-ready field needed for a usable question doc."""
    warnings: list[dict[str, str]] = []
    document_id = str(document.get("questionId") or "document IDなし")

    def add(field: str, detail: str) -> None:
        warnings.append(
            {
                "stage": "upload-ready",
                "documentId": document_id,
                "field": field,
                "dataPath": field,
                "detail": detail,
            }
        )

    is_choice_only = document.get("isChoiceOnly") is True
    for field in UPLOAD_REQUIRED_STRING_FIELDS:
        if field == "explanationText" and is_choice_only:
            continue
        if field not in document:
            add(field, f"upload-ready documentに{field}がありません。")
        elif not str(document.get(field) or "").strip():
            add(field, f"upload-ready documentの{field}が空です。")

    if is_choice_only and "explanationText" in document:
        add("explanationText", "isChoiceOnly=true documentにはexplanationTextを保存しません。")

    if "correctChoiceText" in document and normalize_verdict(
        document.get("correctChoiceText")
    ) not in {"正しい", "間違い"}:
        if not any(warning["field"] == "correctChoiceText" for warning in warnings):
            add("correctChoiceText", "upload-ready documentの正誤が不正です。")

    for field in UPLOAD_REQUIRED_BOOLEAN_FIELDS:
        if field not in document:
            add(field, f"upload-ready documentに{field}がありません。")
        elif not isinstance(document.get(field), bool):
            add(field, f"upload-ready documentの{field}がbooleanではありません。")

    tags = document.get("questionTags")
    if "questionTags" not in document:
        add("questionTags", "upload-ready documentにquestionTagsがありません。")
    elif not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        add("questionTags", "upload-ready documentのquestionTagsがlist[str]ではありません。")

    if document.get("questionType") == "true_false" and document.get("isChoiceOnly") is False:
        choice_text = str(document.get("originalQuestionChoiceText") or "").strip()
        choice_images = document.get("originalQuestionChoiceImageUrls")
        has_choice_image = isinstance(choice_images, list) and any(
            str(value or "").strip() for value in choice_images
        )
        if not choice_text and not has_choice_image:
            add(
                "originalQuestionChoiceText",
                "true_falseのupload-ready documentに選択肢文字列又は選択肢画像がありません。",
            )

    if (
        document.get("isOfficial") is True
        and str(document.get("examSource") or "").strip()
        == INDEPENDENT_QUESTION_EXAM_SOURCE
        and not isinstance(document.get(INDEPENDENT_IMAGE_REQUIRED_FIELD), bool)
    ):
        add(
            INDEPENDENT_IMAGE_REQUIRED_FIELD,
            "独自問題の画像要否が未検証です。05独自問題化から再生成してください。",
        )

    return warnings


def law_audit_quality_warnings(
    document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate law-audit metadata without conflating it with upload schema."""
    if document.get("isLawRelated") is not True:
        return []

    warnings: list[dict[str, Any]] = []
    document_id = str(document.get("questionId") or "document IDなし")

    def add(code: str, field: str, detail: str) -> None:
        warnings.append(
            {
                "code": code,
                "category": "law_audit_quality",
                "stage": "upload-ready",
                "documentId": document_id,
                "field": field,
                "dataPath": field,
                "detail": detail,
                "blocksSync": False,
                "blocksPublish": True,
            }
        )

    references = document.get("lawReferences")
    if not isinstance(references, list) or not references:
        add(
            "law_audit_metadata_incomplete",
            "lawReferences",
            "法令問題のlawReferencesがありません。",
        )

    facts = document.get("lawRevisionFacts")
    if not isinstance(facts, Mapping) or not facts:
        add(
            "law_audit_metadata_incomplete",
            "lawRevisionFacts",
            "法令問題のlawRevisionFactsがありません。",
        )
        return warnings

    if not str(facts.get("auditStatus") or "").strip():
        add(
            "law_audit_metadata_incomplete",
            "lawRevisionFacts.auditStatus",
            "lawRevisionFacts.auditStatusがありません。",
        )

    summary = facts.get("evidenceSummary")
    if not isinstance(summary, Mapping) or not summary:
        add(
            "law_audit_metadata_incomplete",
            "lawRevisionFacts.evidenceSummary",
            "lawRevisionFacts.evidenceSummaryがありません。",
        )

    for issue in law_revision_current_verdict_issues(
        correct_choice_text=document.get("correctChoiceText"),
        law_revision_facts=facts,
    ):
        add(issue["code"], issue["field"], issue["detail"])

    return warnings
