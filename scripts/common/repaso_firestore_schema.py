from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from scripts.common.question_learning_patterns import QUESTION_LEARNING_PATTERN_IDS


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
        "licenseNames",
        "qualificationIds",
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
        "licenseNames",
        "qualificationIds",
        "questionCount",
        "createdById",
        "createdAt",
        "updatedById",
        "updatedByRef",
        "updatedAt",
        "deletedAt",
        "canonicalFolderId",
        "sourceSharedFolderId",
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
        "canonicalFolderId",
        "canonicalQuestionSetId",
        "sourceSharedFolderId",
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
        "questionLearningPatternId",
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
        "suggestedQuestions",
        "suggestedQuestionDetails",
        "explanationReferences",
        "lawReferences",
        "lawRevisionFacts",
        "isLawRelated",
        "lawGroundedExplanationNotNeeded",
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
        "canonicalFolderId",
        "canonicalQuestionSetId",
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


def _is_law_reference_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for reference in value:
        if not isinstance(reference, dict):
            return False
        for key, item in reference.items():
            if key == "choiceIndex":
                if not isinstance(item, int):
                    return False
                continue
            if key == "externalPrimarySource":
                if not isinstance(item, bool):
                    return False
                continue
            if not isinstance(item, str):
                return False
        if reference.get("verificationStatus") == "verified":
            law_id = reference.get("lawId")
            article = reference.get("article")
            external_source = (
                reference.get("appLinkMode") == "source_url"
                and reference.get("externalPrimarySource") is True
                and _is_non_empty_str(reference.get("sourceUrl"))
            )
            if (not external_source and not _is_non_empty_str(law_id)) or not _is_non_empty_str(article):
                return False
    return True


def _is_suggested_question_detail_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for detail in value:
        if not isinstance(detail, dict):
            return False
        question = detail.get("question")
        answer = detail.get("answer")
        if not _is_non_empty_str(question):
            return False
        if not _is_non_empty_str(answer):
            return False
        extra = sorted(set(detail.keys()) - {"question", "answer"})
        if extra:
            return False
    return True


def _is_explanation_reference_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for reference in value:
        if not isinstance(reference, dict):
            return False
        if set(reference) - {
            "title",
            "sourceUrl",
            "referenceDate",
            "choiceIndex",
        }:
            return False
        if any(
            not _is_non_empty_str(reference.get(field))
            for field in ("title", "sourceUrl", "referenceDate")
        ):
            return False
        if not str(reference["sourceUrl"]).startswith("https://"):
            return False
        choice_index = reference.get("choiceIndex")
        if choice_index is not None and (
            not isinstance(choice_index, int)
            or isinstance(choice_index, bool)
            or choice_index < 0
        ):
            return False
    return True


LAW_REVISION_AUDIT_STATUSES = {
    "same_as_current",
    "updated_to_current_law",
    "hold",
    "not_law_related",
}

LAW_REVISION_FACT_KEYS = {
    "auditStatus",
    "reviewState",
    "auditedAt",
    "nextAuditDueAt",
    "auditMethodVersion",
    "auditInputHash",
    "auditRunId",
    "lawCorpusSnapshotId",
    "primaryAuditRunId",
    "secondaryAuditRunId",
    "tertiaryAuditRunId",
    "reconciliationStatus",
    "sourceEvidenceVersionId",
    "evidenceBindingHash",
    "examTime",
    "current",
    "differenceFacts",
    "answerImpactFacts",
    "notes",
    "evidenceSummary",
}

LAW_REVISION_SNAPSHOT_KEYS = {
    "correctChoiceText",
    "lawId",
    "lawRevisionId",
    "lawTitle",
    "article",
    "paragraph",
    "item",
    "subitem",
    "referenceDate",
    "effectiveDate",
    "verificationStatus",
    "articleText",
    "articleTextHash",
    "sourceUrl",
}

LAW_REVISION_EVIDENCE_SUMMARY_KEYS = {
    "verdict",
    "explanationText",
    "differenceSummary",
    "promptContext",
    "displayRefIds",
    "refs",
}

LAW_REVISION_EVIDENCE_REF_KEYS = {
    "refId",
    "lawTimeScope",
    "relation",
    "primaryBasis",
    "lawId",
    "lawRevisionId",
    "lawTitle",
    "elm",
    "encodedElm",
    "rootArticleElm",
    "article",
    "paragraph",
    "item",
    "subitem",
    "highlightElms",
    "articleTextHash",
    "textHash",
}


def _is_optional_string_map(
    value: Any,
    *,
    allowed_keys: set[str],
    list_keys: set[str] | None = None,
    bool_keys: set[str] | None = None,
) -> bool:
    if not isinstance(value, dict):
        return False
    list_keys = list_keys or set()
    bool_keys = bool_keys or set()
    if set(value.keys()) - allowed_keys:
        return False
    for key, item in value.items():
        if key in list_keys:
            if not _is_list_of_str(item):
                return False
        elif key in bool_keys:
            if not isinstance(item, bool):
                return False
        elif not _is_non_empty_str(item):
            return False
    return True


def _is_law_revision_evidence_summary(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value.keys()) - LAW_REVISION_EVIDENCE_SUMMARY_KEYS:
        return False
    for key, item in value.items():
        if key == "displayRefIds":
            if not _is_list_of_str(item):
                return False
        elif key == "refs":
            if not isinstance(item, list):
                return False
            for ref in item:
                if not _is_optional_string_map(
                    ref,
                    allowed_keys=LAW_REVISION_EVIDENCE_REF_KEYS,
                    list_keys={"highlightElms"},
                    bool_keys={"primaryBasis"},
                ):
                    return False
        elif not _is_non_empty_str(item):
            return False
    return True


def _is_law_revision_facts(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value.keys()) - LAW_REVISION_FACT_KEYS:
        return False
    audit_status = value.get("auditStatus")
    if audit_status not in LAW_REVISION_AUDIT_STATUSES:
        return False
    for key, item in value.items():
        if key == "auditStatus":
            continue
        if key in {
            "reviewState",
            "auditedAt",
            "nextAuditDueAt",
            "auditMethodVersion",
            "auditInputHash",
            "auditRunId",
            "lawCorpusSnapshotId",
            "primaryAuditRunId",
            "secondaryAuditRunId",
            "tertiaryAuditRunId",
            "reconciliationStatus",
            "sourceEvidenceVersionId",
            "evidenceBindingHash",
        }:
            if not _is_non_empty_str(item):
                return False
        elif key in {"examTime", "current"}:
            if not _is_optional_string_map(item, allowed_keys=LAW_REVISION_SNAPSHOT_KEYS):
                return False
        elif key in {"differenceFacts", "answerImpactFacts", "notes"}:
            if not _is_list_of_str(item):
                return False
        elif key == "evidenceSummary":
            if not _is_law_revision_evidence_summary(item):
                return False
    return True


def is_law_revision_facts_shape(
    value: Any,
    *,
    allow_choice_verdict_lists: bool = False,
) -> bool:
    """Validate one fact object against the public Firestore contract.

    Question-level patch and merged records may keep every choice verdict in
    one snapshot list. Firestore splits those verdicts into scalar documents,
    so shape validation normalizes only that representation difference.
    """
    if _is_law_revision_facts(value):
        return True
    if not allow_choice_verdict_lists or not isinstance(value, dict):
        return False

    normalized = dict(value)
    normalized_any = False
    for snapshot_key in ("examTime", "current"):
        snapshot = value.get(snapshot_key)
        if not isinstance(snapshot, dict):
            continue
        verdicts = snapshot.get("correctChoiceText")
        if (
            not isinstance(verdicts, list)
            or not verdicts
            or any(not _is_non_empty_str(verdict) for verdict in verdicts)
        ):
            continue
        normalized_snapshot = dict(snapshot)
        normalized_snapshot["correctChoiceText"] = verdicts[0]
        normalized[snapshot_key] = normalized_snapshot
        normalized_any = True

    return normalized_any and _is_law_revision_facts(normalized)


def law_revision_facts_shape_errors(
    value: Any,
    *,
    allow_choice_verdict_lists: bool = False,
) -> list[str]:
    """Explain why one fact object misses the public Firestore contract."""
    if not isinstance(value, dict):
        return ["objectではありません"]

    errors: list[str] = []
    extra_keys = sorted(set(value) - LAW_REVISION_FACT_KEYS)
    if extra_keys:
        errors.append("未対応field: " + ", ".join(extra_keys))
    if value.get("auditStatus") not in LAW_REVISION_AUDIT_STATUSES:
        errors.append("auditStatusが許可値ではありません")

    required_string_fields = {
        "reviewState",
        "auditedAt",
        "nextAuditDueAt",
        "auditMethodVersion",
        "auditInputHash",
        "auditRunId",
        "lawCorpusSnapshotId",
        "primaryAuditRunId",
        "secondaryAuditRunId",
        "tertiaryAuditRunId",
        "reconciliationStatus",
        "sourceEvidenceVersionId",
        "evidenceBindingHash",
    }
    for key in sorted(set(value) & required_string_fields):
        if not _is_non_empty_str(value[key]):
            errors.append(f"{key}が非空文字列ではありません")

    for key in ("examTime", "current"):
        if key not in value:
            continue
        snapshot = value[key]
        if not isinstance(snapshot, dict):
            errors.append(f"{key}がobjectではありません")
            continue
        snapshot_extra_keys = sorted(set(snapshot) - LAW_REVISION_SNAPSHOT_KEYS)
        if snapshot_extra_keys:
            errors.append(
                f"{key}の未対応field: " + ", ".join(snapshot_extra_keys)
            )
        for field, item in snapshot.items():
            if field not in LAW_REVISION_SNAPSHOT_KEYS:
                continue
            if (
                field == "correctChoiceText"
                and allow_choice_verdict_lists
                and isinstance(item, list)
            ):
                if not item or any(not _is_non_empty_str(entry) for entry in item):
                    errors.append(
                        f"{key}.correctChoiceTextが非空文字列の配列ではありません"
                    )
            elif not _is_non_empty_str(item):
                errors.append(f"{key}.{field}が非空文字列ではありません")

    for key in ("differenceFacts", "answerImpactFacts", "notes"):
        if key in value and not _is_list_of_str(value[key]):
            errors.append(f"{key}が文字列配列ではありません")

    if "evidenceSummary" in value:
        summary = value["evidenceSummary"]
        if not isinstance(summary, dict):
            errors.append("evidenceSummaryがobjectではありません")
        else:
            summary_extra_keys = sorted(
                set(summary) - LAW_REVISION_EVIDENCE_SUMMARY_KEYS
            )
            if summary_extra_keys:
                errors.append(
                    "evidenceSummaryの未対応field: "
                    + ", ".join(summary_extra_keys)
                )
            for key, item in summary.items():
                if key not in LAW_REVISION_EVIDENCE_SUMMARY_KEYS:
                    continue
                if key == "displayRefIds":
                    if not _is_list_of_str(item):
                        errors.append(
                            "evidenceSummary.displayRefIdsが文字列配列ではありません"
                        )
                elif key == "refs":
                    if not isinstance(item, list):
                        errors.append("evidenceSummary.refsが配列ではありません")
                        continue
                    for index, ref in enumerate(item, start=1):
                        if not isinstance(ref, dict):
                            errors.append(
                                f"evidenceSummary.refs[{index}]がobjectではありません"
                            )
                            continue
                        ref_extra_keys = sorted(
                            set(ref) - LAW_REVISION_EVIDENCE_REF_KEYS
                        )
                        if ref_extra_keys:
                            errors.append(
                                f"evidenceSummary.refs[{index}]の未対応field: "
                                + ", ".join(ref_extra_keys)
                            )
                        for field, ref_value in ref.items():
                            if field not in LAW_REVISION_EVIDENCE_REF_KEYS:
                                continue
                            if field == "highlightElms":
                                if not _is_list_of_str(ref_value):
                                    errors.append(
                                        "evidenceSummary."
                                        f"refs[{index}].highlightElmsが"
                                        "文字列配列ではありません"
                                    )
                            elif field == "primaryBasis":
                                if not isinstance(ref_value, bool):
                                    errors.append(
                                        "evidenceSummary."
                                        f"refs[{index}].primaryBasisが"
                                        "booleanではありません"
                                    )
                            elif not _is_non_empty_str(ref_value):
                                errors.append(
                                    "evidenceSummary."
                                    f"refs[{index}].{field}が"
                                    "非空文字列ではありません"
                                )
                elif not _is_non_empty_str(item):
                    errors.append(
                        f"evidenceSummary.{key}が非空文字列ではありません"
                    )

    if not errors and not is_law_revision_facts_shape(
        value,
        allow_choice_verdict_lists=allow_choice_verdict_lists,
    ):
        errors.append("公開契約に一致しません")
    return errors


def _ensure_only_allowed_fields(schema: CollectionSchema, doc: dict[str, Any], *, doc_id: str) -> None:
    extra = sorted(set(doc.keys()) - schema.allowed_fields)
    if extra:
        raise ValueError(f"{schema.name}:{doc_id} has disallowed fields: {extra}")


def _ensure_required_fields(schema: CollectionSchema, doc: dict[str, Any], *, doc_id: str) -> None:
    missing = sorted(schema.required_fields - set(doc.keys()))
    if missing:
        raise ValueError(f"{schema.name}:{doc_id} missing required fields: {missing}")


def _ensure_optional_string_fields(
    doc: dict[str, Any],
    *,
    doc_id: str,
    collection_name: str,
    keys: Iterable[str],
) -> None:
    for key in keys:
        if key in doc and not _is_non_empty_str(doc.get(key)):
            raise ValueError(f"{collection_name}:{doc_id} {key} must be non-empty string")


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
    for key in ("licenseNames", "qualificationIds"):
        if not _is_list_of_str(doc.get(key)) or not doc.get(key):
            raise ValueError(f"folders:{doc_id} {key} must be non-empty list[str]")
    _ensure_optional_string_fields(
        doc,
        doc_id=doc_id,
        collection_name="folders",
        keys=("canonicalFolderId", "sourceSharedFolderId"),
    )
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
    _ensure_optional_string_fields(
        doc,
        doc_id=doc_id,
        collection_name="questionSets",
        keys=(
            "canonicalFolderId",
            "canonicalQuestionSetId",
            "sourceSharedFolderId",
            "sourceSharedQuestionSetId",
        ),
    )
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
    learning_pattern_id = doc.get("questionLearningPatternId")
    if learning_pattern_id is not None and learning_pattern_id not in (
        QUESTION_LEARNING_PATTERN_IDS
    ):
        raise ValueError(
            f"questions:{doc_id} questionLearningPatternId must be one of "
            f"{sorted(QUESTION_LEARNING_PATTERN_IDS)}"
        )
    for key in ("isOfficial", "isDeleted", "isChoiceOnly", "isGroupable"):
        if not isinstance(doc.get(key), bool):
            raise ValueError(f"questions:{doc_id} {key} must be bool")
    if doc.get("isChoiceOnly") is True:
        forbidden = [
            key
            for key in (
                "questionLearningPatternId",
                "explanationText",
                "explanationReferences",
                "suggestedQuestions",
                "suggestedQuestionDetails",
            )
            if key in doc
        ]
        if forbidden:
            raise ValueError(
                f"questions:{doc_id} isChoiceOnly documents must omit "
                + ", ".join(forbidden)
            )
    if not _is_list_of_str(doc.get("questionTags")):
        raise ValueError(f"questions:{doc_id} questionTags must be list[str]")
    for key in ("createdById", "updatedById"):
        if not _is_non_empty_str(doc.get(key)):
            raise ValueError(f"questions:{doc_id} {key} must be non-empty string")
    _ensure_optional_string_fields(
        doc,
        doc_id=doc_id,
        collection_name="questions",
        keys=(
            "canonicalFolderId",
            "canonicalQuestionSetId",
            "sourceSharedQuestionSetId",
            "sourceSharedQuestionId",
        ),
    )
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
        "suggestedQuestions",
    ):
        if list_key in doc and doc[list_key] is not None and not _is_list_of_str(doc[list_key]):
            raise ValueError(f"questions:{doc_id} {list_key} must be list[str]|null")
    if "lawReferences" in doc and doc["lawReferences"] is not None and not _is_law_reference_list(doc["lawReferences"]):
        raise ValueError(f"questions:{doc_id} lawReferences must be list<object>|null")
    if (
        "explanationReferences" in doc
        and doc["explanationReferences"] is not None
        and not _is_explanation_reference_list(doc["explanationReferences"])
    ):
        raise ValueError(
            f"questions:{doc_id} explanationReferences must be list<object>|null"
        )
    if "lawRevisionFacts" in doc and doc["lawRevisionFacts"] is not None and not _is_law_revision_facts(doc["lawRevisionFacts"]):
        raise ValueError(f"questions:{doc_id} lawRevisionFacts must be object|null")
    if (
        "isLawRelated" in doc
        and doc["isLawRelated"] is not None
        and not isinstance(doc["isLawRelated"], bool)
    ):
        raise ValueError(f"questions:{doc_id} isLawRelated must be bool|null")
    if (
        "lawGroundedExplanationNotNeeded" in doc
        and doc["lawGroundedExplanationNotNeeded"] is not None
        and not isinstance(doc["lawGroundedExplanationNotNeeded"], bool)
    ):
        raise ValueError(
            f"questions:{doc_id} lawGroundedExplanationNotNeeded must be bool|null"
        )
    if "suggestedQuestionDetails" in doc and doc["suggestedQuestionDetails"] is not None and not _is_suggested_question_detail_list(doc["suggestedQuestionDetails"]):
        raise ValueError(f"questions:{doc_id} suggestedQuestionDetails must be list<object>|null")
    suggested_questions = doc.get("suggestedQuestions")
    suggested_details = doc.get("suggestedQuestionDetails")
    if suggested_questions is not None or suggested_details is not None:
        if not isinstance(suggested_questions, list) or not isinstance(
            suggested_details, list
        ):
            raise ValueError(
                f"questions:{doc_id} suggestedQuestions and suggestedQuestionDetails must be stored together"
            )
        if len(suggested_details) > 3:
            raise ValueError(
                f"questions:{doc_id} suggestedQuestionDetails must contain at most 3 entries"
            )
        detail_questions = [detail["question"] for detail in suggested_details]
        if suggested_questions != detail_questions:
            raise ValueError(
                f"questions:{doc_id} suggestedQuestions must be derived from suggestedQuestionDetails"
            )
    if "deletedAt" in doc and doc["deletedAt"] is not None and not _is_timestamp_like(doc["deletedAt"]):
        raise ValueError(f"questions:{doc_id} deletedAt must be datetime|null")
