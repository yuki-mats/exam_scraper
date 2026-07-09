#!/usr/bin/env python3
"""
Validate explanationText patch coverage and format against source questions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.common.question_identity import review_question_id
from scripts.common.repaso_firestore_schema import _is_law_revision_facts


REQUIRED_FIELDS = [
    "explanationText",
    "suggestedQuestions",
    "suggestedQuestionDetails",
    "original_question_id",
    "question_url",
]

ALLOWED_LAW_REFERENCE_ROLES = {"current_basis", "exam_time_basis"}
ALLOWED_LAW_REFERENCE_SCOPES = {"question", "choice"}
ALLOWED_LAW_REFERENCE_VERIFICATION_STATUS = {"verified", "candidate", "unverified"}
ALLOWED_LAW_REFERENCE_COMPARISON_STATUS = {
    "same_as_current",
    "differs_from_current",
    "not_checked",
}
LAW_REFERENCE_PLACEHOLDERS = {"", "不明", "未確認", "TODO", "TBD", "N/A", "null", "None"}
LAW_CONTEXT_KEYWORDS = (
    "現行法",
    "出題当時",
    "当時",
    "現在",
    "法",
    "条",
    "項",
    "号",
    "政令",
    "省令",
    "規則",
    "告示",
    "基準",
    "制度",
    "定義",
    "要件",
    "義務",
    "手続",
    "届出",
    "許可",
    "確認",
    "改正",
)
CURRENT_LAW_TERMS = ("現行法", "現在", "現行")
EXAM_TIME_TERMS = ("出題当時", "当時", "試験当時", "元の正答", "掲載元")


def has_non_empty_law_references(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return any(has_non_empty_law_references(entry) for entry in value)
    return False


def normalize_for_anchor_match(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def iter_nested_strings(value: Any) -> List[str]:
    strings: List[str] = []
    if isinstance(value, str):
        if value.strip():
            strings.append(value.strip())
    elif isinstance(value, dict):
        for nested in value.values():
            strings.extend(iter_nested_strings(nested))
    elif isinstance(value, list):
        for nested in value:
            strings.extend(iter_nested_strings(nested))
    return strings


def iter_law_reference_objects(value: Any) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        refs.append(value)
    elif isinstance(value, list):
        for nested in value:
            refs.extend(iter_law_reference_objects(nested))
    return refs


def iter_law_revision_fact_objects(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return []


def law_revision_fact_statuses(value: Any) -> set[str]:
    statuses: set[str] = set()
    for fact in iter_law_revision_fact_objects(value):
        status = fact.get("auditStatus")
        if isinstance(status, str) and status:
            statuses.add(status)
    return statuses


def law_related_for_utilization(patch: Dict[str, Any], has_law_references: bool) -> bool:
    if patch.get("isLawRelated") is True:
        return True
    if has_law_references:
        return True
    statuses = law_revision_fact_statuses(patch.get("lawRevisionFacts"))
    return bool(statuses and statuses != {"not_law_related"})


def law_evidence_anchors(patch: Dict[str, Any]) -> List[str]:
    anchors: list[str] = []

    for ref in iter_law_reference_objects(patch.get("lawReferences")):
        for key in ("lawTitle", "lawAlias"):
            value = ref.get(key)
            if isinstance(value, str) and len(normalize_for_anchor_match(value)) >= 3:
                anchors.append(value)
        article = ref.get("article")
        if isinstance(article, str) and article.strip():
            article_text = article.strip()
            if "条" in article_text:
                anchors.append(article_text)
            elif article_text.isdigit():
                anchors.append(f"第{article_text}条")
                anchors.append(f"{article_text}条")

    for fact in iter_law_revision_fact_objects(patch.get("lawRevisionFacts")):
        for key in ("current", "examTime", "evidenceSummary"):
            source = fact.get(key)
            for text in iter_nested_strings(source):
                if "法" in text and len(normalize_for_anchor_match(text)) <= 40:
                    anchors.append(text)
            if isinstance(source, dict):
                law_title = source.get("lawTitle")
                if isinstance(law_title, str) and len(normalize_for_anchor_match(law_title)) >= 3:
                    anchors.append(law_title)
                article = source.get("article")
                if isinstance(article, str) and article.strip():
                    article_text = article.strip()
                    if "条" in article_text:
                        anchors.append(article_text)
                    elif article_text.isdigit():
                        anchors.append(f"第{article_text}条")
                        anchors.append(f"{article_text}条")

    normalized_seen: set[str] = set()
    unique: list[str] = []
    for anchor in anchors:
        normalized = normalize_for_anchor_match(anchor)
        if len(normalized) < 2 or normalized in normalized_seen:
            continue
        normalized_seen.add(normalized)
        unique.append(anchor)
    return unique


def public_text_for_patch(patch: Dict[str, Any]) -> str:
    parts: list[str] = []
    parts.extend(iter_nested_strings(patch.get("explanationText")))
    parts.extend(iter_nested_strings(patch.get("suggestedQuestions")))
    parts.extend(iter_nested_strings(patch.get("suggestedQuestionDetails")))
    return "\n".join(parts)


def contains_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    return any(term in text for term in terms)


def validate_law_evidence_utilization(
    *,
    patch: Dict[str, Any],
    index: int,
    has_law_references: bool,
    errors: List[str],
) -> None:
    if not law_related_for_utilization(patch, has_law_references):
        return

    statuses = law_revision_fact_statuses(patch.get("lawRevisionFacts"))
    if statuses == {"hold"}:
        return

    questions = patch.get("suggestedQuestions")
    details = patch.get("suggestedQuestionDetails")
    question_text = "\n".join(q for q in questions if isinstance(q, str)) if isinstance(questions, list) else ""
    answer_text = "\n".join(
        detail.get("answer", "")
        for detail in details
        if isinstance(detail, dict) and isinstance(detail.get("answer"), str)
    ) if isinstance(details, list) else ""
    public_text = public_text_for_patch(patch)

    if not contains_any(question_text, LAW_CONTEXT_KEYWORDS):
        errors.append(
            f"index {index}: law-related suggestedQuestions must include at least one law/context-specific question"
        )

    if not contains_any(answer_text, LAW_CONTEXT_KEYWORDS):
        errors.append(
            f"index {index}: law-related suggestedQuestionDetails answers must use law/context evidence"
        )

    anchors = law_evidence_anchors(patch)
    normalized_public_text = normalize_for_anchor_match(public_text)
    if anchors and not any(normalize_for_anchor_match(anchor) in normalized_public_text for anchor in anchors):
        errors.append(
            f"index {index}: public explanation fields do not mention any concrete law evidence anchor"
        )

    if "updated_to_current_law" in statuses:
        if not contains_any(public_text, CURRENT_LAW_TERMS) or not contains_any(public_text, EXAM_TIME_TERMS):
            errors.append(
                f"index {index}: updated_to_current_law explanation must distinguish current law from exam-time handling"
            )
        if not (
            contains_any(question_text, CURRENT_LAW_TERMS)
            and contains_any(question_text, EXAM_TIME_TERMS)
        ):
            errors.append(
                f"index {index}: updated_to_current_law suggestedQuestions must ask about current law and exam-time difference"
            )


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_source_questions(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("source JSON must be an object")
    questions = data.get("question_bodies")
    if not isinstance(questions, list):
        raise ValueError("source JSON missing question_bodies")
    return [q for q in questions if isinstance(q, dict)]


def get_patch_entries(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, list):
        raise ValueError("patch JSON must be an array")
    return [q for q in data if isinstance(q, dict)]


def get_question_identity(question: Dict[str, Any]) -> Any:
    return review_question_id(question)


def validate_suggested_question_details(
    *,
    suggested_questions: Any,
    suggested_question_details: Any,
    index: int,
    errors: List[str],
) -> None:
    if not isinstance(suggested_question_details, list):
        errors.append(f"index {index}: suggestedQuestionDetails must be list[object]")
        return
    if not isinstance(suggested_questions, list):
        errors.append(
            f"index {index}: suggestedQuestionDetails requires suggestedQuestions to be list[str]"
        )
        return
    if len(suggested_question_details) != len(suggested_questions):
        errors.append(
            "index {}: suggestedQuestionDetails length mismatch (questions={} details={})".format(
                index,
                len(suggested_questions),
                len(suggested_question_details),
            )
        )
        return

    for detail_index, detail in enumerate(suggested_question_details):
        if not isinstance(detail, dict):
            errors.append(
                f"index {index}: suggestedQuestionDetails[{detail_index}] must be object"
            )
            continue
        question = detail.get("question")
        answer = detail.get("answer")
        if not isinstance(question, str) or not question.strip():
            errors.append(
                f"index {index}: suggestedQuestionDetails[{detail_index}].question must be non-empty string"
            )
        elif question != suggested_questions[detail_index]:
            errors.append(
                f"index {index}: suggestedQuestionDetails[{detail_index}].question must match suggestedQuestions[{detail_index}]"
            )
        if not isinstance(answer, str) or not answer.strip():
            errors.append(
                f"index {index}: suggestedQuestionDetails[{detail_index}].answer must be non-empty string"
            )


def validate_law_references_shape(
    *,
    law_references: Any,
    choice_count: int,
    index: int,
    errors: List[str],
) -> None:
    if not isinstance(law_references, list):
        errors.append(f"index {index}: lawReferences must be a list when present")
        return
    if len(law_references) != choice_count:
        errors.append(
            "index {}: lawReferences length mismatch (source={} patch={})".format(
                index,
                choice_count,
                len(law_references),
            )
        )
        return

    for choice_index, choice_refs in enumerate(law_references):
        if not isinstance(choice_refs, list):
            errors.append(
                f"index {index}: lawReferences[{choice_index}] must be list[object]"
            )
            continue
        for ref_index, reference in enumerate(choice_refs):
            if not isinstance(reference, dict):
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}] must be object"
                )
                continue

            role = reference.get("role")
            if role not in ALLOWED_LAW_REFERENCE_ROLES:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].role is invalid"
                )

            scope = reference.get("scope")
            if scope not in ALLOWED_LAW_REFERENCE_SCOPES:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].scope is invalid"
                )
            elif scope == "choice" and reference.get("choiceIndex") != choice_index:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].choiceIndex must equal outer index"
                )

            verification_status = reference.get("verificationStatus")
            if verification_status not in ALLOWED_LAW_REFERENCE_VERIFICATION_STATUS:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].verificationStatus is invalid"
                )

            comparison_status = reference.get("comparisonStatus")
            if comparison_status is not None and comparison_status not in ALLOWED_LAW_REFERENCE_COMPARISON_STATUS:
                errors.append(
                    f"index {index}: lawReferences[{choice_index}][{ref_index}].comparisonStatus is invalid"
                )

            for required_key in ("lawTitle", "referenceDate"):
                value = reference.get(required_key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(
                        f"index {index}: lawReferences[{choice_index}][{ref_index}].{required_key} must be non-empty string"
                    )

            for optional_key in (
                "lawId",
                "lawRevisionId",
                "article",
                "paragraph",
                "item",
                "lawAlias",
                "reason",
            ):
                value = reference.get(optional_key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    errors.append(
                        f"index {index}: lawReferences[{choice_index}][{ref_index}].{optional_key} must be non-empty string when present"
                    )

            if verification_status == "verified":
                for verified_key in ("lawId", "article"):
                    value = reference.get(verified_key)
                    if not isinstance(value, str) or value.strip() in LAW_REFERENCE_PLACEHOLDERS:
                        errors.append(
                            f"index {index}: lawReferences[{choice_index}][{ref_index}].{verified_key} is required for verified lawReferences"
            )


def validate_law_revision_facts_shape(
    *,
    law_revision_facts: Any,
    choice_count: int,
    index: int,
    errors: List[str],
) -> None:
    if isinstance(law_revision_facts, dict):
        if not _is_law_revision_facts(law_revision_facts):
            errors.append(f"index {index}: lawRevisionFacts must be a valid object")
        return
    if isinstance(law_revision_facts, list):
        if choice_count and len(law_revision_facts) != choice_count:
            errors.append(
                "index {}: lawRevisionFacts length mismatch (source={} patch={})".format(
                    index, choice_count, len(law_revision_facts)
                )
            )
        for choice_index, facts in enumerate(law_revision_facts):
            if not isinstance(facts, dict):
                errors.append(
                    f"index {index}: lawRevisionFacts[{choice_index}] must be object"
                )
            elif not _is_law_revision_facts(facts):
                errors.append(
                    f"index {index}: lawRevisionFacts[{choice_index}] must be a valid object"
                )
        return
    errors.append(f"index {index}: lawRevisionFacts must be object or list[object]")


def compare_entries(
    source_questions: List[Dict[str, Any]],
    patch_entries: List[Dict[str, Any]],
    *,
    require_law_grounded_flag: bool = False,
    require_is_law_related: bool = False,
    require_law_revision_facts: bool = False,
    require_law_evidence_utilization: bool = False,
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if len(source_questions) != len(patch_entries):
        errors.append(
            f"count mismatch: source={len(source_questions)} patch={len(patch_entries)}"
        )

    source_ids = [get_question_identity(q) for q in source_questions]
    patch_ids = [q.get("original_question_id") for q in patch_entries]
    missing_ids = sorted({sid for sid in source_ids if sid} - {pid for pid in patch_ids if pid})
    extra_ids = sorted({pid for pid in patch_ids if pid} - {sid for sid in source_ids if sid})
    if missing_ids:
        errors.append(f"missing original_question_id: {missing_ids}")
    if extra_ids:
        errors.append(f"extra original_question_id: {extra_ids}")

    for idx, (src, patch) in enumerate(zip(source_questions, patch_entries), start=1):
        missing_fields = [k for k in REQUIRED_FIELDS if k not in patch]
        if missing_fields:
            errors.append(f"index {idx}: missing fields {missing_fields}")
            continue

        source_question_id = get_question_identity(src)
        if patch.get("original_question_id") != source_question_id:
            errors.append(
                "index {}: original_question_id mismatch (source={} patch={})".format(
                    idx, source_question_id, patch.get("original_question_id")
                )
            )

        if patch.get("question_url") != src.get("question_url"):
            errors.append(
                "index {}: question_url mismatch (source={} patch={})".format(
                    idx, src.get("question_url"), patch.get("question_url")
                )
            )

        explanations = patch.get("explanationText")
        choices = src.get("choiceTextList") or []
        if not isinstance(explanations, list):
            errors.append(f"index {idx}: explanationText must be a list")
        else:
            source_question_type = src.get("questionType")
            if (
                isinstance(choices, list)
                and len(choices) == 0
                and source_question_type in {"fill_in_blank", "free_text"}
            ):
                if not explanations or any(
                    not isinstance(explanation, str) or not explanation.strip()
                    for explanation in explanations
                ):
                    errors.append(
                        f"index {idx}: fill_in_blank explanationText must be non-empty list[str]"
                    )
            elif isinstance(choices, list) and len(explanations) != len(choices):
                errors.append(
                    "index {}: explanationText length mismatch "
                    "(source={} patch={})".format(idx, len(choices), len(explanations))
                )

        suggested_questions = patch.get("suggestedQuestions")
        if not isinstance(suggested_questions, list) or any(
            not isinstance(question, str) or not question.strip()
            for question in suggested_questions
        ):
            errors.append(f"index {idx}: suggestedQuestions must be non-empty list[str]")

        validate_suggested_question_details(
            suggested_questions=suggested_questions,
            suggested_question_details=patch.get("suggestedQuestionDetails"),
            index=idx,
            errors=errors,
        )

        has_law_references = has_non_empty_law_references(patch.get("lawReferences"))
        if "lawReferences" in patch:
            validate_law_references_shape(
                law_references=patch.get("lawReferences"),
                choice_count=len(choices) if isinstance(choices, list) else 0,
                index=idx,
                errors=errors,
            )
        if "lawRevisionFacts" in patch:
            validate_law_revision_facts_shape(
                law_revision_facts=patch.get("lawRevisionFacts"),
                choice_count=len(choices) if isinstance(choices, list) else 0,
                index=idx,
                errors=errors,
            )

        is_law_related: bool | None = None
        if require_is_law_related and "isLawRelated" not in patch:
            errors.append(f"index {idx}: missing isLawRelated")
        elif "isLawRelated" in patch:
            value = patch.get("isLawRelated")
            if not isinstance(value, bool):
                errors.append(f"index {idx}: isLawRelated must be bool when present")
            else:
                is_law_related = value

        if require_law_grounded_flag and "lawGroundedExplanationNotNeeded" not in patch:
            errors.append(
                f"index {idx}: missing lawGroundedExplanationNotNeeded"
            )
        elif "lawGroundedExplanationNotNeeded" in patch:
            flag = patch.get("lawGroundedExplanationNotNeeded")
            if not isinstance(flag, bool):
                errors.append(
                    f"index {idx}: lawGroundedExplanationNotNeeded must be bool when present"
                )
            elif flag and has_law_references:
                errors.append(
                    f"index {idx}: lawGroundedExplanationNotNeeded cannot be true when lawReferences is non-empty"
                )
            elif is_law_related is not None and flag == is_law_related:
                errors.append(
                    f"index {idx}: lawGroundedExplanationNotNeeded must be the inverse of isLawRelated"
                )

        if is_law_related is False and has_law_references:
            errors.append(
                f"index {idx}: isLawRelated cannot be false when lawReferences is non-empty"
            )
        if (
            require_law_revision_facts
            and is_law_related is True
            and "lawRevisionFacts" not in patch
        ):
            errors.append(
                f"index {idx}: missing lawRevisionFacts for law-related question"
            )
        if require_law_evidence_utilization:
            validate_law_evidence_utilization(
                patch=patch,
                index=idx,
                has_law_references=has_law_references,
                errors=errors,
            )

    if len(set(patch_ids)) != len(patch_ids):
        warnings.append("duplicate original_question_id detected in patch")

    return errors, warnings


def check_pair(
    source_path: Path,
    patch_path: Path,
    *,
    require_law_grounded_flag: bool = False,
    require_is_law_related: bool = False,
    require_law_revision_facts: bool = False,
    require_law_evidence_utilization: bool = False,
) -> int:
    if not source_path.exists():
        print(f"[ERROR] source not found: {source_path}")
        return 2
    if not patch_path.exists():
        print(f"[ERROR] patch not found: {patch_path}")
        return 2

    source_data = load_json(source_path)
    patch_data = load_json(patch_path)

    source_questions = get_source_questions(source_data)
    patch_entries = get_patch_entries(patch_data)

    errors, warnings = compare_entries(
        source_questions,
        patch_entries,
        require_law_grounded_flag=require_law_grounded_flag,
        require_is_law_related=require_is_law_related,
        require_law_revision_facts=require_law_revision_facts,
        require_law_evidence_utilization=require_law_evidence_utilization,
    )
    for warn in warnings:
        print(f"[WARN] {warn}")
    if errors:
        for err in errors:
            print(f"[ERROR] {err}")
        return 1

    print("[OK] coverage check passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate explanationText patch coverage and format."
    )
    parser.add_argument("--source", required=True, help="Path to source question_*.json")
    parser.add_argument(
        "--patch",
        required=True,
        help="Path to *_explanationText_added_YYYYMMDD_HHMM.json (旧形式 *_explanationText_added.json も可)",
    )
    parser.add_argument(
        "--require-law-grounded-flag",
        action="store_true",
        help="Require lawGroundedExplanationNotNeeded on every patch entry.",
    )
    parser.add_argument(
        "--require-is-law-related",
        action="store_true",
        help="Require isLawRelated on every patch entry.",
    )
    parser.add_argument(
        "--require-law-revision-facts",
        action="store_true",
        help="Require lawRevisionFacts when isLawRelated=true.",
    )
    parser.add_argument(
        "--require-law-evidence-utilization",
        action="store_true",
        help="Require law-related explanationText/suggestedQuestions/suggestedQuestionDetails to reflect existing law evidence.",
    )
    args = parser.parse_args()
    return check_pair(
        Path(args.source),
        Path(args.patch),
        require_law_grounded_flag=args.require_law_grounded_flag,
        require_is_law_related=args.require_is_law_related,
        require_law_revision_facts=args.require_law_revision_facts,
        require_law_evidence_utilization=args.require_law_evidence_utilization,
    )


if __name__ == "__main__":
    raise SystemExit(main())
