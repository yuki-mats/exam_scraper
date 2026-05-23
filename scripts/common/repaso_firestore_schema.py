from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable


REPASO_QUESTION_TYPES = {
    "single_choice",
    "true_false",
    "flash_card",
    "fill_in_blank",
    "group_choice",
}


@dataclass(frozen=True)
class CollectionSchema:
    name: str
    required_fields: set[str]
    allowed_fields: set[str]


FOLDER_SCHEMA = CollectionSchema(
    name="folders",
    required_fields={
        "name",
        "isDeleted",
        "isPublic",
        "isOfficial",
        "aggregatedQuestionTags",
        "licenseName",
        "qualificationId",
        "questionCount",
        "createdById",
        "updatedById",
        "createdAt",
        "updatedAt",
    },
    allowed_fields={
        "name",
        "isDeleted",
        "isPublic",
        "isOfficial",
        "aggregatedQuestionTags",
        "licenseName",
        "qualificationId",
        "questionCount",
        "createdById",
        "createdAt",
        "updatedById",
        "updatedByRef",
        "updatedAt",
        "deletedAt",
    },
)


QUESTION_SET_SCHEMA = CollectionSchema(
    name="questionSets",
    required_fields={
        "name",
        "folderId",
        "qualificationId",
        "questionCount",
        "isDeleted",
        "isOfficial",
        "createdById",
        "updatedById",
        "createdAt",
        "updatedAt",
    },
    allowed_fields={
        "name",
        "folderId",
        "folderRef",
        "qualificationId",
        "questionCount",
        "isDeleted",
        "isOfficial",
        "lastStudiedAt",
        "createdById",
        "createdAt",
        "updatedById",
        "updatedAt",
        "deletedAt",
        "sourceSharedQuestionSetId",
    },
)


QUESTION_SCHEMA = CollectionSchema(
    name="questions",
    required_fields={
        "questionSetId",
        "questionText",
        "questionType",
        "qualificationId",
        "isOfficial",
        "isDeleted",
        "isChoiceOnly",
        "isGroupable",
        "questionTags",
        "createdById",
        "updatedById",
        "createdAt",
        "updatedAt",
    },
    allowed_fields={
        "questionSetId",
        "questionSetRef",
        "folderId",
        "listGroupId",
        "originalQuestionId",
        "originalQuestionBodyText",
        "originalQuestionChoiceText",
        "questionBodyText",
        "questionText",
        "questionType",
        "qualificationId",
        "examDate",
        "questionImageUrls",
        "questionImagePaths",
        "originalQuestionChoiceImageUrls",
        "correctChoiceText",
        "correctChoiceImageUrls",
        "correctChoiceImagePaths",
        "incorrectChoice1Text",
        "incorrectChoice2Text",
        "incorrectChoice3Text",
        "incorrectChoice4Text",
        "knowledgeText",
        "explanationText",
        "explanationImageUrls",
        "explanationImagePaths",
        "hintText",
        "hintImageUrls",
        "hintImagePaths",
        "examYear",
        "examSource",
        "questionTags",
        "isOfficial",
        "isDeleted",
        "isChoiceOnly",
        "isGroupable",
        "importKey",
        "fillInBlanks",
        "createdById",
        "updatedById",
        "createdAt",
        "updatedAt",
        "deletedAt",
        "sourceSharedQuestionSetId",
        "sourceSharedQuestionId",
    },
)


def _is_non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_timestamp_like(value: Any) -> bool:
    return isinstance(value, datetime)


def _is_list_of_str(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _ensure_only_allowed_fields(schema: CollectionSchema, doc: dict[str, Any], *, doc_id: str) -> None:
    extra = sorted(set(doc.keys()) - schema.allowed_fields)
    if extra:
        raise ValueError(f"{schema.name}:{doc_id} has disallowed fields: {extra}")


def _ensure_required_fields(schema: CollectionSchema, doc: dict[str, Any], *, doc_id: str) -> None:
    missing = sorted(schema.required_fields - set(doc.keys()))
    if missing:
        raise ValueError(f"{schema.name}:{doc_id} missing required fields: {missing}")


def validate_folder_doc(doc: dict[str, Any], *, doc_id: str) -> None:
    _ensure_required_fields(FOLDER_SCHEMA, doc, doc_id=doc_id)
    _ensure_only_allowed_fields(FOLDER_SCHEMA, doc, doc_id=doc_id)
    if not _is_non_empty_str(doc.get("name")):
        raise ValueError(f"folders:{doc_id} name must be non-empty string")
    for key in ("isDeleted", "isPublic", "isOfficial"):
        if not isinstance(doc.get(key), bool):
            raise ValueError(f"folders:{doc_id} {key} must be bool")
    if not _is_list_of_str(doc.get("aggregatedQuestionTags")):
        raise ValueError(f"folders:{doc_id} aggregatedQuestionTags must be list[str]")
    for key in ("licenseName", "qualificationId", "createdById", "updatedById"):
        if not _is_non_empty_str(doc.get(key)):
            raise ValueError(f"folders:{doc_id} {key} must be non-empty string")
    if not isinstance(doc.get("questionCount"), int) or doc["questionCount"] < 0:
        raise ValueError(f"folders:{doc_id} questionCount must be non-negative int")
    for key in ("createdAt", "updatedAt"):
        if not _is_timestamp_like(doc.get(key)):
            raise ValueError(f"folders:{doc_id} {key} must be datetime")
    if "deletedAt" in doc and doc["deletedAt"] is not None and not _is_timestamp_like(doc["deletedAt"]):
        raise ValueError(f"folders:{doc_id} deletedAt must be datetime|null")


def validate_question_set_doc(doc: dict[str, Any], *, doc_id: str) -> None:
    _ensure_required_fields(QUESTION_SET_SCHEMA, doc, doc_id=doc_id)
    _ensure_only_allowed_fields(QUESTION_SET_SCHEMA, doc, doc_id=doc_id)
    for key in ("name", "folderId", "qualificationId", "createdById", "updatedById"):
        if not _is_non_empty_str(doc.get(key)):
            raise ValueError(f"questionSets:{doc_id} {key} must be non-empty string")
    for key in ("isDeleted", "isOfficial"):
        if not isinstance(doc.get(key), bool):
            raise ValueError(f"questionSets:{doc_id} {key} must be bool")
    if not isinstance(doc.get("questionCount"), int) or doc["questionCount"] < 0:
        raise ValueError(f"questionSets:{doc_id} questionCount must be non-negative int")
    for key in ("createdAt", "updatedAt"):
        if not _is_timestamp_like(doc.get(key)):
            raise ValueError(f"questionSets:{doc_id} {key} must be datetime")
    if "deletedAt" in doc and doc["deletedAt"] is not None and not _is_timestamp_like(doc["deletedAt"]):
        raise ValueError(f"questionSets:{doc_id} deletedAt must be datetime|null")


def validate_question_doc(doc: dict[str, Any], *, doc_id: str) -> None:
    _ensure_required_fields(QUESTION_SCHEMA, doc, doc_id=doc_id)
    _ensure_only_allowed_fields(QUESTION_SCHEMA, doc, doc_id=doc_id)
    if not _is_non_empty_str(doc.get("questionSetId")):
        raise ValueError(f"questions:{doc_id} questionSetId must be non-empty string")
    if not _is_non_empty_str(doc.get("questionText")):
        raise ValueError(f"questions:{doc_id} questionText must be non-empty string")
    if not _is_non_empty_str(doc.get("qualificationId")):
        raise ValueError(f"questions:{doc_id} qualificationId must be non-empty string")
    qt = doc.get("questionType")
    if not isinstance(qt, str) or qt not in REPASO_QUESTION_TYPES:
        raise ValueError(f"questions:{doc_id} questionType must be one of {sorted(REPASO_QUESTION_TYPES)}")
    for key in ("isOfficial", "isDeleted", "isChoiceOnly", "isGroupable"):
        if not isinstance(doc.get(key), bool):
            raise ValueError(f"questions:{doc_id} {key} must be bool")
    if not _is_list_of_str(doc.get("questionTags")):
        raise ValueError(f"questions:{doc_id} questionTags must be list[str]")
    for key in ("createdById", "updatedById"):
        if not _is_non_empty_str(doc.get(key)):
            raise ValueError(f"questions:{doc_id} {key} must be non-empty string")
    for key in ("createdAt", "updatedAt"):
        if not _is_timestamp_like(doc.get(key)):
            raise ValueError(f"questions:{doc_id} {key} must be datetime")
    if "examYear" in doc and doc["examYear"] is not None:
        if not isinstance(doc["examYear"], int):
            raise ValueError(f"questions:{doc_id} examYear must be int|null")
    for list_key in (
        "questionImageUrls",
        "questionImagePaths",
        "originalQuestionChoiceImageUrls",
        "correctChoiceImageUrls",
        "correctChoiceImagePaths",
        "explanationImageUrls",
        "explanationImagePaths",
        "hintImageUrls",
        "hintImagePaths",
    ):
        if list_key in doc and doc[list_key] is not None and not _is_list_of_str(doc[list_key]):
            raise ValueError(f"questions:{doc_id} {list_key} must be list[str]|null")
    if "deletedAt" in doc and doc["deletedAt"] is not None and not _is_timestamp_like(doc["deletedAt"]):
        raise ValueError(f"questions:{doc_id} deletedAt must be datetime|null")

