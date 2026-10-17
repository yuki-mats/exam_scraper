import json
import hashlib
import hmac
import os
import re
import firebase_admin
from firebase_admin import firestore
from datetime import datetime
import argparse
from pathlib import Path
import sys
from copy import deepcopy

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.upload.firebase_credentials import (  # noqa: E402
    DEFAULT_PROJECT_ID,
    initialize_firebase_app,
)
from scripts.common.repaso_firestore_schema import validate_question_doc, is_law_revision_facts_shape
from scripts.common.fill_in_blank_contract import validate_fill_in_blank_question
from scripts.common.independent_question_images import (
    validate_independent_upload_image_gate,
)

PROJECT_ID = DEFAULT_PROJECT_ID
UPDATED_BY_ID = "aMpBCmAEGSQPbhUMzbHvFiM1cYK2"
CREATED_BY_ID = UPDATED_BY_ID
BATCH_SIZE = 500  # Firestoreバッチ書き込みの上限
CONFIG_DOC_ID = "08zYvCuKUcvGTNYqehrm"
OFFICIAL_EXAM_YEARS_FIELD = "official_exam_years_by_qualification"
WRITE_FIELDS_KEY = "writeFields"
EXPECTED_UPDATE_TIMES_KEY = "expectedUpdateTimes"
ATOMIC_MAINTENANCE_SCHEMA = "question-maintenance-atomic-upload/v1"
ATOMIC_MAINTENANCE_RESULT_SCHEMA = "question-maintenance-atomic-upload-result/v1"
ATOMIC_MAINTENANCE_KEYS = frozenset((
    "schemaVersion", "originalQuestionId", "questions", "writeFieldsByDocument",
    EXPECTED_UPDATE_TIMES_KEY, "expectedLiveFingerprint",
))
EXPLANATION_DETAILS_WRITE_FIELDS = ("explanationText", "suggestedQuestionDetails")
DETAILS_ONLY_WRITE_FIELDS = ("suggestedQuestionDetails",)
LAW_FACTS_DETAILS_WRITE_FIELDS = ("explanationText", "lawRevisionFacts", "suggestedQuestionDetails")
PRESERVED_SUGGESTION_MASKS = (EXPLANATION_DETAILS_WRITE_FIELDS, DETAILS_ONLY_WRITE_FIELDS,
                            LAW_FACTS_DETAILS_WRITE_FIELDS)
# Replacing both sides of the suggestion mirror is an explicit atomic-only
# contract. Legacy masks continue to preserve the saved question list.
ATOMIC_SUGGESTION_REPLACEMENT_MASKS = (
    ("suggestedQuestions", "suggestedQuestionDetails"),
    ("explanationText", "suggestedQuestions", "suggestedQuestionDetails"),
    ("explanationText", "lawRevisionFacts", "suggestedQuestions", "suggestedQuestionDetails"),
)
ATOMIC_MAINTENANCE_WRITE_FIELD_SETS = (*PRESERVED_SUGGESTION_MASKS, *ATOMIC_SUGGESTION_REPLACEMENT_MASKS)
ALLOWED_PATCH_WRITE_FIELD_SETS = (
    ("explanationText",),
    EXPLANATION_DETAILS_WRITE_FIELDS,
    DETAILS_ONLY_WRITE_FIELDS,
    LAW_FACTS_DETAILS_WRITE_FIELDS,
    ("suggestedQuestions", "suggestedQuestionDetails"),
    ("qualificationId", "listGroupId"),
    ("correctChoiceText", "explanationText"),
    (
        "originalQuestionBodyText",
        "originalQuestionChoiceText",
        "correctChoiceText",
        "explanationText",
    ),
    (
        "originalQuestionBodyText",
        "originalQuestionChoiceText",
        "questionText",
        "correctChoiceText",
        "explanationText",
    ),
)


def init_firestore(credentials_json: Path | None = None):
    """Firestoreを初期化"""
    initialize_firebase_app(project_id=PROJECT_ID, credentials_json=credentials_json)
    return firestore.client()

def infer_qualification_id_from_json_path(json_file_path: str) -> str:
    """
    output/<qualification>/questions_json/... から qualificationId を推定する。
    推定できない場合は空文字を返す（strict validate で落ちる）。
    """
    path = Path(json_file_path).expanduser().resolve()
    parts = list(path.parts)
    for idx, part in enumerate(parts):
        if part == "output" and idx + 1 < len(parts):
            return str(parts[idx + 1])
    return ""


DOC_COMPARE_KEYS = (
    "questionSetId",
    "listGroupId",
    "originalQuestionId",
    "originalQuestionBodyText",
    "questionBodyText",
    "originalQuestionChoiceText",
    "originalQuestionChoiceImageUrls",
    "questionText",
    "questionType",
    "fillInBlankContent",
    "qualificationId",
    "correctChoiceText",
    "questionLearningPatternId",
    "explanationText",
    "knowledgeText",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "explanationReferences",
    "lawReferences",
    "lawRevisionFacts",
    "isLawRelated",
    "lawGroundedExplanationNotNeeded",
    "examYear",
    "examSource",
    "questionTags",
    "questionImageUrls",
    "importKey",
    "isOfficial",
    "isDeleted",
    "isChoiceOnly",
    "isGroupable",
)
PRODUCTION_CLIENT_OMITTED_FIELDS = (
    # App Store 公開版 2.17.6 は未知fieldを拒否するため、対応版の公開確認まで
    # Firestore question documentへ書き込まない。整備patchには保持する。
    "explanationReferences",
    "questionLearningPatternId",
)
GX_PRIVATE_AUTHORING_FIELDS = frozenset(
    {
        "explanationReferences", "lawReferences", "lawRevisionFacts",
        "referenceUrls", "sourceUrl", "knowledgeText",
    }
)
GX_PUBLIC_ATTRIBUTION_PATTERN = re.compile(r"(?:出典|参考文献|参照先)\s*[:：]")


def validate_gx_authoring_privacy(questions: list[dict]) -> None:
    """Keep GX research notes and source attribution off public Firestore docs."""

    for question in questions:
        if not isinstance(question, dict) or question.get("qualificationId") != "gx-kentei":
            continue
        qid = str(question.get("questionId") or "unknown")
        if question.get("examSource") != "独自問題":
            raise ValueError(f"GX question must be an Anki Plus original: {qid}")
        disallowed = sorted(
            field
            for field in GX_PRIVATE_AUTHORING_FIELDS.intersection(question)
            if question[field] not in (None, "", [], {})
        )
        if disallowed:
            raise ValueError(f"GX private authoring fields must stay local: {qid}: {disallowed}")
        public = build_doc_data_base(question)
        for field in (
            "originalQuestionBodyText", "questionBodyText", "originalQuestionChoiceText",
            "questionText", "explanationText", "suggestedQuestions", "suggestedQuestionDetails",
        ):
            value = public.get(field)
            serialized = json.dumps(value, ensure_ascii=False) if value is not None else ""
            if (
                "http://" in serialized
                or "https://" in serialized
                or "ankiplus://" in serialized
                or GX_PUBLIC_ATTRIBUTION_PATTERN.search(serialized)
            ):
                raise ValueError(f"GX public content includes source attribution: {qid}: {field}")


CHOICE_ONLY_OMITTED_FIELDS = (
    "questionLearningPatternId",
    "explanationText",
    "explanationReferences",
    "suggestedQuestions",
    "suggestedQuestionDetails",
)
EXISTING_DOC_FIELD_PATHS = tuple(
    dict.fromkeys(
        (
            *DOC_COMPARE_KEYS,
            *PRODUCTION_CLIENT_OMITTED_FIELDS,
            "createdAt",
            "createdById",
        )
    )
)

_TRUTHY_CORRECT = {"正しい", "正解", "○", "〇", "true", "True", "TRUE"}
_TRUTHY_INCORRECT = {"間違い", "不正解", "誤り", "×", "false", "False", "FALSE"}


def _normalize_correct_choice_text(value: str) -> str:
    text = (value or "").strip()
    if text in _TRUTHY_CORRECT:
        return "正しい"
    if text in _TRUTHY_INCORRECT:
        return "間違い"
    return text


def validate_required_question_fields(questions: list[dict], source_label: str) -> None:
    """
    upload 前に最低限の整合性チェックを行う（例外時は ValueError）。

    - originalQuestionBodyText は必須（空白のみ不可）
    - true_false の grouped candidate（isChoiceOnly=false）では、
      originalQuestionChoiceText または originalQuestionChoiceImageUrls のいずれかが必須
    - correctChoiceText は "正解/不正解" 等を "正しい/間違い" に正規化
    - 同一 originalQuestionId の true_false grouped candidate が複数ある場合は isGroupable=true を付与
    """
    if not isinstance(questions, list):
        raise ValueError(f"questions is not a list: {source_label}")

    grouped_candidates_by_original: dict[str, list[dict]] = {}

    for q in questions:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("questionId") or "").strip()
        if not qid:
            raise ValueError(f"questionId is required: {source_label}")

        validate_fill_in_blank_question(q, doc_id=qid)

        body = str(q.get("originalQuestionBodyText") or "")
        if not body.strip():
            raise ValueError(f"originalQuestionBodyText is required: {qid}")

        qset_id = str(q.get("questionSetId") or "").strip()
        if not qset_id:
            raise ValueError(f"questionSetId is required: {qid}")

        qtext = str(q.get("questionText") or "")
        if not qtext.strip():
            raise ValueError(f"questionText is required: {qid}")

        qtype = str(q.get("questionType") or "").strip()
        if not qtype:
            raise ValueError(f"questionType is required: {qid}")

        qual_id = q.get("qualificationId")
        if not isinstance(qual_id, str) or not qual_id.strip():
            raise ValueError(f"qualificationId is required: {qid}")

        tags = q.get("questionTags")
        if tags is None:
            q["questionTags"] = []
        elif not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
            raise ValueError(f"questionTags must be list[str]: {qid}")

        if "correctChoiceText" in q:
            q["correctChoiceText"] = _normalize_correct_choice_text(str(q.get("correctChoiceText") or ""))

        question_type = str(q.get("questionType") or "")
        is_choice_only = bool(q.get("isChoiceOnly", False))
        if question_type == "true_false" and not is_choice_only:
            original_id = str(q.get("originalQuestionId") or "").strip()
            if original_id:
                grouped_candidates_by_original.setdefault(original_id, []).append(q)

            choice_text = str(q.get("originalQuestionChoiceText") or "")
            choice_images = q.get("originalQuestionChoiceImageUrls")
            has_images = isinstance(choice_images, list) and any(str(u).strip() for u in choice_images)
            if not choice_text.strip() and not has_images:
                raise ValueError(
                    f"originalQuestionChoiceText or originalQuestionChoiceImageUrls is required: {qid}"
                )

    for _, group in grouped_candidates_by_original.items():
        should_group = len(group) >= 2
        for q in group:
            q["isGroupable"] = should_group

    validate_independent_upload_image_gate(questions, source_label)


def validate_explanation_patch_questions(questions: list[dict], source_label: str) -> None:
    """Validate the minimum immutable identity needed for explanation-only repairs."""

    if not isinstance(questions, list):
        raise ValueError(f"questions is not a list: {source_label}")
    seen: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError(f"question is not an object: {source_label}")
        question_id = str(question.get("questionId") or "").strip()
        if not question_id:
            raise ValueError(f"questionId is required: {source_label}")
        if question_id in seen:
            raise ValueError(f"duplicate questionId: {question_id}")
        seen.add(question_id)
        if question.get("isDeleted") is not False:
            raise ValueError(f"explanation patch target must have isDeleted=false: {question_id}")
        if question.get("isChoiceOnly") is not False:
            raise ValueError(f"explanation patch target must have isChoiceOnly=false: {question_id}")
        if not str(question.get("questionText") or "").strip():
            raise ValueError(f"questionText is required: {question_id}")
        explanation = question.get("explanationText")
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError(f"explanationText is required: {question_id}")


def validate_question_patch_questions(
    questions: list[dict],
    write_fields: tuple[str, ...],
    source_label: str,
    *,
    allow_legacy_suggestion_rollback: bool = False,
) -> None:
    """Validate narrow question repairs, including answer-correction repairs."""

    if write_fields in PRESERVED_SUGGESTION_MASKS:
        validate_explanation_patch_questions(questions, source_label)
        validate_suggestion_patch_questions(questions, source_label, max_entries=3)
        for question in questions:
            details = question["suggestedQuestionDetails"]
            if (any(not isinstance(item["question"], str) or not isinstance(item["answer"], str)
                    for item in details)
                    or question["suggestedQuestions"] != [item["question"] for item in details]):
                raise ValueError("explanation/detail patch requires an exact string suggestion mirror")
            if write_fields == LAW_FACTS_DETAILS_WRITE_FIELDS:
                facts = question.get("lawRevisionFacts")
                if (not is_law_revision_facts_shape(facts)
                        or not isinstance(facts.get("differenceFacts"), list)
                        or not facts['differenceFacts']
                        or any(not isinstance(item, str) or not item.strip() for item in facts['differenceFacts'])):
                    raise ValueError("legal explanation patch requires nonempty valid difference facts")
        return
    if write_fields == ("suggestedQuestions", "suggestedQuestionDetails"):
        validate_suggestion_patch_questions(
            questions,
            source_label,
            max_entries=7 if allow_legacy_suggestion_rollback else 3,
        )
        return
    if write_fields == ("qualificationId", "listGroupId"):
        validate_routing_patch_questions(questions, source_label)
        return

    validate_explanation_patch_questions(questions, source_label)
    if "correctChoiceText" not in write_fields:
        return
    for question in questions:
        question_id = str(question.get("questionId") or "").strip()
        answer = _normalize_correct_choice_text(
            str(question.get("correctChoiceText") or "")
        )
        if answer not in {"正しい", "間違い"}:
            raise ValueError(
                f"correctChoiceText must be 正しい or 間違い: {question_id}"
            )
        question["correctChoiceText"] = answer
        explanation = str(question.get("explanationText") or "").strip()
        if not explanation.startswith(answer):
            raise ValueError(
                f"correctChoiceText/explanationText prefix mismatch: {question_id}"
            )


def validate_suggestion_patch_questions(
    questions: list[dict], source_label: str, *, max_entries: int = 3, min_entries: int = 1
) -> None:
    """Validate an existing-document-only normalization of saved suggestions."""

    if not isinstance(questions, list):
        raise ValueError(f"questions is not a list: {source_label}")
    seen: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError(f"question is not an object: {source_label}")
        question_id = str(question.get("questionId") or "").strip()
        if not question_id or question_id in seen:
            raise ValueError(f"questionId is required and unique: {source_label}")
        seen.add(question_id)
        if question.get("isDeleted") is not False:
            raise ValueError(f"suggestion patch target must have isDeleted=false: {question_id}")
        if question.get("isChoiceOnly") is not False:
            raise ValueError(f"suggestion patch target must have isChoiceOnly=false: {question_id}")
        if not str(question.get("questionText") or "").strip():
            raise ValueError(f"questionText is required: {question_id}")
        details = question.get("suggestedQuestionDetails")
        prompts = question.get("suggestedQuestions")
        if not isinstance(details, list) or not min_entries <= len(details) <= max_entries:
            raise ValueError(
                "suggestedQuestionDetails must contain "
                f"{min_entries} to {max_entries} entries: {question_id}"
            )
        normalized_prompts: list[str] = []
        for item in details:
            if not isinstance(item, dict) or set(item) != {"question", "answer"}:
                raise ValueError(
                    f"suggestedQuestionDetails entry must contain question and answer only: {question_id}"
                )
            prompt = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if not prompt or not answer:
                raise ValueError(
                    f"suggestedQuestionDetails entry must be non-empty: {question_id}"
                )
            normalized_prompts.append(prompt)
        if prompts != normalized_prompts:
            raise ValueError(
                f"suggestedQuestions must match suggestedQuestionDetails: {question_id}"
            )


def validate_atomic_suggestion_replacement(questions, mask, source_label):
    """Replace the paired fields together, including an audited zero-suggestion result."""
    if mask not in ATOMIC_SUGGESTION_REPLACEMENT_MASKS:
        raise ValueError("explicit atomic suggestion replacement mask required")
    validate_explanation_patch_questions(questions, source_label)
    validate_suggestion_patch_questions(questions, source_label, max_entries=3, min_entries=0)
    for question in questions:
        details = question["suggestedQuestionDetails"]
        if (any(not isinstance(item["question"], str) or not isinstance(item["answer"], str)
                for item in details)
                or question["suggestedQuestions"] != [item["question"] for item in details]):
            raise ValueError("atomic suggestion replacement requires an exact string mirror")
        if "lawRevisionFacts" in mask:
            facts = question.get("lawRevisionFacts")
            if (not is_law_revision_facts_shape(facts)
                    or not isinstance(facts.get("differenceFacts"), list)
                    or not facts["differenceFacts"]
                    or any(not isinstance(item, str) or not item.strip() for item in facts["differenceFacts"])):
                raise ValueError("legal explanation patch requires nonempty valid difference facts")


def validate_routing_patch_questions(questions: list[dict], source_label: str) -> None:
    """Validate an existing-document-only qualification/year routing repair."""

    if not isinstance(questions, list):
        raise ValueError(f"questions is not a list: {source_label}")
    seen: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            raise ValueError(f"question is not an object: {source_label}")
        question_id = str(question.get("questionId") or "").strip()
        if not question_id or question_id in seen:
            raise ValueError(f"questionId is required and unique: {source_label}")
        seen.add(question_id)
        if question.get("isDeleted") is not False or question.get("isChoiceOnly") is not False:
            raise ValueError(f"routing patch target must be active display: {question_id}")
        if not str(question.get("questionText") or "").strip():
            raise ValueError(f"questionText is required: {question_id}")
        qualification_id = str(question.get("qualificationId") or "").strip()
        if qualification_id not in {"gas-shunin-kou", "gas-shunin-otsu"}:
            raise ValueError(f"qualificationId is not gas shunin: {question_id}")
        year = normalize_exam_year(question.get("examYear"))
        if year is None or str(question.get("listGroupId") or "") != str(year):
            raise ValueError(f"listGroupId must match examYear: {question_id}")


def build_doc_data_base(question: dict) -> dict:
    """
    問題データからFirestoreドキュメントデータを構築（updatedAt/updatedByIdは除外）。
    """
    is_choice_only = question.get("isChoiceOnly", False) is True
    doc_data = {
        "questionSetId": question.get("questionSetId", ""),
        "listGroupId": question.get("listGroupId", ""),
        "originalQuestionId": question.get("originalQuestionId", ""),
        "originalQuestionBodyText": question.get("originalQuestionBodyText", ""),
        "questionBodyText": question.get("questionBodyText", ""),
        "originalQuestionChoiceText": question.get("originalQuestionChoiceText", ""),
        "questionText": question.get("questionText", ""),
        "questionType": question.get("questionType", ""),
        "qualificationId": question.get("qualificationId", ""),
        "correctChoiceText": str(question.get("correctChoiceText", "")),
        "examSource": question.get("examSource", ""),
        "questionTags": question.get("questionTags", []),
        "isOfficial": question.get("isOfficial", False),
        "isDeleted": question.get("isDeleted", False),
        "isChoiceOnly": question.get("isChoiceOnly", False),
        "isGroupable": question.get("isGroupable", False),
    }
    if not is_choice_only:
        doc_data["explanationText"] = question.get("explanationText", "")
    if question.get("examYear") not in (None, ""):
        doc_data["examYear"] = question["examYear"]
    # オプションフィールド
    for opt_key in (
        "fillInBlankContent",
        "questionLearningPatternId",
        "knowledgeText",
        "suggestedQuestions",
        "suggestedQuestionDetails",
        "explanationReferences",
        "lawReferences",
        "lawRevisionFacts",
        "isLawRelated",
        "lawGroundedExplanationNotNeeded",
        "questionImageUrls",
        "importKey",
        "originalQuestionChoiceImageUrls",
    ):
        if question.get("qualificationId") == "gx-kentei" and opt_key in GX_PRIVATE_AUTHORING_FIELDS:
            continue
        if opt_key in PRODUCTION_CLIENT_OMITTED_FIELDS:
            continue
        if is_choice_only and opt_key in CHOICE_ONLY_OMITTED_FIELDS:
            continue
        if opt_key in question:
            doc_data[opt_key] = question[opt_key]
    return doc_data


def stale_public_fields_to_delete(
    new_base: dict,
    existing: dict,
) -> tuple[str, ...]:
    """Return fields that the current public document contract must omit."""

    fields = [
        field
        for field in PRODUCTION_CLIENT_OMITTED_FIELDS
        if field in existing
    ]
    if new_base.get("questionType") != "fill_in_blank" and "fillInBlankContent" in existing:
        fields.append("fillInBlankContent")
    is_choice_only = new_base.get("isChoiceOnly") is True
    if not is_choice_only:
        fields.extend(
            field
            for field in ("suggestedQuestions", "suggestedQuestionDetails")
            if field in existing and field not in new_base and field not in fields
        )
    if is_choice_only:
        fields.extend(
            field
            for field in CHOICE_ONLY_OMITTED_FIELDS
            if field in existing and field not in fields
        )
    return tuple(fields)


def build_doc_data(question: dict, now: datetime) -> dict:
    """
    互換API: テスト/呼び出し側が期待する build_doc_data を残す。
    """
    doc_data = build_doc_data_base(question)
    doc_data.setdefault("createdAt", now)
    doc_data.setdefault("createdById", CREATED_BY_ID)
    doc_data["updatedAt"] = now
    doc_data["updatedById"] = UPDATED_BY_ID
    return doc_data


def resolve_write_fields(data: dict) -> tuple[str, ...] | None:
    """Return an explicitly requested narrow update contract, if present."""

    if _requests_atomic_maintenance(data):
        raise ValueError("atomic maintenance must use its explicit schema resolver")
    raw = data.get(WRITE_FIELDS_KEY)
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{WRITE_FIELDS_KEY} must be a non-empty list[str]")
    fields = tuple(raw)
    if len(fields) != len(set(fields)):
        raise ValueError(f"{WRITE_FIELDS_KEY} contains duplicates")
    if fields not in ALLOWED_PATCH_WRITE_FIELD_SETS:
        raise ValueError(
            f"unsupported {WRITE_FIELDS_KEY}: {list(fields)}; "
            f"allowed={[list(item) for item in ALLOWED_PATCH_WRITE_FIELD_SETS]}"
        )
    return fields


class _UploadJSONObject(dict):
    """Retain duplicate JSON keys for the opt-in update-time contract only."""

    def __init__(self, pairs):
        super().__init__()
        self.duplicate_keys = set()
        for key, value in pairs:
            if key in self:
                self.duplicate_keys.add(key)
            self[key] = value


def _requests_atomic_maintenance(data: dict) -> bool:
    schema = data.get("schemaVersion")
    return ("writeFieldsByDocument" in data or "expectedLiveFingerprint" in data
            or (isinstance(schema, str) and schema.startswith("question-maintenance-atomic-upload")))


def _reject_duplicate_json_keys(value) -> None:
    if isinstance(value, dict):
        if getattr(value, "duplicate_keys", ()):
            raise ValueError("atomic maintenance JSON contains duplicate keys")
        for child in value.values():
            _reject_duplicate_json_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_duplicate_json_keys(child)


def resolve_atomic_maintenance_contract(data: dict, source_label: str) -> dict:
    """Validate the transport contract without SDK initialization or live reads.

    Exactly one original question is accepted, with an explicit preserved-list
    or paired-replacement mask per document. All IDs, versions and the ordered live
    fingerprint are mandatory. Full sibling membership, current evaluation and
    checkpoint gates, publication authority and before/after readback belong to
    the calling publication orchestrator; this transport never establishes
    publishReady or publication completion.
    """
    if not isinstance(data, dict):
        raise ValueError("atomic maintenance JSON root must be an object")
    _reject_duplicate_json_keys(data)
    if (set(data) != ATOMIC_MAINTENANCE_KEYS
            or data.get("schemaVersion") != ATOMIC_MAINTENANCE_SCHEMA):
        raise ValueError("atomic maintenance requires its exact schema and six root keys")
    original = data["originalQuestionId"]
    if (not isinstance(original, str) or not original or original.strip() != original
            or "/" in original):
        raise ValueError("atomic maintenance originalQuestionId must be a nonempty ID")
    questions = data["questions"]
    if not isinstance(questions, list) or not 1 <= len(questions) <= BATCH_SIZE:
        raise ValueError("atomic maintenance questions must fit one nonempty batch")
    masks = data["writeFieldsByDocument"]
    if not isinstance(masks, dict) or not masks:
        raise ValueError("writeFieldsByDocument must be a nonempty exact-ID map")
    # Reuse the strict ID/time parser; this sentinel mask is never a write mask.
    expected = resolve_expected_update_times(
        data, questions, DETAILS_ONLY_WRITE_FIELDS,
    )
    ids = [question["questionId"] for question in questions]
    if set(masks) != set(ids):
        raise ValueError("writeFieldsByDocument keys must exactly cover questionIds")
    qualification_ids = set()
    resolved_masks = {}
    for question in questions:
        identity = question["questionId"]
        if (question.get("originalQuestionId") != original
                or any(key in question for key in (WRITE_FIELDS_KEY, "writeFieldsByDocument",
                                                    EXPECTED_UPDATE_TIMES_KEY, "expectedLiveFingerprint"))):
            raise ValueError("atomic maintenance question identity/control fields differ")
        qualification = question.get("qualificationId")
        if (not isinstance(qualification, str) or not qualification
                or qualification.strip() != qualification or "/" in qualification):
            raise ValueError("atomic maintenance requires an explicit qualificationId")
        qualification_ids.add(qualification)
        raw = masks[identity]
        if (not isinstance(raw, list)
                or tuple(raw) not in ATOMIC_MAINTENANCE_WRITE_FIELD_SETS):
            raise ValueError("atomic maintenance has an unsupported document write mask")
        mask = tuple(raw)
        if mask in ATOMIC_SUGGESTION_REPLACEMENT_MASKS:
            validate_atomic_suggestion_replacement([question], mask, source_label)
        else:
            validate_question_patch_questions([question], mask, source_label)
        # No missing field may be silently constructed by the DTO conversion.
        if any(field not in question for field in mask):
            raise ValueError("atomic maintenance payload is missing a masked field")
        build_patch_doc_data(build_doc_data_base(question), mask, datetime.now())
        resolved_masks[identity] = mask
    if len(qualification_ids) != 1:
        raise ValueError("atomic maintenance must target one qualification")
    fingerprint = data["expectedLiveFingerprint"]
    if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise ValueError("expectedLiveFingerprint must be a lowercase SHA256")
    return {"documentIds": ids, "masks": resolved_masks, "expectedUpdateTimes": expected}


def resolve_expected_update_times(data: dict, questions, write_fields) -> dict[str, str] | None:
    if EXPECTED_UPDATE_TIMES_KEY not in data:
        return None
    expected = data[EXPECTED_UPDATE_TIMES_KEY]
    if (not write_fields or not isinstance(expected, dict) or not expected
            or EXPECTED_UPDATE_TIMES_KEY in getattr(data, "duplicate_keys", ())
            or getattr(expected, "duplicate_keys", ())):
        raise ValueError("expectedUpdateTimes requires a unique nonempty map and narrow writeFields")
    if not isinstance(questions, list) or not questions:
        raise ValueError("expectedUpdateTimes requires nonempty upload questions")
    ids = [q.get("questionId") if isinstance(q, dict) else None for q in questions]
    if (any(not isinstance(i, str) or not i or i.strip() != i or "/" in i for i in ids)
            or len(set(ids)) != len(ids) or set(expected) != set(ids)):
        raise ValueError("expectedUpdateTimes keys must exactly cover unique upload questionIds")
    pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
    for value in expected.values():
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise ValueError("expectedUpdateTimes must contain exact RFC3339 timestamps")
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("expectedUpdateTimes contains an invalid timestamp") from exc
    return dict(expected)


def checked_existing_snapshots(document_ids: list[str], snapshots: list) -> dict:
    by_id = {}
    for snapshot in snapshots:
        identity = getattr(snapshot, "id", None)
        if (identity not in document_ids or identity in by_id
                or getattr(snapshot, "exists", False) is not True):
            raise RuntimeError("existing snapshot scope is missing, duplicate or unexpected")
        by_id[identity] = snapshot
    if set(by_id) != set(document_ids):
        raise RuntimeError("existing snapshot scope is incomplete")
    return by_id


def require_expected_update_times(document_ids: list[str], snapshots: list, expected: dict[str, str]) -> dict:
    by_id = checked_existing_snapshots(document_ids, snapshots)
    for identity in document_ids:
        timestamp = getattr(by_id[identity], "update_time", None)
        render = getattr(timestamp, "rfc3339", None)
        if not callable(render) or render() != expected[identity]:
            raise RuntimeError("expectedUpdateTimes conflict: document update_time changed")
    return by_id


def require_explanation_details_live_mirror(questions: list[dict], snapshots: list) -> dict:
    by_id = checked_existing_snapshots([q["questionId"] for q in questions], snapshots)
    for question in questions:
        live = by_id[question["questionId"]].to_dict() or {}
        if (live.get("isDeleted") is not False or live.get("isChoiceOnly") is not False
                or live.get("suggestedQuestions") != question["suggestedQuestions"]):
            raise RuntimeError("explanation/detail patch live flags or preserved suggestion mirror changed")
    return by_id


def require_preserved_legal_metadata(questions: list[dict], snapshots: list) -> None:
    """A narrow explanatory repair cannot replace legal verdicts or provenance."""
    by_id = checked_existing_snapshots([q['questionId'] for q in questions], snapshots)
    for question in questions:
        old = (by_id[question['questionId']].to_dict() or {}).get('lawRevisionFacts')
        new = question['lawRevisionFacts']
        if (not is_law_revision_facts_shape(old) or not isinstance(old.get('differenceFacts'), list)
                or {k:v for k,v in old.items() if k != 'differenceFacts'}
                != {k:v for k,v in new.items() if k != 'differenceFacts'}):
            raise RuntimeError('legal explanatory patch would change preserved law metadata')


def build_patch_doc_data(new_base: dict, write_fields: tuple[str, ...], now: datetime) -> dict:
    """Build a partial update that cannot alter fields outside the explicit contract."""

    missing = [field for field in write_fields if field not in new_base]
    if missing:
        raise ValueError(f"patch write field is missing from payload: {missing}")
    doc_data = {field: new_base[field] for field in write_fields}
    doc_data["updatedAt"] = now
    doc_data["updatedById"] = UPDATED_BY_ID
    return doc_data


def top_level_merge_fields(doc_data: dict) -> list[str]:
    """指定フィールドの map 値を丸ごと置換し、未指定の既存フィールドは保持する。"""
    return list(doc_data.keys())


def fetch_existing_question_snapshots(db, doc_refs: list):
    get_all = getattr(db, "get_all", None)
    if callable(get_all):
        return list(get_all(doc_refs, field_paths=EXISTING_DOC_FIELD_PATHS))
    return [ref.get(field_paths=EXISTING_DOC_FIELD_PATHS) for ref in doc_refs]


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_json"):
        return _json_safe(value.to_json())
    return value


def firestore_live_fingerprint(document_ids: list[str], live_documents: dict) -> str:
    records = []
    for question_id in document_ids:
        document = live_documents.get(question_id)
        filtered = None
        if isinstance(document, dict):
            filtered = {
                field: _json_safe(document[field])
                for field in DOC_COMPARE_KEYS
                if field in document
            }
        records.append(
            {
                "questionId": question_id,
                "exists": document is not None,
                "document": filtered,
            }
        )
    value = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_expected_live_fingerprint(
    document_ids: list[str], snapshots: list, expected: str
) -> None:
    snapshots_by_id = {
        str(getattr(snapshot, "id", "")): snapshot
        for snapshot in snapshots
        if getattr(snapshot, "id", None)
    }
    if set(snapshots_by_id) != set(document_ids):
        raise RuntimeError("Firestore documentの確認結果が対象範囲と一致しません。")
    live_documents = {
        question_id: (snapshots_by_id[question_id].to_dict() or {})
        for question_id in document_ids
        if getattr(snapshots_by_id[question_id], "exists", False)
    }
    current = firestore_live_fingerprint(document_ids, live_documents)
    if not expected or not hmac.compare_digest(current, expected):
        raise RuntimeError("確認後にFirestore documentが更新されたため反映を停止しました。")


def add_guarded_question_write(batch, doc_ref, doc_data: dict, snapshot) -> None:
    """read後の同時更新を上書きしないFirestore writeをbatchへ追加する。"""
    if getattr(snapshot, "exists", False):
        update_time = getattr(snapshot, "update_time", None)
        if update_time is None:
            raise RuntimeError("既存documentのupdate_timeを取得できません。")
        batch.update(
            doc_ref,
            doc_data,
            option=firestore.LastUpdateOption(update_time),
        )
        return
    batch.create(doc_ref, doc_data)


class AtomicMaintenanceCommitUnconfirmed(RuntimeError):
    """A commit was attempted, but its result cannot establish every write version.

    Callers must persist their operation intent before invoking the transport.
    They must reconcile this exception through readback/operation evidence, not
    automatically resend with freshly substituted expected versions.
    """

    def __init__(self, prepared_result: dict):
        super().__init__("atomic maintenance commit outcome is unconfirmed; reconcile before retry")
        self.prepared_result = prepared_result


def upload_atomic_maintenance_questions(
    data: dict, source_label: str, *, dry_run: bool = False,
    credentials_json: Path | None = None,
) -> dict:
    """One existing-original maintenance transport, never publication approval.

    Content writes are exactly each document's mask plus updatedAt/updatedById.
    There are no creates, deletes, manifest writes or full-document sets. All
    local/live validation finishes before a batch is constructed. A partially
    unchanged scope is refused: skipping such documents would leave their CAS
    outside the atomic batch. All-unchanged is a no-write observation, not proof
    of publication. The orchestrator owns sibling-membership before/after checks
    (including concurrent new siblings), all current gates and durable recovery.
    """
    # Own the entire request before validating it or yielding to SDK calls.
    # deepcopy also retains _UploadJSONObject.duplicate_keys at every depth;
    # serializing through plain dict/JSON here would erase that rejection evidence.
    data = deepcopy(data)
    contract = resolve_atomic_maintenance_contract(data, source_label)
    ids = contract["documentIds"]
    masks = contract["masks"]
    result = {
        "schemaVersion": ATOMIC_MAINTENANCE_RESULT_SCHEMA,
        "status": "dry_run" if dry_run else "prepared",
        "originalQuestionId": data["originalQuestionId"],
        "documentIds": list(ids),
        "writeFieldsByDocument": {identity: list(masks[identity]) for identity in ids},
        "auditWriteFields": ["updatedAt", "updatedById"],
        "expectedUpdateTimes": dict(contract["expectedUpdateTimes"]),
        "expectedLiveFingerprint": data["expectedLiveFingerprint"],
        "changedDocumentIds": [], "unchangedDocumentIds": [], "writeResults": [],
        "publicationConfirmed": False,
    }
    if dry_run:
        return result

    db = init_firestore(credentials_json)
    refs = {identity: db.collection("questions").document(identity) for identity in ids}
    snapshots = fetch_existing_question_snapshots(db, list(refs.values()))
    by_id = require_expected_update_times(ids, snapshots, contract["expectedUpdateTimes"])
    require_expected_live_fingerprint(ids, snapshots, data["expectedLiveFingerprint"])
    now = datetime.now()
    updates = {}
    for question in data["questions"]:
        identity = question["questionId"]
        snapshot = by_id[identity]
        existing = snapshot.to_dict() or {}
        if (existing.get("originalQuestionId") != data["originalQuestionId"]
                or existing.get("qualificationId") != question["qualificationId"]):
            raise RuntimeError("atomic maintenance live original/qualification identity differs")
        mask = masks[identity]
        if mask in ATOMIC_SUGGESTION_REPLACEMENT_MASKS:
            if existing.get("isDeleted") is not False or existing.get("isChoiceOnly") is not False:
                raise RuntimeError("atomic suggestion replacement live flags changed")
        else:
            require_explanation_details_live_mirror([question], [snapshot])
        if "lawRevisionFacts" in mask:
            require_preserved_legal_metadata([question], [snapshot])
        base = build_doc_data_base(question)
        # Include presence in comparisons: absent is not equivalent to null.
        changed = any(field not in existing or existing[field] != base[field] for field in mask)
        if changed:
            result["changedDocumentIds"].append(identity)
            updates[identity] = build_patch_doc_data(base, mask, now)
        else:
            result["unchangedDocumentIds"].append(identity)
    if not updates:
        result["status"] = "no_changes"
        return result
    if result["unchangedDocumentIds"]:
        raise RuntimeError("atomic maintenance partially unchanged scope requires reconciliation")

    # Every approved document is changed and participates in this single CAS.
    batch = db.batch()
    for identity in ids:
        add_guarded_question_write(batch, refs[identity], updates[identity], by_id[identity])
    try:
        responses = batch.commit()
        if not isinstance(responses, (list, tuple)) or len(responses) != len(ids):
            raise ValueError("atomic maintenance commit response coverage differs")
        versions = {}
        for identity, response in zip(ids, responses):
            stamp = getattr(response, "update_time", None)
            render = getattr(stamp, "rfc3339", None)
            if not callable(render):
                raise ValueError("atomic maintenance commit response has no exact updateTime")
            versions[identity] = render()
        # Validate exact nanosecond timestamps without normalizing their text.
        resolve_expected_update_times(
            {EXPECTED_UPDATE_TIMES_KEY: versions}, data["questions"], DETAILS_ONLY_WRITE_FIELDS,
        )
    except Exception as exc:
        raise AtomicMaintenanceCommitUnconfirmed(result) from exc
    result["writeResults"] = [
        {"questionId": identity, "updateTime": versions[identity]} for identity in ids
    ]
    result["status"] = "committed"
    return result


def normalize_exam_year(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        year = value
    elif isinstance(value, float):
        year = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            year = int(text)
        except ValueError:
            return None
    else:
        return None
    if 1900 <= year <= 2100:
        return year
    return None


def collect_exam_years_by_qualification(questions: list[dict]) -> dict[str, list[int]]:
    years_by_qualification: dict[str, set[int]] = {}
    for question in questions:
        if not isinstance(question, dict):
            continue
        qualification_id = str(question.get("qualificationId") or "").strip()
        if not qualification_id:
            continue
        year = normalize_exam_year(question.get("examYear"))
        if year is None:
            continue
        years_by_qualification.setdefault(qualification_id, set()).add(year)
    return {
        qualification_id: sorted(years, reverse=True)
        for qualification_id, years in sorted(years_by_qualification.items())
    }


def merge_official_exam_years_map(
    current: object,
    additions: dict[str, list[int]],
) -> dict[str, list[int]]:
    merged: dict[str, list[int]] = {}
    if isinstance(current, dict):
        for qualification_id, raw_years in current.items():
            if not isinstance(qualification_id, str) or not qualification_id.strip():
                continue
            years: set[int] = set()
            if isinstance(raw_years, list):
                for raw_year in raw_years:
                    year = normalize_exam_year(raw_year)
                    if year is not None:
                        years.add(year)
            if years:
                merged[qualification_id.strip()] = sorted(years, reverse=True)

    for qualification_id, years in additions.items():
        existing = set(merged.get(qualification_id, []))
        existing.update(years)
        if existing:
            merged[qualification_id] = sorted(existing, reverse=True)
    return dict(sorted(merged.items()))


def upsert_official_exam_years_manifest(db, additions: dict[str, list[int]]) -> None:
    if not additions:
        print("[SKIP] official exam years manifest: examYear が見つかりません")
        return
    doc_ref = db.collection("config").document(CONFIG_DOC_ID)
    existing_data: dict = {}
    try:
        snapshot = doc_ref.get()
        if getattr(snapshot, "exists", False):
            existing_data = snapshot.to_dict() or {}
    except Exception as exc:
        raise RuntimeError(f"config/{CONFIG_DOC_ID} の取得に失敗しました: {exc}") from exc

    current = existing_data.get(OFFICIAL_EXAM_YEARS_FIELD)
    merged = merge_official_exam_years_map(current, additions)
    if current == merged:
        print("[SKIP] official exam years manifest: 差分なし")
        return

    doc_ref.set({OFFICIAL_EXAM_YEARS_FIELD: merged}, merge=True)
    print(
        "[CONFIG] official exam years manifest updated: "
        + ", ".join(
            f"{qualification_id}={years}"
            for qualification_id, years in additions.items()
        )
    )


def upload_questions(
    json_file_path: str,
    dry_run: bool = False,
    credentials_json: Path | None = None,
):
    """JSONファイルからFirestoreに質問データをバッチアップロード"""

    # JSONファイルを読み込み
    with open(json_file_path, "r", encoding="utf-8") as f:
        data = json.load(f, object_pairs_hook=_UploadJSONObject)

    if not isinstance(data, dict):
        raise ValueError("upload JSON root must be an object")
    # Resolve the explicit atomic transport before legacy mask/full-upload paths.
    if _requests_atomic_maintenance(data):
        return upload_atomic_maintenance_questions(
            data, str(json_file_path), dry_run=dry_run, credentials_json=credentials_json,
        )
    write_fields = resolve_write_fields(data)
    questions = data.get("questions", [])
    expected_update_times = resolve_expected_update_times(data, questions, write_fields)
    if write_fields in (DETAILS_ONLY_WRITE_FIELDS, LAW_FACTS_DETAILS_WRITE_FIELDS) and expected_update_times is None:
        raise ValueError('maintenance patches require exact expectedUpdateTimes')
    if not questions:
        print("アップロードする質問データがありません。")
        return

    qualification_id = infer_qualification_id_from_json_path(json_file_path)
    for q in questions:
        if isinstance(q, dict):
            if not isinstance(q.get("qualificationId"), str) or not str(q.get("qualificationId") or "").strip():
                q["qualificationId"] = qualification_id
            if q.get("questionTags") is None:
                q["questionTags"] = []

    if write_fields:
        validate_question_patch_questions(
            questions,
            write_fields,
            str(json_file_path),
            allow_legacy_suggestion_rollback=(
                data.get("allowLegacySuggestionRollback") is True
            ),
        )
    else:
        validate_required_question_fields(questions, str(json_file_path))
        validate_gx_authoring_privacy(questions)
    exam_years_by_qualification = (
        {} if write_fields else collect_exam_years_by_qualification(questions)
    )

    print(f"合計 {len(questions)} 件の質問をアップロードします...")

    if dry_run:
        print("[DRY RUN] 実際のアップロードは行いません。")
        if write_fields:
            print(f"[DRY RUN] 更新フィールド限定: {', '.join(write_fields)}")
        if exam_years_by_qualification:
            print(
                "[DRY RUN] official exam years manifest: "
                + ", ".join(
                    f"{qualification_id}={years}"
                    for qualification_id, years in exam_years_by_qualification.items()
                )
            )
        now = datetime.now()
        for q in questions:
            if not isinstance(q, dict):
                continue
            qid = str(q.get("questionId") or "").strip() or "unknown"
            base = build_doc_data_base(q)
            if write_fields:
                build_patch_doc_data(base, write_fields, now)
                continue
            doc_data = dict(base)
            doc_data["createdAt"] = now
            doc_data["createdById"] = CREATED_BY_ID
            doc_data["updatedAt"] = now
            doc_data["updatedById"] = UPDATED_BY_ID
            validate_question_doc(doc_data, doc_id=qid)
        for q in questions[:5]:
            print(f"  - {q.get('questionId')}: {q.get('questionText', '')[:50]}...")
        return

    # Firestore初期化
    db = init_firestore(credentials_json)
    now = datetime.now()

    expected_live_hash = os.environ.get("QUESTION_PUBLISH_EXPECTED_LIVE_HASH", "").strip()
    guarded_snapshots_by_id = None
    if expected_live_hash or expected_update_times is not None or write_fields in PRESERVED_SUGGESTION_MASKS:
        guarded_ids = [str(question.get("questionId") or "").strip() for question in questions]
        if not all(guarded_ids) or len(set(guarded_ids)) != len(guarded_ids):
            raise RuntimeError("公開対象のquestionIdが空又は重複しています。")
        guarded_refs = [
            db.collection("questions").document(question_id)
            for question_id in guarded_ids
        ]
        guarded_snapshots = fetch_existing_question_snapshots(db, guarded_refs)
        if expected_update_times is not None:
            require_expected_update_times(guarded_ids, guarded_snapshots, expected_update_times)
        if write_fields in PRESERVED_SUGGESTION_MASKS:
            require_explanation_details_live_mirror(questions, guarded_snapshots)
        if write_fields == LAW_FACTS_DETAILS_WRITE_FIELDS:
            require_preserved_legal_metadata(questions, guarded_snapshots)
        if expected_live_hash:
            require_expected_live_fingerprint(guarded_ids, guarded_snapshots, expected_live_hash)
        guarded_snapshots_by_id = {
            str(snapshot.id): snapshot for snapshot in guarded_snapshots
        }

    success_count = 0
    error_count = 0
    batch_num = 0
    skipped_count = 0

    # BATCH_SIZE 件ずつバッチ書き込み
    for chunk_start in range(0, len(questions), BATCH_SIZE):
        chunk = questions[chunk_start:chunk_start + BATCH_SIZE]
        batch = db.batch()
        chunk_valid = 0

        doc_refs = []
        doc_ref_by_id: dict[str, Any] = {}
        base_by_id: dict[str, dict] = {}

        for question in chunk:
            question_id = question.get("questionId")
            if not question_id:
                print(f"Error: questionId が見つかりません: {question}")
                error_count += 1
                continue

            doc_ref = db.collection("questions").document(question_id)
            doc_refs.append(doc_ref)
            doc_ref_by_id[question_id] = doc_ref
            base_by_id[question_id] = build_doc_data_base(question)

        # 既存ドキュメントをまとめて取得し、差分があるものだけ書き込む（updatedAtは差分がある時のみ更新）
        try:
            snapshots = (
                [guarded_snapshots_by_id[question_id] for question_id in base_by_id]
                if guarded_snapshots_by_id is not None
                else fetch_existing_question_snapshots(db, doc_refs)
            )
        except Exception as exc:
            # 「差分がある時のみ updatedAt 更新」を守るため、既存取得に失敗したら中断する
            raise RuntimeError(f"既存ドキュメントの取得に失敗しました: {exc}") from exc

        if doc_refs and not snapshots:
            raise RuntimeError("既存ドキュメントの取得結果が空です（想定外）")

        for snap in snapshots:
            qid = getattr(snap, "id", None)
            if not qid or qid not in base_by_id:
                continue
            new_base = base_by_id[qid]
            doc_ref = doc_ref_by_id[qid]

            exists = getattr(snap, "exists", False)
            if write_fields and not exists:
                raise RuntimeError(f"限定フィールド更新では新規documentを作成できません: {qid}")
            if exists:
                existing = snap.to_dict() or {}
                fields_to_delete = (
                    ()
                    if write_fields
                    else stale_public_fields_to_delete(new_base, existing)
                )
                compare_fields = write_fields or tuple(
                    key for key in DOC_COMPARE_KEYS if key in new_base
                )
                changed = bool(fields_to_delete) or any(
                    existing.get(key) != new_base.get(key)
                    for key in compare_fields
                )
                if not changed:
                    skipped_count += 1
                    continue
                created_at = existing.get("createdAt") or now
                created_by_id = existing.get("createdById") or CREATED_BY_ID
            else:
                created_at = now
                created_by_id = CREATED_BY_ID

            if write_fields:
                doc_data = build_patch_doc_data(new_base, write_fields, now)
            else:
                doc_data = dict(new_base)
                doc_data["createdAt"] = created_at
                doc_data["createdById"] = created_by_id
                doc_data["updatedAt"] = now
                doc_data["updatedById"] = UPDATED_BY_ID
                validate_question_doc(doc_data, doc_id=str(qid))
            if exists:
                for field in fields_to_delete:
                    doc_data[field] = firestore.DELETE_FIELD
            add_guarded_question_write(batch, doc_ref, doc_data, snap)
            chunk_valid += 1

        try:
            if chunk_valid == 0:
                end_idx = min(chunk_start + BATCH_SIZE, len(questions))
                print(f"バッチ skip（差分なし）: {end_idx}/{len(questions)} 件")
            else:
                batch.commit()
                batch_num += 1
                success_count += chunk_valid
                end_idx = min(chunk_start + BATCH_SIZE, len(questions))
                print(f"バッチ {batch_num} 完了: {end_idx}/{len(questions)} 件 (updated={chunk_valid}, skipped_total={skipped_count})")
        except Exception as e:
            print(f"Error: バッチ {batch_num + 1} のコミット失敗: {e}")
            error_count += chunk_valid

    if error_count == 0:
        upsert_official_exam_years_manifest(db, exam_years_by_qualification)
    else:
        print("[SKIP] official exam years manifest: question upload error があるため更新しません")

    print(f"\n完了: 更新 {success_count} 件, スキップ {skipped_count} 件, エラー {error_count} 件")


def resolve_json_file_path(path_or_dir: str) -> Path:
    path = Path(path_or_dir)
    if path.is_file():
        return path
    if path.is_dir():
        candidates = sorted(path.glob("*_firestore*.json"))
        if not candidates:
            raise FileNotFoundError(f"Firestore JSONが見つかりません: {path}")
        return candidates[-1]
    raise FileNotFoundError(f"指定パスが見つかりません: {path}")


def main():
    parser = argparse.ArgumentParser(description="FirestoreにJSONデータをアップロード")
    parser.add_argument(
        "json_file",
        nargs="?",
        default="output/2nd-class-kenchikushi/questions_json/upload_to_firestore",
        help="アップロードするJSONファイルのパス（またはディレクトリ。ディレクトリ指定時は最新1件を使用）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="実際のアップロードを行わずに確認のみ"
    )
    parser.add_argument(
        "--credentials-json",
        type=Path,
        default=None,
        help="Firebase service account JSON のパス。未指定時は GOOGLE_APPLICATION_CREDENTIALS を使う。",
    )
    
    args = parser.parse_args()
    resolved_json_file = resolve_json_file_path(args.json_file)
    print(f"使用ファイル: {resolved_json_file}")
    upload_questions(str(resolved_json_file), args.dry_run, args.credentials_json)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # pragma: no cover
        raise SystemExit(0)
