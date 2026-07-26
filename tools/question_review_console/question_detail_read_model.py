from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tools.question_review_console.inventory import QuestionInventory


QUESTION_DETAIL_READ_MODEL_SCHEMA = "question-detail-read-model-cache/v1"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_CACHE_KEY_SEPARATOR = "--"

_DETAIL_FIELDS = (
    "id",
    "sourceQuestionKey",
    "sourceRecordRef",
    "sourceStem",
    "questionLabel",
    "qualification",
    "listGroupId",
    "body",
    "contentUpdatedAt",
    "choiceCount",
    "choicesExtractedFromQuestionBody",
    "isLawRelated",
    "issues",
    "issueCodes",
    "workflow",
    "stateHash",
    "sourceCorrectChoiceComparison",
    "requiredFieldWarnings",
    "qualityWarnings",
)

_PROJECTED_FIELDS = (
    "questionBodyText",
    "originalQuestionBodyText",
    "choiceTextList",
    "originalQuestionChoiceText",
    "correctChoiceText",
    "explanationText",
    "questionType",
    "isCalculationQuestion",
    "questionIntent",
    "answer_result_text",
    "questionSetId",
    "suggestedQuestionDetailsByChoice",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "explanationReferences",
    "lawReferences",
    "lawRevisionFacts",
    "knowledgeText",
    "questionImageUrls",
)

_SOURCE_FIELDS = (
    "questionBodyText",
    "originalQuestionBodyText",
    "choiceTextList",
    "originalQuestionChoiceText",
)

_UPLOAD_READY_FIELDS = (
    "questionId",
    "questionSetId",
    "isChoiceOnly",
    "questionBodyText",
    "questionText",
    "originalQuestionChoiceText",
    "correctChoiceText",
    "explanationText",
    "questionType",
    "explanationReferences",
    "suggestedQuestionDetails",
    "lawReferences",
    "lawRevisionFacts",
    "knowledgeText",
    "questionImageUrls",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(
        microsecond=0
    ).isoformat()


def _safe_segment(value: str, label: str) -> str:
    segment = str(value).strip()
    if (
        not segment
        or segment in {".", ".."}
        or not _SAFE_SEGMENT.fullmatch(segment)
        or _CACHE_KEY_SEPARATOR in segment
    ):
        raise ValueError(f"invalid {label}: {value}")
    return segment


def question_detail_cache_key(
    qualification: str,
    list_group_id: str,
) -> str:
    return (
        f"{_safe_segment(qualification, 'qualification')}"
        f"{_CACHE_KEY_SEPARATOR}"
        f"{_safe_segment(list_group_id, 'listGroupId')}"
    )


def parse_question_detail_cache_key(cache_key: str) -> tuple[str, str]:
    value = str(cache_key)
    parts = value.split(_CACHE_KEY_SEPARATOR)
    if len(parts) != 2:
        raise ValueError(f"invalid question detail cache key: {cache_key}")
    qualification, list_group_id = parts
    if question_detail_cache_key(qualification, list_group_id) != value:
        raise ValueError(f"invalid question detail cache key: {cache_key}")
    return qualification, list_group_id


def _selected_fields(
    value: Mapping[str, Any] | None,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return {
        field: copy.deepcopy(source[field])
        for field in fields
        if field in source
    }


def question_detail_content(question: Mapping[str, Any]) -> dict[str, Any]:
    """Project only data rendered by the read-only question detail screen."""

    content = _selected_fields(question, _DETAIL_FIELDS)
    content["issues"] = list(content.get("issues") or [])
    content["issueCodes"] = list(content.get("issueCodes") or [])
    content["requiredFieldWarnings"] = list(
        content.get("requiredFieldWarnings") or []
    )
    content["qualityWarnings"] = list(content.get("qualityWarnings") or [])
    content["projected"] = _selected_fields(
        question.get("projected"),
        _PROJECTED_FIELDS,
    )
    content["source"] = _selected_fields(
        question.get("source"),
        _SOURCE_FIELDS,
    )
    paths = question.get("paths")
    paths = paths if isinstance(paths, Mapping) else {}
    content["paths"] = {
        "patches": copy.deepcopy(list(paths.get("patches") or [])),
    }
    upload_ready = question.get("uploadReadyDocs")
    content["uploadReadyDocs"] = [
        _selected_fields(document, _UPLOAD_READY_FIELDS)
        for document in (
            upload_ready if isinstance(upload_ready, list) else []
        )
        if isinstance(document, Mapping)
    ]
    content["detailVersion"] = hashlib.sha256(
        json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    return content


def validate_question_detail_read_model(
    cache_key: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("question detail read model must be an object")
    snapshot = copy.deepcopy(dict(value))
    qualification, list_group_id = parse_question_detail_cache_key(cache_key)
    if snapshot.get("cacheKey") != cache_key:
        raise ValueError("question detail read model cache key mismatch")
    if snapshot.get("qualification") != qualification:
        raise ValueError("question detail read model qualification mismatch")
    if snapshot.get("listGroupId") != list_group_id:
        raise ValueError("question detail read model listGroupId mismatch")
    if not isinstance(snapshot.get("questionsById"), Mapping):
        raise ValueError(
            "question detail read model questionsById must be an object"
        )
    return snapshot


def build_question_detail_read_model(
    repo_root: Path,
    qualification: str,
    list_group_id: str,
) -> dict[str, Any]:
    """Build one folder's read-only detail projection outside HTTP requests."""

    cache_key = question_detail_cache_key(qualification, list_group_id)
    inventory = QuestionInventory(repo_root.resolve())
    group = inventory.group(qualification, list_group_id)
    questions = [
        question_detail_content(question)
        for question in group.get("questions") or []
        if isinstance(question, Mapping)
    ]
    return {
        "cacheKey": cache_key,
        "qualification": qualification,
        "listGroupId": list_group_id,
        "generatedAt": _now_iso(),
        "fingerprint": str(group.get("fingerprint") or ""),
        "questionCount": len(questions),
        "questionsById": {
            str(question["id"]): question
            for question in questions
            if question.get("id")
        },
    }
